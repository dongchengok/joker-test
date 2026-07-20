"""create_native_backend 平台分发测试。"""
from __future__ import annotations

import sys

import pytest

from joker_test.executor.backends.factory import create_native_backend


def test_darwin_returns_mac_backend(monkeypatch):
    pytest.importorskip("Quartz", reason="需要 pyobjc（pip install -e .[mac]）")
    monkeypatch.setattr(sys, "platform", "darwin")
    from joker_test.executor.backends.mac import MacBackend
    b = create_native_backend("Shattered")
    assert isinstance(b, MacBackend)


def test_win32_returns_airtest_backend(monkeypatch):
    pytest.importorskip("airtest", reason="需要 airtest（pip install -e .[airtest]）")
    monkeypatch.setattr(sys, "platform", "win32")
    from joker_test.executor.backends.airtest import AirtestBackend
    b = create_native_backend("Shattered")
    assert isinstance(b, AirtestBackend)


def test_unsupported_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="不支持的平台"):
        create_native_backend("Shattered")
