"""_quartz 输入事件测试（假 Quartz 记录事件构造参数）。

注：pyproject 配置 python_classes=[]（不收集 Test* 类），故测试写为 test_* 函数。
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest


class _Recorder:
    """记录事件构造与投递。"""

    def __init__(self) -> None:
        self.mouse_events: list[tuple] = []   # (src, event_type, point, button)
        self.key_events: list[tuple] = []     # (src, keycode, down)
        self.posted: list[tuple] = []         # (tap, event)


@pytest.fixture
def rec(monkeypatch):
    r = _Recorder()
    mod = types.ModuleType("Quartz")
    mod.kCGHIDEventTap = 0
    mod.kCGEventLeftMouseDown = 1
    mod.kCGEventLeftMouseUp = 2
    mod.kCGEventLeftMouseDragged = 6
    mod.kCGMouseButtonLeft = 0

    def _mk_mouse(src, etype, point, button):
        ev = ("mouse", etype, tuple(point), button)
        r.mouse_events.append((src, etype, tuple(point), button))
        return ev

    def _mk_key(src, keycode, down):
        ev = ("key", keycode, down)
        r.key_events.append((src, keycode, down))
        return ev

    mod.CGEventCreateMouseEvent = _mk_mouse
    mod.CGEventCreateKeyboardEvent = _mk_key
    mod.CGEventPost = lambda tap, ev: r.posted.append((tap, ev))
    monkeypatch.setitem(sys.modules, "Quartz", mod)
    from joker_test.executor.backends.mac import _quartz
    importlib.reload(_quartz)
    return r, _quartz


def test_post_click_down_up_at_point(rec):
    r, q = rec
    q.post_click(640.0, 360.0)
    types_seq = [e[1] for e in r.mouse_events]
    assert types_seq == [1, 2]  # down, up
    assert r.mouse_events[0][2] == (640.0, 360.0)
    assert len(r.posted) == 2


def test_post_key_down_up(rec):
    r, q = rec
    q.post_key(53)  # escape
    assert r.key_events == [(None, 53, True), (None, 53, False)]
    assert len(r.posted) == 2


def test_post_swipe_down_drag_up(rec):
    r, q = rec
    q.post_swipe(0.0, 0.0, 100.0, 0.0, duration=0.01)
    types_seq = [e[1] for e in r.mouse_events]
    assert types_seq[0] == 1 and types_seq[-1] == 2  # down ... up
    assert 6 in types_seq  # 中间有 dragged
    assert r.mouse_events[-1][2] == (100.0, 0.0)


def test_post_long_press_down_up_same_point(rec):
    r, q = rec
    q.post_long_press(50.0, 60.0, duration=0.01)
    types_seq = [e[1] for e in r.mouse_events]
    assert types_seq == [1, 2]
    assert r.mouse_events[0][2] == r.mouse_events[1][2] == (50.0, 60.0)


def test_keycodes_common_keys(rec):
    _, q = rec
    for name in ("escape", "enter", "space", "tab", "backspace",
                 "up", "down", "left", "right", "i", "a"):
        assert name in q.KEYCODES, f"缺键位映射: {name}"
    assert q.KEYCODES["escape"] == 53
    assert q.KEYCODES["enter"] == 36
