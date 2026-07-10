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
    ) -> None:
        self._backend = backend
        self._llm = llm
        self._strategy = strategy
        self._max_steps = max_steps
        self._recorder = recorder
        self._screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        if self._screenshot_dir:
            self._screenshot_dir.mkdir(parents=True, exist_ok=True)

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
        screenshot = self._retry_screenshot()
        if screenshot is None:
            return

        perception = self._perceive(screenshot)

        try:
            decision = self._strategy.decide(screenshot, perception, ctx)
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("策略 decide 失败：%s", e)
            return

        self._trace_think(decision)

        if decision.stop:
            return

        result = self._perform_action(decision)
        self._strategy.on_action_executed(decision, result)

        if self._recorder is not None and result.success:
            self._record(decision, result)

    def _perceive(self, screenshot: Any) -> Any:
        """感知当前截图（OCR + 图像匹配）。降级为空结果。"""
        try:
            from joker_test.perception.base import PerceptionEngine  # noqa: PLC0415

            engine = PerceptionEngine()
            return engine.perceive(screenshot)
        except Exception:  # noqa: BLE001
            return None

    def _perform_action(self, decision: StepDecision) -> ActionResult:
        """执行动作 + 等待界面稳定 + 检测变化。"""
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
        """分发动作到 backend。"""
        act = decision.action
        desc = decision.description
        if act == "click_text":
            self._backend.click_text(desc)
        elif act == "click_coord":
            parts = desc.split(",")
            self._backend.click(float(parts[0]), float(parts[1]))
        elif act == "press_key":
            self._backend.press_key(desc)
        elif act == "type_text":
            self._backend.type_text(desc)
        elif act == "swipe":
            parts = desc.split(",")
            self._backend.swipe(
                float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
            )
        elif act == "scroll":
            if "down" in desc:
                self._backend.swipe(0.5, 0.7, 0.5, 0.3)
            else:
                self._backend.swipe(0.5, 0.3, 0.5, 0.7)
        elif act == "long_press":
            parts = desc.split(",")
            self._backend.long_press(float(parts[0]), float(parts[1]))
        elif act == "back":
            self._backend.press_key("escape")

    def _record(self, decision: StepDecision, result: ActionResult) -> None:
        """录制操作到 GlobalRecorder。"""
        if self._recorder is None:
            return
        act = decision.action
        note = decision.think
        if act == "click_text":
            self._recorder.record_action("click_text", text=decision.description, note=note)
        elif act == "click_coord":
            parts = decision.description.split(",")
            self._recorder.record_action(
                "click", x=float(parts[0]), y=float(parts[1]), note=note
            )
        elif act == "press_key":
            self._recorder.record_action("press_key", key=decision.description, note=note)
        elif act == "type_text":
            self._recorder.record_action("type_text", text=decision.description, note=note)
        elif act in ("swipe", "long_press"):
            parts = decision.description.split(",")
            self._recorder.record_action(
                "click", x=float(parts[0]), y=float(parts[1]), note=f"{act}: {note}"
            )

    def _retry_screenshot(self, max_retries: int = 3) -> Any:
        """重试截图。"""
        for _ in range(max_retries):
            try:
                return self._backend.screenshot()
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        return None

    def _detect_change(self, before: Any, after: Any) -> tuple[bool, float]:
        """检测界面变化。"""
        try:
            from joker_test.explorer.detection import has_screen_changed  # noqa: PLC0415

            return has_screen_changed(before, after, _SCREEN_CHANGE_THRESHOLD)
        except Exception:  # noqa: BLE001
            return True, 0.0

    def _trace_think(self, decision: StepDecision) -> None:
        """记录 ReAct 推理到 trace。"""
        try:
            from joker_test.trace import trace_event  # noqa: PLC0415

            trace_event("explore_think", {"think": decision.think, "action": decision.action})
        except Exception:  # noqa: BLE001
            pass
