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
from joker_test.llm.base import LLMProvider
from joker_test.prompts.loader import load_full_exploration_prompt

_LOGGER = logging.getLogger(__name__)

_BASE_PROMPT = load_full_exploration_prompt()


class ConversationStrategy:
    """conversation context 策略（对齐 Open-AutoGLM）。"""

    def __init__(
        self,
        llm: LLMProvider,
        intent: str,
        max_conversation_tokens: int = 8000,
        plugin_manager: Any = None,
    ) -> None:
        self._llm = llm
        self._intent = intent
        self._max_tokens = max_conversation_tokens
        self._plugin_manager = plugin_manager
        self._system_prompt: str = ""
        self._messages: list[dict[str, Any]] = []
        self._screens: list[Screen] = []
        self._goal_completed: bool = False
        self._initialized = False
        # 上一步操作信息（纯事实，不做判断），供下一步 _build_step_text 注入 observation 行
        self._last_action_info: str = ""
        self._stale_count: int = 0  # 连续无效（界面无变化）次数

    def decide(
        self,
        screenshot: Any,
        perception: Any,
        ctx: ExploreContext,
    ) -> StepDecision:
        """构建 prompt → LLM（tool_use 结构化输出）→ 解析 tool_use block。"""
        if not self._initialized:
            # 系统提示词：base + 插件注入（无 plugin_manager 时用 _BASE_PROMPT）
            # 走顶层 system 参数（Anthropic API 不允许 messages 里出现 role=system）
            sys_prompt = _BASE_PROMPT
            if self._plugin_manager is not None:
                sys_prompt = self._plugin_manager.build_system_prompt(_BASE_PROMPT)
            self._system_prompt = sys_prompt
            self._initialized = True

        img_b64 = self._encode_image(screenshot)
        step_text = self._build_step_text(perception, ctx, screenshot)
        images = [img_b64] if img_b64 else None

        from joker_test.llm.base import build_user_message  # noqa: PLC0415

        user_msg = build_user_message(step_text, images)

        try:
            reply = self._llm.create(
                messages=self._messages + [user_msg],
                system=self._system_prompt,
                tools=[EXPLORE_TOOL_SCHEMA],
                # thinking 开启时 Anthropic 只允许 auto/none，强制 tool 会 400；
                # 模型不调工具由 provider 的 tool_use 重试 + 本地降级兜底
                tool_choice={"type": "auto"},
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

        # 追加 assistant 消息到历史（结构化 XML，不含 thinking）
        # thinking 内容仅存 trace/dump，不回传 API（Anthropic 协议要求 ephemeral）
        action_lines = [
            f"<action>{decision.action}</action>",
        ]
        if decision.target:
            action_lines.append(f"<target>{decision.target}</target>")
        if decision.x is not None and decision.y is not None:
            action_lines.append(f"<coords>{decision.x:.3f},{decision.y:.3f}</coords>")
        if decision.goal_progress:
            action_lines.append(f"<progress>{decision.goal_progress}</progress>")
        self._messages.append(
            {"role": "assistant", "content": [{"type": "text", "text": "\n".join(action_lines)}]}
        )

        if decision.goal_completed:
            self._goal_completed = True

        # 用 perception 的 OCR 文本构建 Screen
        self._maybe_add_screen(perception, ctx)

        return decision

    @staticmethod
    def _extract_thinking(msg: dict) -> str:
        """Extract thinking text from API response thinking blocks."""
        parts = []
        for block in msg.get("content", []):
            if isinstance(block, dict) and block.get("type") == "thinking":
                parts.append(block.get("thinking", ""))
        return "\n".join(parts)

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
                think = ConversationStrategy._extract_thinking(msg) or inp.get("think", "")
                return StepDecision(
                    think=think,
                    action=action,
                    stop=action == "stop",
                    goal_progress=inp.get("goal_progress", ""),
                    goal_completed=inp.get("goal_completed", False),
                    target=target,
                    x=x,
                    y=y,
                    description=target,
                )
        # 无 tool_use block（降级）：tool_choice=auto 下模型可能纯文本回复，
        # provider 重试 3 次仍无工具调用则走到这里。权衡：直接停止探索（而非
        # 降级为无害动作继续）——机械层失败属罕见事件，空转烧 token 更亏；
        # 层次化 replan（主 agent 监督）留待迭代 C 的 L2/L3 分层统一设计。
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
                name=self._infer_screen_name(texts, ctx.step),
                elements=[],
                fingerprint=fp,
            )
        )

    @staticmethod
    def _infer_screen_name(texts: list[str], step: int) -> str:
        """从 OCR 文本推断有意义的界面名（避免用窗口标题当名字）。

        优先级：含"设置/菜单/界面/界面名"特征的文本 > 最长的文本（排除窗口标题）。
        """
        # 排除窗口标题/装饰文本（通常是游戏名或 × □ 等符号）
        skip_prefixes = ("Shattered", "PIXEL", "DUNGEON", "口", "×", "□")
        candidates = [t for t in texts if t and not any(t.startswith(p) for p in skip_prefixes)]
        if not candidates:
            return f"screen_{step}"
        # 优先选含界面语义的文本（设置/音频/菜单等）
        semantic_keywords = ("设置", "菜单", "音频", "音频设置", "显示", "游戏", "选项", "界面", "返回")
        for kw in semantic_keywords:
            for t in candidates:
                if kw in t:
                    return t[:20]
        # 降级：取最长的候选文本
        return max(candidates, key=len)[:20]

    def on_action_executed(
        self,
        decision: StepDecision,
        result: ActionResult,
        validate_feedback: str = "",
    ) -> None:
        """Record a pure-fact summary of the last action (no judgments).

        Following Open-AutoGLM/OpenCode: system provides facts, LLM reasons.
        The observation line goes into the next step via _build_step_text.
        """
        if not result.success:
            self._last_action_info = (
                f"{decision.action} 执行失败({result.error or '未知'})"
            )
            self._stale_count += 1
            return

        # Build pure fact summary line
        parts: list[str] = [decision.action]
        if decision.target:
            parts.append(decision.target[:15])
        elif decision.x is not None and decision.y is not None:
            parts.append(f"({decision.x:.2f},{decision.y:.2f})")
        parts.append(f"diff={result.pixel_diff_ratio:.3f}")
        self._last_action_info = " ".join(parts)

        # Only track stale for should_stop, no feedback text generation
        if result.screen_changed:
            self._stale_count = 0
        else:
            self._stale_count += 1

    def should_stop(self) -> bool:
        # 连续 5 次无效操作 → 判定卡死，停止避免浪费 LLM 调用
        return self._goal_completed or self._token_exceeded() or self._stale_count >= 5

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

    def _build_step_text(self, perception: Any, ctx: ExploreContext, screenshot: Any = None) -> str:
        base = f"目标: {self._intent}\n步数: {ctx.step}/{ctx.max_steps}"
        # Observation line: pure facts from last action (no judgment, no feedback)
        if self._last_action_info:
            base = f"[上一步] {self._last_action_info}\n{base}"
            self._last_action_info = ""

        # Plugin-managed injection path
        if self._plugin_manager is not None:
            return self._plugin_manager.build_step_text(
                screenshot, ctx.backend, ctx, base, validate_feedback="",
            )
        # Fallback: no plugin manager
        parts = [base]
        if perception is not None:
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
