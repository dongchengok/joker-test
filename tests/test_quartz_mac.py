"""_quartz 单元测试（假 Quartz 注入，不依赖真窗口）。

注：pyproject 配置 python_classes=[]（不收集 Test* 类），故测试写为 test_* 函数。
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest


def _make_fake_quartz(windows: list[dict], image: dict | None = None) -> types.ModuleType:
    """构造假 Quartz 模块。windows 为 CGWindowListCopyWindowInfo 返回的窗口 dict 列表。

    image: 假 CGImage dict {"w": int, "h": int, "bpr": int, "data": bytes}，None 表示截图返回 None。
    """
    mod = types.ModuleType("Quartz")
    mod.kCGWindowListOptionOnScreenOnly = 1
    mod.kCGNullWindowID = 0
    mod.kCGWindowListOptionIncludingWindow = 1
    mod.kCGWindowImageBoundsIgnoreFraming = 1
    mod.kCGHIDEventTap = 0
    mod.kCGEventLeftMouseDown = 1
    mod.kCGEventLeftMouseUp = 2
    mod.kCGEventLeftMouseDragged = 6
    mod.kCGMouseButtonLeft = 0
    mod.CGRectNull = "CGRectNull"
    mod.CGWindowListCopyWindowInfo = lambda opts, rel: windows
    mod.CGWindowListCreateImage = (
        (lambda rect, opts, wid, img_opts: image) if image is not None
        else (lambda rect, opts, wid, img_opts: None)
    )
    mod.CGImageGetWidth = lambda img: img["w"]
    mod.CGImageGetHeight = lambda img: img["h"]
    mod.CGImageGetBytesPerRow = lambda img: img["bpr"]
    mod.CGImageGetDataProvider = lambda img: img
    mod.CGDataProviderCopyData = lambda p: p["data"]
    return mod


def _bgra_image(w: int, h: int) -> dict:
    """造一个 w×h 的 BGRA 假 CGImage（每个像素 B=10,G=20,R=30,A=255）。"""
    px = bytes([10, 20, 30, 255])
    return {"w": w, "h": h, "bpr": w * 4, "data": px * (w * h)}


@pytest.fixture
def quartz_mod(monkeypatch):
    """注入假 Quartz 并 reload _quartz，返回设置函数。

    teardown：从 sys.modules 移除已 reload 的 _quartz，
    避免其模块全局永久指向假 Quartz 污染同进程后续真机调用。
    """
    def _setup(windows: list[dict], image: dict | None = None):
        fake = _make_fake_quartz(windows, image)
        monkeypatch.setitem(sys.modules, "Quartz", fake)
        from joker_test.executor.backends.mac import _quartz
        importlib.reload(_quartz)
        return _quartz
    yield _setup
    # 同时清掉父包上的 _quartz 属性，否则下次 from mac import _quartz
    # 会命中残留属性（不再触发 import），导致 reload 报 "module not in sys.modules"
    sys.modules.pop("joker_test.executor.backends.mac._quartz", None)
    mac_pkg = sys.modules.get("joker_test.executor.backends.mac")
    if mac_pkg is not None:
        vars(mac_pkg).pop("_quartz", None)


_WIN = {
    "kCGWindowName": "Shattered Pixel Dungeon",
    "kCGWindowLayer": 0,
    "kCGWindowNumber": 42,
    "kCGWindowOwnerPID": 777,
    "kCGWindowBounds": {"X": 100.0, "Y": 50.0, "Width": 800.0, "Height": 600.0},
}


def test_find_window_found(quartz_mod):
    q = quartz_mod([_WIN])
    assert q.find_window("Shattered") == (42, (100.0, 50.0, 800.0, 600.0), 777)


def test_find_window_not_found(quartz_mod):
    q = quartz_mod([_WIN])
    assert q.find_window("OtherGame") is None


def test_find_window_nonzero_layer_fallback(quartz_mod):
    """全屏游戏窗口 layer≠0（实测 SPD 全屏 layer=25），兜底匹配任意 layer。"""
    win = dict(_WIN, kCGWindowLayer=25)
    q = quartz_mod([win])
    assert q.find_window("Shattered") == (42, (100.0, 50.0, 800.0, 600.0), 777)


def test_find_window_prefers_layer0(quartz_mod):
    """同标题多窗口时优先 layer=0 的普通窗口。"""
    fullscreen = dict(_WIN, kCGWindowLayer=25, kCGWindowNumber=99)
    q = quartz_mod([fullscreen, _WIN])
    assert q.find_window("Shattered")[0] == 42


def test_find_window_empty_name_skipped(quartz_mod):
    win = dict(_WIN, kCGWindowName=None)
    q = quartz_mod([win])
    assert q.find_window("Shattered") is None


def test_capture_window_bgra_to_bgr(quartz_mod):
    q = quartz_mod([_WIN], image=_bgra_image(4, 3))
    frame = q.capture_window(42)
    assert frame.shape == (3, 4, 3)
    # BGRA(10,20,30,255) → BGR(10,20,30)
    assert frame[0, 0].tolist() == [10, 20, 30]


def test_capture_window_nil_image_falls_back(quartz_mod, monkeypatch):
    """CG API 返回 None 时兜底 screencapture（如其他 Space 的全屏窗口）。"""
    import numpy as np

    q = quartz_mod([_WIN], image=None)
    sentinel = np.zeros((2, 2, 3), dtype=np.uint8)
    monkeypatch.setattr(q, "_capture_via_screencapture", lambda wid: sentinel)
    assert q.capture_window(42) is sentinel


def test_capture_window_both_paths_fail_raises(quartz_mod, monkeypatch):
    q = quartz_mod([_WIN], image=None)

    def _boom(wid):
        raise RuntimeError("截图失败（screencapture 也失败）")

    monkeypatch.setattr(q, "_capture_via_screencapture", _boom)
    with pytest.raises(RuntimeError, match="截图失败"):
        q.capture_window(42)
