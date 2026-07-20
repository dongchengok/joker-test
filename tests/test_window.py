"""executor/window.py 跨平台窗口等待测试。

注：pyproject 配置 python_classes=[]（不收集 Test* 类），故测试写为 test_* 函数。
"""
from __future__ import annotations

import sys
import types

import pytest

from joker_test.executor import window as win_mod


@pytest.fixture
def as_darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    return monkeypatch


@pytest.fixture
def as_win32(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    return monkeypatch


def _fake_quartz_mod(found: bool) -> types.ModuleType:
    mod = types.ModuleType("joker_test.executor.backends.mac._quartz")
    mod.find_window = lambda title: (1, (0.0, 0.0, 100.0, 100.0), 1) if found else None
    return mod


def _fake_win32gui(visible_titles: list[str]) -> types.ModuleType:
    mod = types.ModuleType("win32gui")
    mod.IsWindowVisible = lambda h: True
    mod.GetWindowText = lambda h: visible_titles[h]

    def _enum(cb, _):
        for h in range(len(visible_titles)):
            cb(h, None)

    mod.EnumWindows = _enum
    return mod


def test_window_exists_darwin_found(as_darwin, monkeypatch):
    monkeypatch.setitem(
        sys.modules, "joker_test.executor.backends.mac._quartz", _fake_quartz_mod(True)
    )
    assert win_mod.window_exists("Shattered") is True


def test_window_exists_darwin_not_found(as_darwin, monkeypatch):
    monkeypatch.setitem(
        sys.modules, "joker_test.executor.backends.mac._quartz", _fake_quartz_mod(False)
    )
    assert win_mod.window_exists("Shattered") is False


def test_window_exists_win32_found(as_win32, monkeypatch):
    monkeypatch.setitem(sys.modules, "win32gui", _fake_win32gui(["Shattered Pixel Dungeon"]))
    assert win_mod.window_exists("Shattered") is True


def test_window_exists_win32_not_found(as_win32, monkeypatch):
    monkeypatch.setitem(sys.modules, "win32gui", _fake_win32gui(["其他窗口"]))
    assert win_mod.window_exists("Shattered") is False


def test_window_exists_unsupported_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="不支持的平台"):
        win_mod.window_exists("Shattered")


def test_wait_for_window_immediate(as_darwin, monkeypatch):
    monkeypatch.setitem(
        sys.modules, "joker_test.executor.backends.mac._quartz", _fake_quartz_mod(True)
    )
    assert win_mod.wait_for_window("Shattered", timeout=1.0, interval=0.05) is True


def test_wait_for_window_timeout(as_darwin, monkeypatch):
    monkeypatch.setitem(
        sys.modules, "joker_test.executor.backends.mac._quartz", _fake_quartz_mod(False)
    )
    assert win_mod.wait_for_window("Shattered", timeout=0.2, interval=0.05) is False
