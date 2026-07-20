"""LLMExplorer：基础设施外壳。

持有 ExploreStrategy 实例，公共逻辑：截图 / perception / 动作执行 / 录制 / trace / wait_until。
策略只负责决策和状态管理。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from joker_test.explorer.strategy import (
    ActionResult,
    ExploreContext,
    StepDecision,
)
from joker_test.explorer.types import StateMap
from joker_test.flow.recorder import GlobalRecorder
from joker_test.llm.base import LLMProvider

if TYPE_CHECKING:
    from joker_test.executor.base import ExecutorBackend

_LOGGER = logging.getLogger(__name__)

_SCREEN_CHANGE_THRESHOLD = 0.005
_WAIT_AFTER_ACTION = 1.0


class LLMExplorer:
    """LLM 探索器外壳。策略可替换。"""

    def __init__(
        self,
        backend: ExecutorBackend,
        llm: LLMProvider,
        strategy: Any,
        max_steps: int = 30,
        screenshot_dir: str | Path | None = None,
        recorder: GlobalRecorder | None = None,
        plugin_manager: Any = None,
    ) -> None:
        self._backend = backend
        self._llm = llm
        self._strategy = strategy
        self._max_steps = max_steps
        self._recorder = recorder
        self._plugin_manager = plugin_manager
        self._step = 0
        self._screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        if self._screenshot_dir:
            self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._last_action_key: str = ""  # 重复动作检测
        self._repeat_count: int = 0

    def explore(self) -> StateMap:
        """主循环：截图→感知→决策→执行→录制→更新状态。"""
        self._backend.connect()
        try:
            ctx = ExploreContext(
                step=0,
                max_steps=self._max_steps,
                intent="",
                backend=self._backend,
                llm=self._llm,
                recorder=self._recorder,
            )
            while not self._strategy.should_stop() and ctx.step < ctx.max_steps:
                self._run_step(ctx)
                ctx.step += 1
        finally:
            self._backend.close()
        return self._strategy.get_state_map()

    def _run_step(self, ctx: ExploreContext) -> None:
        """单步：截图→感知→决策→执行→录制。"""
        self._step = ctx.step
        screenshot = self._retry_screenshot()
        if screenshot is None:
            return

        # 截图落盘（trace 诊断必需：LLM 当时到底看到了什么界面）
        screenshot_path = self._save_screenshot(screenshot, ctx.step)

        perception = self._perceive(screenshot)
        # 截图尺寸（OCR 文字坐标已由 OCRPlugin 注入到 LLM prompt，trace 不重复存）
        try:
            sh, sw = screenshot.shape[:2]
        except Exception:  # noqa: BLE001
            sw, sh = 0, 0
        self._trace("perceive", {
            "screenshot_size": [sw, sh],
            "screenshot_path": str(screenshot_path) if screenshot_path else "",
        })

        try:
            decision = self._strategy.decide(screenshot, perception, ctx)
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("策略 decide 失败：%s", e)
            return

        self._trace_decision(decision)

        if decision.stop:
            return

        # 重复动作检测：连续相同的坐标 click 标记警告
        action_key = self._action_key(decision)
        if action_key and action_key == self._last_action_key:
            self._repeat_count += 1
            if self._repeat_count >= 2:
                self._trace("repeat_warning", {
                    "count": self._repeat_count + 1,
                    "action_key": action_key,
                    "hint": "连续重复动作，可能陷入循环",
                })
        else:
            self._repeat_count = 0
        self._last_action_key = action_key

        result = self._perform_action(decision)
        self._trace("action_result", {
            "success": result.success,
            "screen_changed": result.screen_changed,
            "pixel_diff_ratio": round(result.pixel_diff_ratio, 4),
            "error": result.error,
            "action": decision.action,
            "target": decision.target,
            "x": decision.x,
            "y": decision.y,
            "effective": result.success and not result.screen_changed,
        })

        # Plugin validate: keep the call for future plugins, but do NOT inject
        # feedback into prompt (following Open-AutoGLM: LLM reasons on its own)
        if self._plugin_manager is not None:
            self._plugin_manager.validate(decision, result, backend=self._backend)

        self._strategy.on_action_executed(decision, result)

        if self._recorder is not None and result.success:
            self._record(decision, result)

    def _perceive(self, screenshot: Any) -> Any:
        """感知当前截图。优先用 backend.state（已含 OCR + bbox），降级用 PerceptionEngine。"""
        try:
            state = self._backend.state
            texts = list(state.texts)
            if texts:
                from joker_test.perception.base import PerceptionResult  # noqa: PLC0415

                # 提取文字 + 中心坐标
                # 支持两种来源：AirtestBackend._ocr_results / FakeBackend.text_elements
                text_elements: list[dict[str, Any]] = []
                raw = getattr(state, "_ocr_results", None) or getattr(state, "text_elements", None)
                if raw:
                    for r in raw:
                        bbox = r.get("bbox")
                        if bbox:
                            cx = round(bbox.x + bbox.w / 2, 3)
                            cy = round(bbox.y + bbox.h / 2, 3)
                            text_elements.append({"text": r["text"], "x": cx, "y": cy})

                return PerceptionResult(
                    texts=texts, text_elements=text_elements, layer_used="ocr"
                )
        except Exception:  # noqa: BLE001
            pass
        return None

    def _perform_action(self, decision: StepDecision) -> ActionResult:
        """执行动作 + 等待界面稳定 + 检测变化。

        变化检测只做通用的像素 diff（引擎无关，不含任何 OCR/游戏特化知识）。
        语义层的变化判断（"OCR 文本是否变了"等）交给插件的 validate 注入点，
        避免把感知知识硬编码进执行循环（ADR-013）。
        """
        before = self._backend.screenshot()
        success = True
        error: str | None = None

        try:
            self._dispatch_action(decision)
        except Exception as e:  # noqa: BLE001
            success = False
            error = str(e)

        if success:
            self._backend.wait_until(lambda: True, timeout=_WAIT_AFTER_ACTION)

        after = self._backend.screenshot() if success else before
        changed, ratio = self._detect_change(before, after)

        return ActionResult(
            success=success,
            screen_changed=changed,
            pixel_diff_ratio=ratio,
            error=error,
        )

    def _dispatch_action(self, decision: StepDecision) -> None:
        """分发动作到 backend。每个分支都记录 dispatch trace。"""
        act = decision.action
        if act == "click":
            self._dispatch_click(decision)
        elif act == "press_key":
            self._backend.press_key(decision.target)
            self._trace("dispatch", {"action": "press_key", "key": decision.target})
        elif act == "type_text":
            self._backend.type_text(decision.target)
            self._trace("dispatch", {"action": "type_text", "text": decision.target[:50]})
        elif act == "swipe":
            # 支持水平和垂直滑动（target 指示方向）
            direction = (decision.target or "").lower()
            x, y = decision.x, decision.y
            if x is not None and y is not None:
                if "left" in direction:
                    dx, dy = -0.3, 0.0  # 向左拖：x 减小
                elif "right" in direction:
                    dx, dy = 0.3, 0.0   # 向右拖：x 增大
                elif "down" in direction:
                    dx, dy = 0, 0.4    # 向下拖：y 增大
                elif "up" in direction:
                    dx, dy = 0, -0.4   # 向上拖：y 减小
                else:
                    # 方向未指定但给了坐标 → 默认水平拖（滑块最常见的操作）
                    dx, dy = -0.3, 0
                self._backend.swipe(x, y, x + dx, y + dy)
                self._trace("dispatch_swipe", {
                    "from": [round(x, 3), round(y, 3)],
                    "to": [round(x + dx, 3), round(y + dy, 3)],
                    "direction": direction or "left(default)",
                })
            else:
                self._backend.swipe(0.5, 0.7, 0.5, 0.3)
                self._trace("dispatch_swipe", {
                    "from": [0.5, 0.7], "to": [0.5, 0.3], "direction": "fullscreen_up",
                })
        elif act == "scroll":
            direction = decision.target or "down"
            if "down" in direction:
                self._backend.swipe(0.5, 0.7, 0.5, 0.3)
            else:
                self._backend.swipe(0.5, 0.3, 0.5, 0.7)
            self._trace("dispatch", {"action": "scroll", "direction": direction})
        elif act == "long_press":
            if decision.x is not None and decision.y is not None:
                self._backend.long_press(decision.x, decision.y)
                self._trace("dispatch", {
                    "action": "long_press", "x": decision.x, "y": decision.y,
                })
        elif act == "back":
            self._backend.press_key("escape")
            self._trace("dispatch", {"action": "back"})

    def _dispatch_click(self, decision: StepDecision) -> None:
        """统一点击 fallback 链。

        优先级：文本精确匹配 > 坐标兜底。
        1. 有文本 → click_text(text)，失败则坐标兜底
        2. 有坐标+无文本 → 检查不在窗口装饰区 → click(x, y)
        3. 都没有 → 跳过
        """
        x, y, text = decision.x, decision.y, decision.target
        click_path: list[str] = []
        hit: bool = False

        if text:
            click_path.append(f"click_text('{text}')")
            if self._backend.click_text(text):
                click_path.append("命中")
                hit = True
            else:
                click_path.append("未命中→坐标兜底")
                _LOGGER.warning("click_text('%s') 未找到，尝试坐标兜底", text)

        if not hit and x is not None and y is not None:
            click_path.append(f"click({x:.3f}, {y:.3f})")
            if self._is_window_decoration(x, y):
                click_path.append("拒绝(窗口装饰)")
                _LOGGER.warning("拒绝点击窗口装饰区域: (%.2f, %.2f)", x, y)
            else:
                self._backend.click(x, y)
                click_path.append("执行")
                hit = True
        elif not hit and x is None:
            click_path.append("跳过(无坐标无文本)")

        self._trace("dispatch_click", {"path": " → ".join(click_path), "success": hit})

    @staticmethod
    def _is_window_decoration(x: float, y: float) -> bool:
        """检查坐标是否在窗口装饰区域（标题栏/关闭按钮等）。"""
        # 右上角关闭/最大化/最小化区域
        if x > 0.9 and y < 0.1:
            return True
        # 左上角窗口标题栏
        if y < 0.05:
            return True
        return False

    @staticmethod
    def _extract_target_from_desc(desc: str) -> str:
        """从 description 里提取可能的按钮文字。

        LLM 的 description 常含 "点击XXX按钮" 格式，提取 XXX。
        """
        import re  # noqa: PLC0415

        # 匹配 "点击"设置"按钮" / "点击设置按钮" / "点击「设置」"
        patterns = [
            r'点击["""「](.+?)["""」]',
            r'点击(.+?)按钮',
            r'点击(.+?)选项',
        ]
        for pattern in patterns:
            match = re.search(pattern, desc)
            if match:
                return match.group(1).strip()
        return ""

    def _record(self, decision: StepDecision, result: ActionResult) -> None:
        """录制操作到 GlobalRecorder。"""
        if self._recorder is None:
            return
        act = decision.action
        note = decision.think or decision.description
        if act == "click":
            if decision.target:
                self._recorder.record_action(
                    "click_text", text=decision.target, note=note,
                    x=decision.x, y=decision.y,
                )
            elif decision.x is not None and decision.y is not None:
                self._recorder.record_action(
                    "click", x=decision.x, y=decision.y, note=note
                )
        elif act == "press_key":
            self._recorder.record_action("press_key", key=decision.target, note=note)
        elif act == "type_text":
            self._recorder.record_action("type_text", text=decision.target, note=note)
        elif act in ("swipe", "long_press"):
            if decision.x is not None and decision.y is not None:
                self._recorder.record_action(
                    "click", x=decision.x, y=decision.y, note=f"{act}: {note}"
                )

    def _retry_screenshot(self, max_retries: int = 3) -> Any:
        """重试截图。"""
        for _ in range(max_retries):
            try:
                return self._backend.screenshot()
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        return None

    def _save_screenshot(self, screenshot: Any, step: int) -> Path | None:
        """把截图落盘到 screenshot_dir（trace 诊断必需，不报错中断主流程）。

        Args:
            screenshot: BGR ndarray 截图
            step: 当前步数（用于文件名）

        Returns:
            保存路径；screenshot_dir 未配置或保存失败时返回 None
        """
        if self._screenshot_dir is None:
            return None
        try:
            import cv2  # noqa: PLC0415

            path = self._screenshot_dir / f"step_{step:03d}.png"
            cv2.imwrite(str(path), screenshot)
            return path
        except Exception:  # noqa: BLE001
            _LOGGER.warning("截图落盘失败 step=%d", step, exc_info=True)
            return None

    @staticmethod
    def _action_key(decision: StepDecision) -> str:
        """生成动作指纹用于重复检测（坐标量化到 0.05 精度）。"""
        act = decision.action
        if act == "click":
            x = round(decision.x / 0.05) * 0.05 if decision.x is not None else 0
            y = round(decision.y / 0.05) * 0.05 if decision.y is not None else 0
            return f"click@{x:.2f},{y:.2f}"
        if act == "press_key":
            return f"press:{decision.target}"
        if act == "swipe":
            return f"swipe:{decision.target}"
        return ""  # 其他动作不跟踪

    def _detect_change(self, before: Any, after: Any) -> tuple[bool, float]:
        """检测界面像素变化（通用，引擎无关）。"""
        try:
            from joker_test.explorer.detection import has_screen_changed  # noqa: PLC0415

            return has_screen_changed(before, after, _SCREEN_CHANGE_THRESHOLD)
        except Exception:  # noqa: BLE001
            return True, 0.0

    def _trace_decision(self, decision: StepDecision) -> None:
        """记录决策推理到 trace（含动作+目标+坐标+目标进度）。"""
        self._trace("explore_think", {
            "think": decision.think,
            "action": decision.action,
            "target": decision.target,
            "x": decision.x,
            "y": decision.y,
            "goal_progress": decision.goal_progress,
            "goal_completed": decision.goal_completed,
        })

    def _trace(self, event_type: str, data: dict[str, Any]) -> None:
        """记录事件到 trace，自动注入 step（容错）。"""
        try:
            from joker_test.trace import trace_event  # noqa: PLC0415

            data = {"step": self._step, **data}
            trace_event(event_type, data)
        except Exception:  # noqa: BLE001
            pass
