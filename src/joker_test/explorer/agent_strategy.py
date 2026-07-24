"""AgentStrategy：完整 AI agent 探索策略。

对比 ConversationStrategy（截图+OCR 自动注入 prompt、单一 execute_action 工具）：
本策略把感知与执行全拆成独立工具（agent_tools.AGENT_TOOL_SCHEMAS），LLM 在 agent
loop 内按需调用——单步（外层 decide）内最多 max_agent_rounds 轮 LLM 调用，单轮可
多个 tool_use。动作在 loop 内即时执行，返回给外壳的 StepDecision 恒为 action="stop"
（外壳 _dispatch_action 对 "stop" 无分支，不会重复执行）。

状态管理与 ConversationStrategy 一致：screens 指纹去重 / stale_count / goal_completed /
conversation 历史 / token 估算。
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

from joker_test.explorer.agent_tools import AGENT_TOOL_SCHEMAS, AgentToolExecutor
from joker_test.explorer.strategy import (
    ActionResult,
    ExploreContext,
    StepDecision,
)
from joker_test.explorer.types import Screen, StateMap
from joker_test.llm.base import LLMProvider
from joker_test.prompts.loader import load_constant

_LOGGER = logging.getLogger(__name__)

_IMAGE_PLACEHOLDER = "[截图]"


class AgentStrategy:
    """agent loop 探索策略（工具化感知/执行）。实现 ExploreStrategy 协议。"""

    def __init__(
        self,
        llm: LLMProvider,
        intent: str,
        max_conversation_tokens: int = 16000,
        plugin_manager: Any = None,
        max_agent_rounds: int = 6,
        finish_gate: Any = None,
    ) -> None:
        self._llm = llm
        self._intent = intent
        self._max_tokens = max_conversation_tokens
        self._plugin_manager = plugin_manager
        self._max_agent_rounds = max_agent_rounds
        # finish 完成验证门：callable() -> str | None（None=通过，str=打回原因）
        # 由脚本侧注入真实检查（如读游戏 settings.xml），防 LLM 谎报完成
        self._finish_gate = finish_gate
        self._system_prompt: str = ""
        self._messages: list[dict[str, Any]] = []
        self._screens: list[Screen] = []
        self._goal_completed: bool = False
        self._finished: bool = False  # finish 工具被调用（无论目标是否完成）
        self._initialized = False
        self._stale_count: int = 0  # 连续无效（界面无变化）步数
        self._executor: AgentToolExecutor | None = None  # 首次 decide 用 ctx.backend 构建
        # 本步 decide 内动作类工具的执行结果（on_action_executed 据此管理 stale_count）
        self._step_results: list[dict[str, Any]] = []
        self._finish_info: dict[str, Any] | None = None  # 本步 finish 调用记录
        self._last_step_summary: str = ""  # 上一步执行摘要，注入下一步 user 消息
        self._id_counter: int = 0  # tool_use id 重写计数器（见 decide 注释）

    def decide(
        self,
        screenshot: Any,
        perception: Any,
        ctx: ExploreContext,
    ) -> StepDecision:
        """外层单步：追加步信息 → agent 内层 loop（多轮工具调用）→ 汇总决策。"""
        if not self._initialized:
            # 系统提示词：agent_system + 插件注入（顶层 system 参数，
            # Anthropic API 不允许 messages 里出现 role=system）
            sys_prompt = load_constant("agent_system")
            if self._plugin_manager is not None:
                sys_prompt = self._plugin_manager.build_system_prompt(sys_prompt)
            self._system_prompt = sys_prompt
            self._initialized = True

        if self._executor is None:
            self._executor = AgentToolExecutor(ctx.backend, recorder=ctx.recorder)

        self._step_results = []
        self._finish_info = None

        # 每步只给目标/步数/上一步摘要，不自动带截图——让 LLM 自己调 get_screenshot
        self._messages.append({
            "role": "user",
            "content": [{"type": "text", "text": self._build_step_text(ctx)}],
        })

        for _ in range(self._max_agent_rounds):
            try:
                reply = self._llm.create(
                    messages=self._messages,
                    system=self._system_prompt,
                    tools=AGENT_TOOL_SCHEMAS,
                    tool_choice={"type": "auto"},
                )
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("LLM 决策失败：%s", e)
                return StepDecision(think=f"LLM失败:{e}", action="stop", stop=True)

            # 回传 assistant 消息前剥离 thinking block（无 signature 回传会 400）
            assistant_blocks = [
                b
                for b in reply.get("content", [])
                if isinstance(b, dict) and b.get("type") != "thinking"
            ]
            # 重写 tool_use id 保证全历史唯一：kimi 端点按 name:N 生成 id，
            # 多轮后重复会 400（"tool call id xxx is duplicated"）卡死后续所有调用。
            # 用新 dict 而非原地改，避免污染 provider 的回复对象。
            rewritten_blocks: list[dict[str, Any]] = []
            for b in assistant_blocks:
                if b.get("type") == "tool_use":
                    self._id_counter += 1
                    b = {**b, "id": f"call_{self._id_counter}"}
                rewritten_blocks.append(b)
            assistant_blocks = rewritten_blocks
            self._messages.append({"role": "assistant", "content": assistant_blocks})

            tool_uses = [b for b in assistant_blocks if b.get("type") == "tool_use"]
            if not tool_uses:
                break  # 无工具调用（纯文本），结束内层 loop

            # Anthropic 要求一个 assistant 的所有 tool_use 在同一个后续 user 消息里给结果
            result_blocks = [self._run_tool(tu) for tu in tool_uses]
            self._messages.append({"role": "user", "content": result_blocks})

            # 历史中的 image 块替换为文本占位（控制 token）
            self._strip_images()

            if self._finish_info is not None:
                break  # finish 被调用，结束内层 loop

        summary = self._summarize_step()
        self._last_step_summary = summary
        self._maybe_add_screen(perception, ctx)

        if self._finish_info is not None:
            return StepDecision(
                think=summary,
                action="stop",
                stop=True,
                goal_completed=bool(self._finish_info.get("goal_completed", False)),
                goal_progress=summary,
            )
        # 动作已在 loop 内执行完；stop=False 让外壳继续下一步（"stop" 动作不会被外壳执行）
        return StepDecision(think=summary, action="stop", stop=False)

    def _run_tool(self, tool_use: dict[str, Any]) -> dict[str, Any]:
        """执行单个 tool_use，返回 tool_result block。

        finish 只记录不执行（结束探索是策略决策，不是 backend 动作）。
        """
        name = tool_use.get("name", "")
        inp = tool_use.get("input", {}) or {}
        if name == "finish":
            goal_completed = bool(inp.get("goal_completed", False))
            summary = str(inp.get("summary", ""))
            # finish 完成验证门：goal_completed=true 时先过脚本侧真实检查，
            # 不过门则打回（不当 finish 处理），让 LLM 继续探索/修正
            if goal_completed and self._finish_gate is not None:
                try:
                    reject = self._finish_gate()
                except Exception as e:  # noqa: BLE001
                    _LOGGER.warning("finish_gate 检查异常（视为通过）：%s", e)
                    reject = None
                if reject:
                    _LOGGER.info("finish_gate 打回：%s", reject)
                    content = [{
                        "type": "text",
                        "text": json.dumps(
                            {
                                "success": False,
                                "gate_rejected": True,
                                "error": f"完成验证未通过：{reject}。不要谎报完成——继续探索真正达成目标，或确认无法完成时 finish(goal_completed=false)。",
                            },
                            ensure_ascii=False,
                        ),
                    }]
                    return {
                        "type": "tool_result",
                        "tool_use_id": tool_use.get("id", ""),
                        "content": content,
                    }
            self._finish_info = {"goal_completed": goal_completed, "summary": summary}
            self._finished = True
            if goal_completed:
                self._goal_completed = True
            content = [{
                "type": "text",
                "text": json.dumps({"success": True, "message": "探索已结束"}, ensure_ascii=False),
            }]
        else:
            assert self._executor is not None  # decide 入口已构建
            out = self._executor.execute(name, inp)
            content = out["tool_result_content"]
            executed = out["executed_action"]
            if executed is not None:
                self._step_results.append(executed)
        return {
            "type": "tool_result",
            "tool_use_id": tool_use.get("id", ""),
            "content": content,
        }

    def _build_step_text(self, ctx: ExploreContext) -> str:
        """每步的 user 消息：目标/步数/上一步执行摘要（不自动带截图）。"""
        base = f"目标: {self._intent}\n步数: {ctx.step}/{ctx.max_steps}"
        if self._last_step_summary:
            base = f"[上一步执行摘要] {self._last_step_summary}\n{base}"
            self._last_step_summary = ""
        base += "\n需要界面信息时用 get_screenshot / get_ocr_text 获取；完成目标后调用 finish。"
        return base

    def _summarize_step(self) -> str:
        """本步执行摘要（finish summary 或动作结果拼接）。"""
        if self._finish_info is not None:
            return str(self._finish_info.get("summary", "")) or "探索结束"
        if not self._step_results:
            return "本步未执行操作"
        parts: list[str] = []
        for r in self._step_results:
            mark = "成功" if r.get("success") else f"失败({r.get('error', '')})"
            changed = "界面变化" if r.get("screen_changed") else "界面无变化"
            parts.append(f"{r.get('tool')} {mark} {changed}")
        return "; ".join(parts)

    def _strip_images(self) -> None:
        """把历史中（含 tool_result 内嵌）的 image 块替换为文本占位。"""
        for msg in self._messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for i, block in enumerate(content):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "image":
                    content[i] = {"type": "text", "text": _IMAGE_PLACEHOLDER}
                elif block.get("type") == "tool_result":
                    inner = block.get("content")
                    if isinstance(inner, list):
                        for j, ib in enumerate(inner):
                            if isinstance(ib, dict) and ib.get("type") == "image":
                                inner[j] = {"type": "text", "text": _IMAGE_PLACEHOLDER}

    def _maybe_add_screen(self, perception: Any, ctx: ExploreContext) -> None:
        """用 OCR 文本构建 Screen，去重后加入 _screens（同 ConversationStrategy）。"""
        import hashlib  # noqa: PLC0415

        texts: list[str] = []
        if perception is not None:
            texts = getattr(perception, "texts", [])
        if not texts:
            return
        fp = hashlib.md5("|".join(sorted(texts)).encode()).hexdigest()  # noqa: S324
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
        """从 OCR 文本推断有意义的界面名（同 ConversationStrategy）。"""
        skip_prefixes = ("Shattered", "PIXEL", "DUNGEON", "口", "×", "□")
        candidates = [t for t in texts if t and not any(t.startswith(p) for p in skip_prefixes)]
        if not candidates:
            return f"screen_{step}"
        semantic_keywords = ("设置", "菜单", "音频", "音频设置", "显示", "游戏", "选项", "界面", "返回")
        for kw in semantic_keywords:
            for t in candidates:
                if kw in t:
                    return t[:20]
        return max(candidates, key=len)[:20]

    def on_action_executed(
        self,
        decision: StepDecision,
        result: ActionResult,
        validate_feedback: str = "",
    ) -> None:
        """动作执行后回调：用本步 decide 内工具执行结果管理 stale_count。

        忽略外壳传来的 result——动作已在 agent loop 内执行，外壳对 "stop" 动作无分支。
        本步有动作且任一 screen_changed=True → 清零；否则 +1。
        """
        if any(r.get("screen_changed") for r in self._step_results):
            self._stale_count = 0
        else:
            self._stale_count += 1

    def should_stop(self) -> bool:
        # 连续 5 步无效操作 → 判定卡死，停止避免浪费 LLM 调用
        return (
            self._goal_completed
            or self._finished
            or self._token_exceeded()
            or self._stale_count >= 5
        )

    def get_state_map(self) -> StateMap:
        return StateMap(
            screens=self._screens,
            root_screen_id=self._screens[0].id if self._screens else "",
            explored_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            backend_info={"strategy": "agent"},
        )

    def _token_exceeded(self) -> bool:
        """估算 conversation token 量（字符数 // 4，同 ConversationStrategy）。"""
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


__all__ = ["AgentStrategy"]
