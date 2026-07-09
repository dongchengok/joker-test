"""LLMExplorer —— LLM 驱动的界面探索器（要求2：通过 LLM 充分探索）。

与 UIExplorer（纯 OCR DFS）互补：UIExplorer 是确定性算法，快但不懂语义；
LLMExplorer 让 LLM 看截图 + OCR 文本，理解界面后决定下一步操作，能发现
DFS 发现不了的语义关联（如"这个按钮会打开设置"）。

设计要点：
- 状态自洽：持有 backend + llm + 探索状态（已访问界面、操作历史）
- LLM 克制：每界面只调 1-2 次 LLM（1 次理解 + 1 次决策下一步）
- 安全：每步截图存档，操作前/后都记录，失败可回放
- 产出 UIMap：与 UIExplorer 同构，可喂给 SmokeTestGenerator

用法::

    explorer = LLMExplorer(backend=backend, llm=llm, max_steps=5)
    uimap = explorer.explore()
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2

from joker_test.explorer.types import Exit, Screen, UIElement, UIMap
from joker_test.trace import trace_event, trace_stage

if TYPE_CHECKING:
    from joker_test.executor.base import ExecutorBackend
    from joker_test.flow.recorder import GlobalRecorder
    from joker_test.llm.base import LLMProvider, Message

logger = logging.getLogger(__name__)


class LLMExplorer:
    """LLM 驱动的界面探索器。

    Args:
        backend: ExecutorBackend（截图 + 操作）
        llm: LLMProvider（多模态，能看图）
        max_steps: 最大探索步数（每步 = 一次界面理解 + 一次操作）
        screenshot_dir: 截图存档目录（调试用，None 不存）
        recorder: GlobalRecorder（可选，传入则探索过程自动录制操作流，供生成 test_case）

    状态自洽：所有探索状态都是自身属性。
    """

    def __init__(
        self,
        backend: ExecutorBackend,
        llm: LLMProvider,
        max_steps: int = 10,
        screenshot_dir: str | Path | None = None,
        recorder: GlobalRecorder | None = None,
        max_stale_steps: int = 3,
    ) -> None:
        self._backend = backend
        self._llm = llm
        self._max_steps = max_steps
        self._recorder = recorder
        self._max_stale_steps = max_stale_steps  # 连续 N 步无新界面就停止
        self._screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        if self._screenshot_dir:
            self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._screens: list[Screen] = []
        self._screen_names: list[str] = []  # 平行于 _screens，存 LLM 起的中文名
        self._tried_actions: set[tuple[str, str]] = set()  # (界面指纹, 操作target) 去重
        self._stale_count = 0  # 连续无新界面的步数

    def explore(self) -> UIMap:
        """LLM 驱动探索，返回 UIMap。带容错兜底保证探索持续深入不中断。

        容错机制：
        - 单步异常隔离：截图/LLM调用/操作失败不中断整个探索，跳过该步继续
        - 操作失败重试：click_text 找不到文字时，让 LLM 重新决策（最多重试 2 次）
        - 界面变化检测：操作后检测界面是否变化，没变则换备选操作
        - 操作去重：已尝试过的 (界面, 操作) 不重复尝试，避免卡死
        - 充分度终止：连续 max_stale_steps 步无新界面才停止（不是死板 max_steps）
        """
        self._backend.connect()
        prev_screen_id: str | None = None
        prev_action_desc: str = ""
        step = 0
        stop_reason = "max_steps"  # 终止原因（trace 用）
        try:
            with trace_stage("explore"):
                while step < self._max_steps and self._stale_count < self._max_stale_steps:
                    logger.info("LLM 探索 step %d/%d（连续无新界面: %d）",
                               step + 1, self._max_steps, self._stale_count)

                    # ---- 单步异常隔离：这步任何异常都不中断整个探索 ----
                    frame = self._retry_screenshot(3)
                    if frame is None:
                        logger.warning("step %d 连续截图失败，跳过", step)
                        step += 1
                        self._stale_count += 1
                        continue

                    self._save_screenshot(frame, step, "before")

                    # LLM 理解界面（异常隔离）
                    try:
                        screen, name = self._understand_screen(frame, step)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("step %d LLM 理解界面失败，跳过: %s", step, e)
                        step += 1
                        self._stale_count += 1
                        continue

                    # 判断是否新界面（指纹去重）
                    is_new_screen = screen.fingerprint not in {s.fingerprint for s in self._screens}
                    if is_new_screen:
                        self._stale_count = 0  # 发现新界面，重置计数
                        logger.info("step %d 发现新界面: %s", step, name)
                    else:
                        self._stale_count += 1
                        logger.info("step %d 界面未变化: %s（连续 %d）",
                                   step, name, self._stale_count)

                    # trace：记录每步探索决策（界面名/是否新界面/无进展计数）
                    trace_event("explore_step", {
                        "step": step, "screen": name,
                        "is_new": is_new_screen, "stale_count": self._stale_count,
                    })

                    # 记录 exit（连接上界面 → 当前界面）
                    if prev_screen_id and step > 0:
                        self._add_exit(prev_screen_id, screen.id, prev_action_desc)

                    if is_new_screen or not self._screens:
                        self._screens.append(screen)
                        self._screen_names.append(name)

                    ocr_before = list(self._backend.state.texts)

                    # LLM 决策下一步（带重试：操作失败或无变化时重新决策）
                    action_executed = False
                    for attempt in range(3):  # 最多尝试 3 次（1 次主决策 + 2 次重试）
                        if step >= self._max_steps - 1:
                            break
                        try:
                            action = self._decide_next_action(name, step, attempt)
                        except Exception as e:  # noqa: BLE001
                            logger.warning("step %d LLM 决策失败(attempt %d): %s", step, attempt, e)
                            continue

                        if action is None:
                            logger.info("step %d LLM 无法决策，重试", step)
                            continue
                        if action.get("action") == "stop":
                            # LLM 说停，但如果有未探索的元素，尝试继续
                            if self._has_unexplored_elements(screen):
                                logger.info("step %d LLM 想停但有未探索元素，继续", step)
                                action = self._pick_unexplored_action(screen)
                                if action is None:
                                    break
                            else:
                                logger.info("step %d LLM 决定停止探索", step)
                                stop_reason = "llm_stop"
                                break

                        # 操作去重检查（click_coord 用坐标做 key，不用 target）
                        act_type = action.get("action", "")
                        if act_type == "click_coord":
                            target = f"coord({action.get('x', 0):.3f},{action.get('y', 0):.3f})"
                        else:
                            target = action.get("target", "")
                        action_key = (screen.fingerprint, target)
                        if action_key in self._tried_actions and attempt < 2:
                            logger.info("step %d 操作已尝试过(%s)，换一个", step, target)
                            continue
                        self._tried_actions.add(action_key)

                        # 执行操作
                        shot_act_before = self._save_screenshot(frame, step, f"act{attempt}_pre")
                        success = self._perform_action(action)

                        if not success:
                            logger.warning("step %d 操作失败(%s)，重试决策", step, target)
                            # trace：操作失败（重试决策用）
                            trace_event("action_result", {
                                "step": step, "action": act_type,
                                "target": target, "success": False, "attempt": attempt,
                            })
                            # 操作失败，截个新图让 LLM 重新看
                            try:
                                frame = self._backend.screenshot()
                            except Exception:  # noqa: BLE001
                                pass
                            continue  # 重试

                        # 等界面切换
                        import time  # noqa: PLC0415

                        time.sleep(1.5)

                        # 检测界面是否变化（截图失败/空帧时重试，最多 3 次）
                        frame_after = self._retry_screenshot(3)
                        if frame_after is None:
                            logger.warning("step %d 操作后连续截图失败，用旧帧兜底", step)
                            frame_after = frame

                        changed = self._detect_screen_change(frame, frame_after)
                        if not changed and attempt < 2:
                            logger.info("step %d 操作后界面未变化，重试换操作", step)
                            frame = frame_after  # 更新帧给重试用
                            continue

                        # 操作成功 + 录制
                        action_executed = True
                        shot_after = self._save_screenshot(frame_after, step, f"act{attempt}_post")
                        ocr_after = list(self._backend.state.texts)

                        # trace：操作成功（含 action/坐标/成功/界面变化 ratio/重试次数）
                        ratio = None
                        try:
                            from joker_test.executor.coords import pixel_diff_ratio  # noqa: PLC0415

                            ratio = round(pixel_diff_ratio(frame, frame_after), 4)
                        except Exception:  # noqa: BLE001
                            pass
                        trace_event("action_result", {
                            "step": step, "action": act_type,
                            "target": target, "success": True,
                            "pixel_diff_ratio": ratio, "attempt": attempt,
                        })

                        if self._recorder is not None:
                            self._record_action(action, ocr_before, ocr_after,
                                                shot_act_before, shot_after, step)

                        prev_screen_id = screen.id
                        prev_action_desc = action.get("description", "")
                        break  # 成功了，跳出重试循环

                    if not action_executed:
                        logger.info("step %d 本轮无成功操作", step)
                        stop_reason = "no_action"

                    step += 1

                # 循环退出后判断终止原因
                if self._stale_count >= self._max_stale_steps:
                    stop_reason = "stale_limit"
        finally:
            self._backend.close()

        logger.info("探索结束：共 %d 步，%d 个界面", step, len(self._screens))

        # trace：探索终止（原因/步数/界面数/界面名列表）
        trace_event("explore_end", {
            "reason": stop_reason, "steps": step,
            "screens": len(self._screens), "screen_names": list(self._screen_names),
        })

        return UIMap(
            screens=self._screens,
            root_screen_id=self._screens[0].id if self._screens else "",
            explored_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            backend_info={
                "type": type(self._backend).__name__,
                "explorer": "LLMExplorer",
                "screen_names": self._screen_names,
            },
        )

    def _understand_screen(self, frame: Any, step: int) -> tuple[Screen, str]:
        """LLM 看图 + OCR 理解当前界面，返回 (Screen, 中文名)。"""
        ocr_texts = self._backend.state.texts

        img_b64 = self._frame_to_b64(frame)
        prompt = (
            "你在探索一个游戏的界面。请**仔细看截图**，识别：\n"
            "1. 这是什么界面（起个简短中文名，如'主菜单'、'角色选择'、'地牢内'）\n"
            "2. 界面上有哪些**游戏内的**可交互元素（按钮/菜单项/选项/图标）\n\n"
            "**重要过滤规则**（必须遵守）：\n"
            "- 只列**游戏内容**元素，忽略 Windows 窗口装饰（最小化/最大化/关闭按钮、标题栏的 — □ ×）\n"
            "- 元素文字必须从截图中**实际看到**，不要凭空想象\n"
            "- 如果元素是图标没有文字，用简短描述（如'战士图标'、'红色按钮'）\n\n"
            "**bbox 坐标（必须提供）**：\n"
            "- 每个元素都要给出 bbox（归一化坐标 [x, y, w, h]，全部 ∈ [0,1]）\n"
            "- (0,0)=截图左上角，(1,1)=右下角。x,y 是元素左上角，w,h 是宽高\n"
            "- 根据元素在截图中**实际占据的区域**估算，尽量精确覆盖整个元素\n\n"
            f"OCR 已识别的文本（仅供辅助参考，可能含窗口装饰杂质，可能不全）：{ocr_texts[:12]}\n\n"
            "请用 JSON 回答："
            '{"screen_name": "...", "elements": [{"text": "...", "type": "button", '
            '"bbox": [0.1, 0.3, 0.15, 0.2]}, ...]}'
        )
        msg = self._llm.simple_converse(prompt, [], images=[img_b64])
        reply = _extract_text(msg)
        logger.info("step %d LLM 理解界面: %s", step, reply[:150])

        name, element_texts = self._parse_screen_reply(reply)
        # 程序级兜底：过滤已知窗口装饰元素（防止 LLM 漏过滤）
        element_texts = _filter_window_decoration(element_texts)

        screen_id = f"{name}_{step}"
        # 解析 elements 的 bbox（LLM 返回的，用于精确裁剪模板）
        element_bboxes = self._parse_element_bboxes(reply)
        elements = []
        for t in element_texts:
            if not t:
                continue
            # 从 LLM 返回的 bbox 映射里找对应的，找不到用 0,0,0,0
            bbox = element_bboxes.get(t, (0.0, 0.0, 0.0, 0.0))
            elements.append(UIElement(type="button", text=t, bbox=bbox))
        # 指纹 = 排序后的元素文本 hash（与 detection.compute_fingerprint 同构）
        fingerprint = hashlib.md5(
            "|".join(sorted(element_texts)).encode("utf-8")
        ).hexdigest()  # noqa: S324

        screen = Screen(
            id=screen_id,
            elements=elements,
            exits=[],
            fingerprint=fingerprint,
            screenshot_ref=(
                str(self._screenshot_dir / f"step{step:02d}_before.png")
                if self._screenshot_dir
                else None
            ),
        )
        return screen, name

    def _decide_next_action(
        self, current_name: str, step: int, attempt: int = 0
    ) -> dict[str, Any] | None:
        """LLM 决定下一步操作。attempt>0 时告知 LLM 之前的操作失败了需换一个。"""
        img_b64 = self._frame_to_b64(self._backend.screenshot())
        retry_hint = ""
        if attempt > 0:
            tried = [t for fp, t in self._tried_actions]  # noqa: B007
            retry_hint = (
                f"\n\n**注意：之前尝试的操作失败了或界面没变化（已试过: {tried[-3:]}）。"
                "请换一个不同的操作。如果界面上没有可点击的文字按钮，"
                "请用 click_coord 直接给出要点击的归一化坐标。**"
            )
        prompt = (
            f"你正在探索游戏界面（当前：{current_name}，step {step + 1}）。\n"
            f"已探索的界面：{self._screen_names}\n\n"
            "为了推进探索（发现新界面），下一步应该做什么操作？\n"
            "选择一个最可能切换到新界面的操作。\n\n"
            "请用 JSON 回答（只能选一个操作）：\n"
            '- 点击文字按钮：{"action": "click_text", "target": "按钮文字", '
            '"description": "点击XXX"}\n'
            '- 点击图标/无文字区域（给出归一化坐标x,y∈[0,1]）：'
            '{"action": "click_coord", "x": 0.15, "y": 0.35, '
            '"description": "点击左上角第一个角色图标"}\n'
            '- 按键：{"action": "press_key", "target": "escape", '
            '"description": "按ESC返回"}\n'
            '- 停止：{"action": "stop", "target": "", "description": "探索完成"}\n\n'
            "**选择策略**：\n"
            "- 如果界面上有文字按钮（如'进入'、'开始'、'确定'），优先用 click_text\n"
            "- 如果界面上是纯图标（角色头像、图标按钮、无文字），用 click_coord 给出坐标\n"
            "- click_coord 的 x,y 是归一化坐标[0,1]，(0,0)=左上角，(1,1)=右下角\n"
            + retry_hint
        )
        msg = self._llm.simple_converse(prompt, [], images=[img_b64])
        reply = _extract_text(msg)
        logger.info("step %d LLM 决策(attempt %d): %s", step, attempt, reply[:150])

        return self._parse_action_reply(reply)

    def _perform_action(self, action: dict[str, Any]) -> bool:
        """执行 LLM 决定的操作。返回是否成功（操作有效且执行了）。

        click_coord 成功后不裁模板——纯图标界面的截图含动态内容（等级数字/血条/时间），
        做模板第二次跑必然匹配失败。固定窗口+分辨率下坐标稳定，直接用 click_coord。
        """
        act = action.get("action", "")
        target = action.get("target", "")
        if act == "click_text" and target:
            logger.info("执行 click_text(%s)", target)
            return bool(self._backend.click_text(target))
        elif act == "click_coord":
            x = action.get("x")
            y = action.get("y")
            if x is None or y is None or not (0.0 <= x <= 1.0) or not (0.0 <= y <= 1.0):
                logger.warning("click_coord 坐标非法: (%s, %s)", x, y)
                return False
            logger.info("执行 click_coord(%.3f, %.3f) %s", x, y, action.get("description", ""))
            self._backend.click(x, y)
            return True
        elif act == "press_key" and target:
            logger.info("执行 press_key(%s)", target)
            self._backend.press_key(target)
            return True
        elif act == "stop":
            return False
        else:
            logger.warning("未知操作，跳过: %s", action)
            return False

    def _detect_screen_change(self, frame_before: Any, frame_after: Any) -> bool:
        """检测两帧之间界面是否发生了变化（pixel_diff_ratio > 0.005）。"""
        try:
            from joker_test.executor.coords import pixel_diff_ratio  # noqa: PLC0415

            ratio = pixel_diff_ratio(frame_before, frame_after)
            changed = ratio > 0.005
            logger.info("界面变化检测: ratio=%.4f changed=%s", ratio, changed)
            return changed
        except Exception as e:  # noqa: BLE001
            logger.warning("界面变化检测失败，保守认为变了: %s", e)
            return True  # 检测失败时保守认为变了，避免误判卡死

    def _has_unexplored_elements(self, screen: Screen) -> bool:
        """检查界面是否有还没尝试过的可交互元素。"""
        for elem in screen.elements:
            if elem.text and (screen.fingerprint, elem.text) not in self._tried_actions:
                return True
        return False

    def _pick_unexplored_action(self, screen: Screen) -> dict[str, Any] | None:
        """从界面元素里选一个还没试过的，生成 click_text 操作。"""
        for elem in screen.elements:
            if elem.text and (screen.fingerprint, elem.text) not in self._tried_actions:
                self._tried_actions.add((screen.fingerprint, elem.text))
                return {
                    "action": "click_text",
                    "target": elem.text,
                    "description": f"尝试点击未探索元素: {elem.text}",
                }
        return None

    def _record_action(
        self,
        action: dict[str, Any],
        ocr_before: list[str],
        ocr_after: list[str],
        shot_before: str | None,
        shot_after: str | None,
        step: int,
    ) -> None:
        """把探索的一步操作录进 GlobalRecorder（操作流→test_case 闭环用）。"""
        if self._recorder is None:
            return
        act = action.get("action", "")
        target = action.get("target", "")
        desc = action.get("description", f"step{step} {act}")
        if act == "click_text":
            self._recorder.record_action(
                "click_text", text=target, note=desc,
                ocr_before=ocr_before, ocr_after=ocr_after,
                screenshot_before=shot_before, screenshot_after=shot_after,
            )
        elif act == "click_coord":
            self._recorder.record_action(
                "click", x=action.get("x"), y=action.get("y"), note=desc,
                ocr_before=ocr_before, ocr_after=ocr_after,
                screenshot_before=shot_before, screenshot_after=shot_after,
            )
        elif act == "press_key":
            self._recorder.record_action(
                "press_key", key=target, note=desc,
                ocr_before=ocr_before, ocr_after=ocr_after,
                screenshot_before=shot_before, screenshot_after=shot_after,
            )

    def _add_exit(self, from_id: str, to_id: str, via: str) -> None:
        """给上一个界面加 exit（连接到当前界面）。"""
        for s in self._screens:
            if s.id == from_id:
                s.exits.append(
                    Exit(
                        from_screen=from_id,
                        element_text=via,
                        action="click_text",
                        to_screen=to_id,
                        evidence={},
                    )
                )
                break

    def _save_screenshot(self, frame: Any, step: int, tag: str) -> str | None:
        """保存截图，返回路径（screenshot_dir 未设时或帧为空返回 None）。"""
        if self._screenshot_dir and frame is not None and getattr(frame, "size", 0) > 0:
            path = self._screenshot_dir / f"step{step:02d}_{tag}.png"
            try:
                cv2.imwrite(str(path), frame)
                return str(path)
            except cv2.error:
                return None
        return None

    def _retry_screenshot(self, max_retries: int = 3) -> Any:
        """重试截图（airtest 偶发返回空帧，窗口切换瞬间会失败）。返回帧或 None。"""
        import time  # noqa: PLC0415

        for i in range(max_retries):
            try:
                frame = self._backend.screenshot()
                if frame is not None and getattr(frame, "size", 0) > 0:
                    return frame
            except Exception as e:  # noqa: BLE001
                logger.warning("截图失败(attempt %d): %s", i + 1, e)
            time.sleep(0.5)
        return None

    @staticmethod
    def _frame_to_b64(frame: Any) -> str:
        success, buf = cv2.imencode(".png", frame)
        if not success:
            return ""
        return base64.b64encode(buf.tobytes()).decode("utf-8")

    @staticmethod
    def _parse_screen_reply(reply: str) -> tuple[str, list[str]]:
        """从容错文本解析 screen_name + 元素文本列表。

        处理 LLM 回复中常见的格式：可能含 markdown 代码块、嵌套 JSON、多余文本。
        策略：先抽 ```json 代码块，再尝试整体 json.loads，最后回退到正则提 screen_name。
        """
        import json  # noqa: PLC0415
        import re  # noqa: PLC0415

        # 1. 尝试抽 ```json ... ``` 代码块
        code_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", reply, re.DOTALL)
        candidate = code_match.group(1) if code_match else reply

        # 2. 尝试整体解析（找第一个 { 到匹配的 }）
        # 用栈匹配找完整 JSON 对象（处理嵌套 elements 数组）
        start = candidate.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(candidate)):
                if candidate[i] == "{":
                    depth += 1
                elif candidate[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            data = json.loads(candidate[start : i + 1])
                            elements = data.get("elements", [])
                            texts = [
                                e.get("text", "")
                                for e in elements
                                if isinstance(e, dict)
                            ]
                            return data.get("screen_name", "未知"), [
                                t for t in texts if t
                            ]
                        except json.JSONDecodeError:
                            break

        # 3. 回退：正则提 screen_name
        name_match = re.search(r'"screen_name"\s*:\s*"([^"]+)"', reply)
        name = name_match.group(1) if name_match else "未知"
        return name, []

    @staticmethod
    def _parse_element_bboxes(reply: str) -> dict[str, tuple[float, float, float, float]]:
        """从 LLM 回复解析元素的 text → bbox 映射（用于精确裁剪模板）。

        bbox 格式：[x, y, w, h]（归一化 [0,1]）。
        解析失败返回空 dict（回退到 0,0,0,0）。
        """
        import json  # noqa: PLC0415
        import re  # noqa: PLC0415

        code_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", reply, re.DOTALL)
        candidate = code_match.group(1) if code_match else reply
        start = candidate.find("{")
        if start < 0:
            return {}
        depth = 0
        for i in range(start, len(candidate)):
            if candidate[i] == "{":
                depth += 1
            elif candidate[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(candidate[start : i + 1])
                        result: dict[str, tuple[float, float, float, float]] = {}
                        for elem in data.get("elements", []):
                            if not isinstance(elem, dict):
                                continue
                            text = elem.get("text", "")
                            bbox = elem.get("bbox")
                            if text and isinstance(bbox, list) and len(bbox) == 4:
                                try:
                                    result[text] = (
                                        float(bbox[0]), float(bbox[1]),
                                        float(bbox[2]), float(bbox[3]),
                                    )
                                except (ValueError, TypeError):
                                    pass
                        return result
                    except json.JSONDecodeError:
                        break
        return {}

    @staticmethod
    def _parse_action_reply(reply: str) -> dict[str, Any] | None:
        """从容错文本解析 action。"""
        import json  # noqa: PLC0415
        import re  # noqa: PLC0415

        m = re.search(r'\{[^{}]*"action"[^{}]*\}', reply, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return None


def _extract_text(msg: Message) -> str:
    """从 Message 提取文本。"""
    parts: list[str] = []
    for block in msg.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


# Windows 窗口装饰元素的文本特征（程序级过滤兜底，防止 LLM 漏过滤）
# 来源：SPD 实测 OCR 把窗口标题栏读成这些（G9 footgun）
_WINDOW_DECORATION_KEYWORDS = {
    "最小化", "最大化", "关闭",
    "—", "□", "×", "口",
    "minimize", "maximize", "close",
}


def _filter_window_decoration(texts: list[str]) -> list[str]:
    """过滤窗口装饰元素（最小化/最大化/关闭/—/□/× 等）。

    LLM 应该已经在 prompt 引导下过滤了，这是程序级兜底。
    """
    return [
        t for t in texts
        if t.strip() and t.strip().lower() not in _WINDOW_DECORATION_KEYWORDS
    ]


__all__ = ["LLMExplorer"]
