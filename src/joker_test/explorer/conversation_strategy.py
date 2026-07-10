"""ConversationStrategy：对齐 Open-AutoGLM 的 conversation context 策略。

历史截图剥离（只保留当前步截图）+ assistant 重封装为 <think>/<answer> + system 一次。
作为 ReactStateStrategy 的对比基准。
"""
from __future__ import annotations

import base64
import datetime
import json
import logging
import re
from typing import Any

from joker_test.explorer.strategy import (
    ActionResult,
    ExploreContext,
    StepDecision,
)
from joker_test.explorer.types import Screen, StateMap
from joker_test.llm.base import LLMProvider, Message

_LOGGER = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你在探索一个游戏界面，目标是完成任务。每步你看到截图和 OCR 文本，"
    "推理后输出动作。用 <think>推理</think><answer>{json}</answer> 回答。\n"
    'json: {"action":"click_text|click_coord|press_key|swipe|scroll|long_press|back|stop",'
    '"target":"...","description":"...","goal_progress":"...","goal_completed":false}'
)


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
        """构建 prompt + 追加 user 消息（含图）→ LLM → 剥离图 → 重封装 assistant。"""
        if not self._initialized:
            self._messages.append(
                {"role": "system", "content": [{"type": "text", "text": _SYSTEM_PROMPT}]}
            )
            self._initialized = True

        img_b64 = self._encode_image(screenshot)
        step_text = self._build_step_text(perception, ctx)

        user_content: list[dict[str, Any]] = []
        if img_b64:
            user_content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": img_b64,
                    },
                }
            )
        user_content.append({"type": "text", "text": step_text})

        self._messages.append({"role": "user", "content": user_content})

        try:
            reply = self._llm.simple_converse("", self._messages)
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("LLM 决策失败：%s", e)
            self._strip_last_image()
            return StepDecision(think=f"LLM失败:{e}", action="stop", stop=True)

        # 关键：剥离最后一条 user 消息的图像块
        self._strip_last_image()

        think, answer = self._parse_react(reply)

        # 关键：assistant 重封装
        self._messages.append(
            {
                "role": "assistant",
                "content": f"<think>{think}</think><answer>{json.dumps(answer, ensure_ascii=False)}</answer>",
            }
        )

        if answer.get("goal_completed"):
            self._goal_completed = True

        return self._build_decision(think, answer)

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

    @staticmethod
    def _parse_react(msg: Any) -> tuple[str, dict[str, Any]]:
        text = ""
        for block in msg.get("content", []):
            if isinstance(block, dict) and "text" in block:
                text += block["text"]

        think = ""
        think_match = re.search(r"<think>(.*?)</think>", text, re.DOTALL)
        if think_match:
            think = think_match.group(1).strip()

        answer_raw = ""
        answer_match = re.search(r"<answer>(.*?)</answer>", text, re.DOTALL)
        if answer_match:
            answer_raw = answer_match.group(1).strip()

        try:
            answer = json.loads(answer_raw)
        except json.JSONDecodeError:
            answer = {"action": "stop", "description": f"解析失败: {answer_raw[:100]}"}

        return think, answer

    @staticmethod
    def _build_decision(think: str, answer: dict[str, Any]) -> StepDecision:
        action = answer.get("action", "stop")
        target = answer.get("target", "")
        desc = answer.get("description", target)
        if action == "click_coord" and target:
            desc = target
        return StepDecision(
            think=think,
            action=action,
            stop=action == "stop",
            goal_progress=answer.get("goal_progress", ""),
            goal_completed=answer.get("goal_completed", False),
            description=desc,
        )
