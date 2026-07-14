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

_BASE_PROMPT = """你在探索一个游戏界面，目标是完成任务。每步你看到截图和界面元素信息，推理后输出动作。

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
- swipe 的 target 填方向 left/right/up/down，x/y 填滑块手柄的当前坐标（从截图上识别手柄位置）
- 音量滑块、进度条等水平控件**必须用 swipe 拖拽**，绝不能用 click 点击轨道
  （点击轨道不会移动手柄）。拖拽起点 = 手柄当前位置，终点 = 手柄 + 方向偏移
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
        plugin_manager: Any = None,
    ) -> None:
        self._llm = llm
        self._intent = intent
        self._max_tokens = max_conversation_tokens
        self._plugin_manager = plugin_manager
        self._messages: list[Message] = []
        self._screens: list[Screen] = []
        self._goal_completed: bool = False
        self._initialized = False
        self._last_validate_feedback: str = ""
        # 上一步动作结果反馈（让 LLM 知道操作是否生效，避免盲目重复无效操作）
        self._last_action_feedback: str = ""
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
            sys_prompt = _BASE_PROMPT
            if self._plugin_manager is not None:
                sys_prompt = self._plugin_manager.build_system_prompt(_BASE_PROMPT)
            self._messages.append(
                {"role": "system", "content": [{"type": "text", "text": sys_prompt}]}
            )
            self._initialized = True

        img_b64 = self._encode_image(screenshot)
        step_text = self._build_step_text(perception, ctx, screenshot)
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
        """记录上一步动作的通用结果（执行失败/连续无效计数）。

        语义层的有效性判断（"OCR 文本是否变了"等）由插件 validate 负责，
        validate_feedback 传入让策略更准确地判断 stale（插件说无效 → 计 stale，
        即使像素 screen_changed=True）。

        Args:
            decision: 本步决策
            result: 执行结果（像素 diff 层）
            validate_feedback: 插件语义校验反馈（空串 = 无问题/无插件）
        """
        if not result.success:
            self._last_action_feedback = f"⚠ 上一步执行失败（{result.error or '未知错误'}），请换个方法。"
            self._stale_count += 1
            return

        # 插件判定操作无效（如 OCR 文本没变）→ 计 stale，即使像素 diff 说变了
        operation_effective = result.screen_changed and not validate_feedback

        if operation_effective:
            self._stale_count = 0
            self._last_action_feedback = ""
        else:
            self._stale_count += 1
            if self._stale_count >= 2:
                self._last_action_feedback = (
                    f"⚠ 连续 {self._stale_count} 步操作无效果，可能陷入循环，请换一种方法。"
                )

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
        # 上一步动作反馈（策略通用反馈 + 插件语义反馈，都注入引导 LLM 换方法）
        feedbacks = [f for f in (self._last_action_feedback, self._last_validate_feedback) if f]
        if feedbacks:
            base = f"上一步反馈: {'；'.join(feedbacks)}\n{base}"

        # 有 plugin_manager → 走插件注入
        if self._plugin_manager is not None:
            return self._plugin_manager.build_step_text(
                screenshot, ctx.backend, ctx, base, validate_feedback="",
            )
        # 无 plugin_manager → 降级到原有逻辑（向后兼容）
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
