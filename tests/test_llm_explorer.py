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

    def on_action_executed(self, decision, result, validate_feedback: str = "") -> None:
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


class _FlakyBackend(FakeBackend):
    """前 N 次 screenshot 后抛 RuntimeError（模拟游戏窗口丢失），healed 后恢复。"""

    def __init__(self, fail_after: int, **kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kw)
        self._fail_after = fail_after
        self._shot_calls = 0
        self.healed = False

    def screenshot(self):  # type: ignore[no-untyped-def]
        self._shot_calls += 1
        if self._shot_calls > self._fail_after and not self.healed:
            raise RuntimeError("窗口丢失")
        return super().screenshot()


def test_explorer_recovers_from_backend_failure() -> None:
    """backend 抛 RuntimeError 时调用 recovery 钩子并继续探索。"""
    backend = _FlakyBackend(
        fail_after=2, screens={"root": ScreenCfg()}, initial_screen="root"
    )
    recovery_calls: list[int] = []

    def recover() -> None:
        recovery_calls.append(1)
        backend.healed = True

    strategy = _MockStrategy([
        StepDecision(think="s1", action="press_key", target="escape"),
        StepDecision(think="done", action="stop", stop=True),
    ])
    explorer = LLMExplorer(
        backend=backend, llm=MagicMock(), strategy=strategy,
        max_steps=10, recovery=recover,
    )
    explorer.explore()
    assert recovery_calls == [1]


def test_explorer_reraises_without_recovery() -> None:
    """无 recovery 钩子时 RuntimeError 上抛。"""
    import pytest

    backend = _FlakyBackend(
        fail_after=0, screens={"root": ScreenCfg()}, initial_screen="root"
    )
    strategy = _MockStrategy([
        StepDecision(think="s1", action="press_key", target="escape"),
    ])
    explorer = LLMExplorer(
        backend=backend, llm=MagicMock(), strategy=strategy, max_steps=10
    )
    with pytest.raises(RuntimeError, match="窗口丢失"):
        explorer.explore()
