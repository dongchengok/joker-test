"""ReactStateStrategy：ReAct 思维链 + 状态机驱动探索。

默认策略。用 ExploreState 维护探索图/路径/队列，每步 LLM 收到固定大小的状态摘要
（不随步数增长），输出 <think>推理</think><answer>动作</answer>。
"""
from __future__ import annotations

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
from joker_test.explorer.types import Exit, Screen, StateMap, UIElement
from joker_test.llm.base import LLMProvider

_LOGGER = logging.getLogger(__name__)


class ExploreState:
    """探索状态机（状态自洽）。"""

    def __init__(self, goal: str) -> None:
        self.goal = goal
        self.screens: list[Screen] = []
        self.exits: list[Exit] = []
        self.path_stack: list[Exit] = []
        self.unvisited: dict[str, list[UIElement]] = {}
        self.goal_completed: bool = False
        self.goal_progress: str = ""
        self.stale_count: int = 0
        self.tried_actions: set[tuple[str, str]] = set()
        self._current_screen_id: str | None = None

    @property
    def current_screen_id(self) -> str | None:
        return self._current_screen_id

    def add_screen(self, screen: Screen) -> bool:
        """添加界面，返回是否新界面。"""
        for s in self.screens:
            if s.fingerprint == screen.fingerprint:
                self._current_screen_id = s.id
                return False
        self.screens.append(screen)
        self._current_screen_id = screen.id
        self.unvisited[screen.id] = list(screen.elements)
        return True

    def quantize_target(self, action: str, target: str) -> str:
        """坐标网格量化去重。"""
        if action == "click_coord":
            try:
                parts = target.split(",")
                x = round(float(parts[0]) * 10) / 10
                y = round(float(parts[1]) * 10) / 10
                return f"coord({x:.1f},{y:.1f})"
            except (ValueError, IndexError):
                return target
        return target


class ReactStateStrategy:
    """ReAct + 状态机策略。"""

    def __init__(
        self,
        llm: LLMProvider,
        intent: str,
        max_stale: int = 3,
    ) -> None:
        self._llm = llm
        self._max_stale = max_stale
        self._state = ExploreState(goal=intent)

    def decide(
        self,
        screenshot: Any,
        perception: Any,
        ctx: ExploreContext,
    ) -> StepDecision:
        """ReAct 决策：截图+perception+状态摘要 → LLM（tool_use）→ 解析。"""
        prompt = self._build_prompt(screenshot, perception, ctx)
        from joker_test.llm.base import build_user_message  # noqa: PLC0415

        try:
            msg = self._llm.create(
                messages=[build_user_message(prompt)],
                tools=[EXPLORE_TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": "execute_action"},
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("LLM 决策失败：%s", e)
            return StepDecision(think=f"LLM失败:{e}", action="stop", stop=True)

        return self._parse_tool_use(msg, screenshot)

    def on_action_executed(
        self, decision: StepDecision, result: ActionResult
    ) -> None:
        """动作执行后更新状态。"""
        if not result.success:
            self._state.stale_count += 1
            return

        if result.screen_changed:
            self._state.stale_count = 0
        else:
            self._state.stale_count += 1

        # 去重 key：click 用坐标量化，其他用 target
        if decision.action == "click" and decision.x is not None:
            target = f"coord({round(decision.x * 10) / 10:.1f},{round(decision.y * 10) / 10:.1f})"
        else:
            target = decision.target or decision.description
        fp = self._state.current_screen_id or "unknown"
        self._state.tried_actions.add((fp, target))

        if decision.goal_progress:
            self._state.goal_progress = decision.goal_progress
        if decision.goal_completed:
            self._state.goal_completed = True

    def should_stop(self) -> bool:
        return self._state.goal_completed or self._state.stale_count >= self._max_stale

    def get_state_map(self) -> StateMap:
        root_id = self._state.screens[0].id if self._state.screens else ""
        return StateMap(
            screens=self._state.screens,
            root_screen_id=root_id,
            explored_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            backend_info={"strategy": "react_state"},
        )

    def _build_prompt(
        self, screenshot: Any, perception: Any, ctx: ExploreContext
    ) -> str:
        """构建 ReAct prompt（固定大小状态摘要）。"""
        parts = [
            f"目标: {self._state.goal}",
            f"进度: {self._state.goal_progress or '开始探索'}",
            f"步数: {ctx.step}/{ctx.max_steps}",
        ]

        if perception is not None:
            elements = getattr(perception, "text_elements", [])
            if elements:
                lines = [f"  {e['text']}@({e['x']:.2f},{e['y']:.2f})" for e in elements[:15]]
                parts.append("界面元素(文字@坐标):\n" + "\n".join(lines))
            else:
                texts = getattr(perception, "texts", [])
                if texts:
                    parts.append("OCR 文本: " + ", ".join(texts[:15]))

        if self._state.screens:
            screen_summary = "; ".join(
                f"{s.name or s.id}({len(s.elements)}元素)" for s in self._state.screens
            )
            parts.append(f"已探索: {screen_summary}")

        if self._state.path_stack:
            path = "→".join(e.from_screen for e in self._state.path_stack)
            parts.append(f"路径: {path}")

        prompt = (
            "你在探索一个游戏界面。请推理下一步。\n"
            + "\n".join(parts)
            + "\n\n用 <think>推理</think><answer>{json}</answer> 回答。\n"
            "动作只能是以下 8 种之一：\n"
            '1. {"action":"click","target":"按钮文字","description":"..."}\n'
            '2. {"action":"press_key","target":"escape","description":"..."}\n'
            '3. {"action":"type_text","target":"文字","description":"..."}\n'
            '4. {"action":"swipe","target":"up","description":"向上滑动"}\n'
            '5. {"action":"scroll","target":"down","description":"向下翻页"}\n'
            '6. {"action":"long_press","target":"图标描述","description":"..."}\n'
            '7. {"action":"back","description":"返回上级"}\n'
            '8. {"action":"stop","description":"目标完成"}\n'
            "规则：click 的 target 是按钮文字，不要输出坐标，不要点全屏/关闭按钮。"
        )
        return prompt

    def _parse_tool_use(self, msg: Any, screenshot: Any = None) -> StepDecision:
        """从 LLM 回复的 tool_use block 提取结构化动作。"""
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
        return StepDecision(think="无 tool_use block", action="stop", stop=True)
