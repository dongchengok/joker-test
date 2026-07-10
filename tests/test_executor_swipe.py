"""ExecutorBackend 动作空间扩展测试（swipe/long_press）。"""
from __future__ import annotations

from joker_test.executor.backends.fake import FakeBackend


def test_fake_swipe_records_history() -> None:
    backend = FakeBackend(width=100, height=100)
    backend.connect()
    backend.swipe(0.5, 0.7, 0.5, 0.3, duration=0.5)
    assert len(backend.swipe_history) == 1
    action, coords = backend.swipe_history[0]
    assert action == "swipe"
    assert coords == (0.5, 0.7, 0.5, 0.3, 0.5)
    backend.close()


def test_fake_long_press_records_history() -> None:
    backend = FakeBackend(width=100, height=100)
    backend.connect()
    backend.long_press(0.5, 0.3, duration=2.0)
    assert len(backend.long_press_history) == 1
    action, coords = backend.long_press_history[0]
    assert action == "long_press"
    assert coords == (0.5, 0.3, 2.0)
    backend.close()


def test_fake_swipe_triggers_transition() -> None:
    """swipe 也能触发多屏状态机切换。"""
    from joker_test.executor.backends.fake import ScreenCfg

    backend = FakeBackend(
        screens={
            "list": ScreenCfg(),
            "list2": ScreenCfg(),
        },
        transitions={("list", "swipe_up"): "list2"},
        initial_screen="list",
    )
    backend.connect()
    backend.swipe(0.5, 0.7, 0.5, 0.3)
    assert backend.current_screen_id == "list2"
    backend.close()
