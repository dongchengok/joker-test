"""ExploreStrategy 协议 + 数据契约测试。"""
from __future__ import annotations

from joker_test.explorer.strategy import (
    ActionResult,
    ExploreAction,
    ExploreStrategy,
    StepDecision,
)


def test_explore_action_literal() -> None:
    assert "click_text" in ExploreAction.__args__
    assert "swipe" in ExploreAction.__args__
    assert "back" in ExploreAction.__args__
    assert "stop" in ExploreAction.__args__
    assert "long_press" in ExploreAction.__args__
    assert "scroll" in ExploreAction.__args__


def test_step_decision_defaults() -> None:
    d = StepDecision(think="测试", action="click_text")
    assert d.stop is False
    assert d.goal_progress == ""
    assert d.goal_completed is False
    assert d.description == ""


def test_action_result_defaults() -> None:
    r = ActionResult(success=True, screen_changed=True)
    assert r.pixel_diff_ratio == 0.0
    assert r.error is None


def test_strategy_is_protocol() -> None:
    assert hasattr(ExploreStrategy, "_is_protocol")


def test_models_not_collected() -> None:
    for cls in (StepDecision, ActionResult):
        assert getattr(cls, "__test__", True) is False
