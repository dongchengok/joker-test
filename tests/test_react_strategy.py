"""ReactStateStrategy 测试。"""
from __future__ import annotations

from unittest.mock import MagicMock

from joker_test.explorer.react_strategy import ExploreState, ReactStateStrategy
from joker_test.explorer.strategy import ActionResult, ExploreContext, StepDecision
from joker_test.explorer.types import Screen
from joker_test.llm.providers.mock import MockProvider


def test_explore_state_init() -> None:
    state = ExploreState(goal="进入地牢")
    assert state.goal == "进入地牢"
    assert state.screens == []
    assert state.path_stack == []
    assert state.unvisited == {}
    assert state.goal_completed is False
    assert state.stale_count == 0


def test_should_stop_on_goal_completed() -> None:
    strategy = ReactStateStrategy(llm=MockProvider(), intent="目标")
    strategy._state.goal_completed = True
    assert strategy.should_stop() is True


def test_should_stop_on_stale_limit() -> None:
    strategy = ReactStateStrategy(llm=MockProvider(), intent="目标", max_stale=3)
    strategy._state.stale_count = 3
    assert strategy.should_stop() is True


def test_should_not_stop_early() -> None:
    strategy = ReactStateStrategy(llm=MockProvider(), intent="目标")
    assert strategy.should_stop() is False


def test_on_action_executed_records_tried_action() -> None:
    """动作执行后记录 tried_actions。"""
    strategy = ReactStateStrategy(llm=MockProvider(), intent="目标")
    decision = StepDecision(think="点按钮", action="click", description="开始")
    result = ActionResult(success=True, screen_changed=True, pixel_diff_ratio=0.2)
    strategy.on_action_executed(decision, result)
    assert len(strategy._state.tried_actions) >= 1


def test_on_action_executed_no_change_increases_stale() -> None:
    """动作执行后界面无变化 → stale_count +1。"""
    strategy = ReactStateStrategy(llm=MockProvider(), intent="目标")
    strategy._state.stale_count = 0
    decision = StepDecision(think="点按钮", action="click", description="开始")
    result = ActionResult(success=True, screen_changed=False)
    strategy.on_action_executed(decision, result)
    assert strategy._state.stale_count == 1


def test_get_state_map_returns_statemap() -> None:
    strategy = ReactStateStrategy(llm=MockProvider(), intent="目标")
    strategy._state.screens = [
        Screen(id="root", name="主菜单", elements=[], fingerprint="fp0")
    ]
    sm = strategy.get_state_map()
    assert sm.root_screen_id == "root"
    assert len(sm.screens) == 1


def test_decide_calls_llm_with_react_format() -> None:
    """decide 调 LLM，解析 <think>/<answer> 格式。"""
    mock_llm = MagicMock()
    mock_llm.create.return_value = {
        "content": [
            {
                "type": "tool_use",
                "name": "execute_action",
                "input": {
                    "action": "click",
                    "target": "进入地牢",
                    "think": "看到主菜单，应该点进入地牢",
                    "goal_progress": "正在进入",
                },
            }
        ]
    }
    strategy = ReactStateStrategy(llm=mock_llm, intent="进入地牢")
    ctx = ExploreContext(
        step=0, max_steps=10, intent="进入地牢", backend=None, llm=mock_llm
    )
    decision = strategy.decide(screenshot=None, perception=None, ctx=ctx)
    assert decision.action == "click"
    assert "主菜单" in decision.think or "进入地牢" in decision.think
    assert decision.goal_progress == "正在进入"
