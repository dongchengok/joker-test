"""LLMExplorer 外壳测试（用 FakeBackend + Mock 策略）。"""
from __future__ import annotations

from unittest.mock import MagicMock

from joker_test.executor.backends.fake import FakeBackend, ScreenCfg
from joker_test.explorer.llm_explorer import LLMExplorer
from joker_test.explorer.strategy import (
    StepDecision,
)
from joker_test.explorer.types import Screen, StateMap


class _MockStrategy:
    """最小策略实现，用于测试外壳循环。"""

    def __init__(self, steps: list[StepDecision]) -> None:
        self._steps = steps
        self._idx = 0
        self._screens: list[Screen] = [
            Screen(id="root", name="根界面", elements=[], fingerprint="fp0")
        ]
        self.executed: list[str] = []

    def decide(self, screenshot, perception, ctx) -> StepDecision:
        if self._idx >= len(self._steps):
            return StepDecision(think="done", action="stop", stop=True)
        d = self._steps[self._idx]
        self._idx += 1
        return d

    def on_action_executed(self, decision, result) -> None:
        self.executed.append(decision.action)

    def should_stop(self) -> bool:
        return self._idx >= len(self._steps)

    def get_state_map(self) -> StateMap:
        return StateMap(
            screens=self._screens,
            root_screen_id="root",
            explored_at="2026-01-01T00:00:00Z",
        )


def test_explorer_runs_loop_and_returns_state_map() -> None:
    """外壳循环执行策略决策，返回 StateMap。"""
    backend = FakeBackend(
        screens={"root": ScreenCfg()}, initial_screen="root"
    )
    decisions = [
        StepDecision(think="step1", action="press_key", description="escape"),
        StepDecision(think="done", action="stop", stop=True),
    ]
    strategy = _MockStrategy(decisions)
    explorer = LLMExplorer(
        backend=backend, llm=MagicMock(), strategy=strategy, max_steps=10
    )
    state_map = explorer.explore()
    assert isinstance(state_map, StateMap)
    assert strategy.executed == ["press_key"]


def test_explorer_stops_on_strategy_should_stop() -> None:
    """策略 should_stop 返回 True 时循环终止。"""
    backend = FakeBackend(screens={"root": ScreenCfg()}, initial_screen="root")
    strategy = _MockStrategy([
        StepDecision(think="s1", action="press_key", description="escape"),
    ])
    explorer = LLMExplorer(
        backend=backend, llm=MagicMock(), strategy=strategy, max_steps=10
    )
    explorer.explore()
    assert strategy._idx == 1


def test_explorer_max_steps_limit() -> None:
    """max_steps 上限生效。"""
    backend = FakeBackend(screens={"root": ScreenCfg()}, initial_screen="root")
    strategy = _MockStrategy([])
    explorer = LLMExplorer(
        backend=backend, llm=MagicMock(), strategy=strategy, max_steps=3
    )
    explorer.explore()
