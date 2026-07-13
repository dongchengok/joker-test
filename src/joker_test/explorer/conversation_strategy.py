"""ConversationStrategy：对齐 Open-AutoGLM 的 conversation context 策略。

历史截图剥离（只保留当前步截图）+ assistant 重封装为 <think>/<answer> + system 一次。
作为 ReactStateStrategy 的对比基准。
"""
from __future__ import annotations

import base64
import datetime
import logging
from typing import Any

from joker_test.explorer.strategy import (
    EXPLORE_TOOL_SCHEMA,
    ActionResult,
    ExploreContext,
    StepDecision,
    normalize_action,
    parse_coords,
)
from joker_test.explorer.types import Screen, StateMap
from joker_test.llm.base import LLMProvider, Message

_LOGGER = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你在探索一个游戏界面，目标是完成任务。每步你看到截图和界面元素信息，推理后输出动作。

界面元素格式：文字@(x,y)，x/y 是归一化坐标[0,1]（左0右1，上0下1），表示该文字在屏幕上的中心位置。
例如 "设置@(0.50,0.80)" 表示"设置"文字的中心在屏幕水平中间、垂直80%处。
点击有文字的按钮时，直接用它的坐标作为 click 的 x/y，不需要自己估算。
点击无文字的图标（如齿轮⚙/喇叭🔊）时，根据截图估算坐标。

用 <think>推理</think><answer>{json}</answer> 回答。json 只能是以下动作之一：

1. 点击按钮：{"action":"click","target":"按钮文字","x":0.5,"y":0.5,"description":"..."}
2. 按键：{"action":"press_key","target":"escape","description":"..."}
3. 输入文字：{"action":"type_text","target":"要输入的文字","description":"..."}
4. 滑动：{"action":"swipe","target":"left","x":0.5,"y":0.5,"description":"向左拖动滑块"}
5. 翻页：{"action":"scroll","target":"down","description":"向下翻页"}
6. 长按：{"action":"long_press","target":"图标描述","description":"..."}
7. 返回上级：{"action":"back","description":"返回上级界面"}
8. 停止探索：{"action":"stop","description":"目标完成/无法继续"}

规则：
- action 必须是上面 8 个之一，不要发明其他动作名
- click 的 target 填按钮上的文字；有文字的按钮直接用界面元素给的坐标作为 x/y
- 坐标是归一化 [0,1]（左0右1，上0下1），不要输出绝对像素值
- swipe 的 target 填方向 left/right/up/down，用于拖动滑块或切换页面
- 水平滑块（如音量）用 swipe + left/right，不要用 click 点滑块（点不动）
- 不要点击窗口的关闭(×)/最小化/最大化按钮
- 不要重复点击同一个按钮，界面没变化就换操作
- 绝对不要点击全屏模式
- goal_progress 描述当前进度，goal_completed 为 true 时表示目标完成"""


class ConversationStrategy:
    """conversation context 策略（对齐 Open-AutoGLM）。"""

    def __init__(
        self,
        llm: LLMProvider,
        intent: str,
        max_conversation_tokens: int = 8000,
    ) -> None:
        self._llm = llm
        self._intent = intent
        self._max_tokens = max_conversation_tokens
        self._messages: list[Message] = []
        self._screens: list[Screen] = []
        self._goal_completed: bool = False
        self._initialized = False

    def decide(
        self,
        screenshot: Any,
        perception: Any,
        ctx: ExploreContext,
    ) -> StepDecision:
        """构建 prompt → LLM（tool_use 结构化输出）→ 解析 tool_use block。"""
        if not self._initialized:
            self._messages.append(
                {"role": "system", "content": [{"type": "text", "text": _SYSTEM_PROMPT}]}
            )
            self._initialized = True

        img_b64 = self._encode_image(screenshot)
        step_text = self._build_step_text(perception, ctx)
        images = [img_b64] if img_b64 else None

        from joker_test.llm.base import build_user_message  # noqa: PLC0415

        user_msg = build_user_message(step_text, images)

        try:
            reply = self._llm.create(
                messages=self._messages + [user_msg],
                tools=[EXPLORE_TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": "execute_action"},
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("LLM 决策失败：%s", e)
            return StepDecision(think=f"LLM失败:{e}", action="stop", stop=True)

        # 追加 user 消息到历史（文本，图不保留）
        self._messages.append(
            {"role": "user", "content": [{"type": "text", "text": step_text}]}
        )

        # 从 tool_use block 提取结构化动作
        decision = self._parse_tool_use(reply, screenshot)

        # 追加 assistant 消息到历史
        self._messages.append(
            {"role": "assistant", "content": [{"type": "text", "text": decision.think or decision.action}]}
        )

        if decision.goal_completed:
            self._goal_completed = True

        # 用 perception 的 OCR 文本构建 Screen
        self._maybe_add_screen(perception, ctx)

        return decision

    def _parse_tool_use(self, msg: Any, screenshot: Any = None) -> StepDecision:
        """从 LLM 回复的 tool_use block 提取结构化动作。

        tool_use 是 API 层 constrained decoding 保证的，不需要正则解析。
        """
        for block in msg.get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                inp = block.get("input", {})
                action = normalize_action(inp.get("action", "stop"))
                target = inp.get("target", "")
                x, y = parse_coords(inp, screenshot)
                return StepDecision(
                    think=inp.get("think", ""),
                    action=action,
                    stop=action == "stop",
                    goal_progress=inp.get("goal_progress", ""),
                    goal_completed=inp.get("goal_completed", False),
                    target=target,
                    x=x,
                    y=y,
                    description=target,
                )
        # 无 tool_use block（降级）
        return StepDecision(think="无 tool_use block", action="stop", stop=True)

    def _maybe_add_screen(self, perception: Any, ctx: ExploreContext) -> None:
        """用 OCR 文本构建 Screen，去重后加入 _screens。"""
        import hashlib  # noqa: PLC0415

        texts: list[str] = []
        if perception is not None:
            texts = getattr(perception, "texts", [])
        if not texts:
            return
        fp = hashlib.md5("|".join(sorted(texts)).encode()).hexdigest()
        # 去重
        for s in self._screens:
            if s.fingerprint == fp:
                return
        self._screens.append(
            Screen(
                id=f"screen_{ctx.step}",
                name=texts[0][:20] if texts else f"screen_{ctx.step}",
                elements=[],
                fingerprint=fp,
            )
        )

    def on_action_executed(
        self, decision: StepDecision, result: ActionResult
    ) -> None:
        """被动维护 screens（不参与循环逻辑）。"""
        pass

    def should_stop(self) -> bool:
        return self._goal_completed or self._token_exceeded()

    def get_state_map(self) -> StateMap:
        return StateMap(
            screens=self._screens,
            root_screen_id=self._screens[0].id if self._screens else "",
            explored_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            backend_info={"strategy": "conversation"},
        )

    def _strip_last_image(self) -> None:
        """剥离最后一条 user 消息的图像块。"""
        if not self._messages:
            return
        last = self._messages[-1]
        if last.get("role") != "user":
            return
        last["content"] = [
            b
            for b in last["content"]
            if isinstance(b, dict) and b.get("type") == "text"
        ]

    def _build_step_text(self, perception: Any, ctx: ExploreContext) -> str:
        parts = [f"目标: {self._intent}", f"步数: {ctx.step}/{ctx.max_steps}"]
        if perception is not None:
            # 优先用带坐标的 text_elements，让 LLM 直接知道每个文字的中心坐标
            elements = getattr(perception, "text_elements", [])
            if elements:
                lines = [f"  {e['text']}@({e['x']:.2f},{e['y']:.2f})" for e in elements[:15]]
                parts.append("界面元素(文字@坐标):\n" + "\n".join(lines))
            else:
                texts = getattr(perception, "texts", [])
                if texts:
                    parts.append("OCR: " + ", ".join(texts[:15]))
        return "\n".join(parts)

    def _token_exceeded(self) -> bool:
        """估算 conversation token 量。"""
        total = 0
        for msg in self._messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict):
                        total += len(b.get("text", ""))
        return total // 4 > self._max_tokens

    @staticmethod
    def _encode_image(screenshot: Any) -> str | None:
        """截图转 base64。"""
        if screenshot is None:
            return None
        try:
            import cv2  # noqa: PLC0415

            if isinstance(screenshot, bytes):
                return base64.b64encode(screenshot).decode("ascii")
            if hasattr(screenshot, "tobytes"):
                _, buf = cv2.imencode(".png", screenshot)
                return base64.b64encode(buf.tobytes()).decode("ascii")
        except Exception:  # noqa: BLE001
            return None
        return None
