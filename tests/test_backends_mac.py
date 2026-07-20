"""MacBackend 单元测试（monkeypatch _quartz，不依赖真窗口）。

注：pyproject 配置 python_classes=[]（不收集 Test* 类），故测试写为 test_* 函数。
"""
from __future__ import annotations

import pytest

pytest.importorskip("Quartz", reason="需要 pyobjc（pip install -e .[mac]）")

import numpy as np

from joker_test.executor.backends.mac import _quartz
from joker_test.executor.backends.mac.backend import MacBackend

# 窗口 bounds (point)：x=100, y=50, w=400, h=300；截图 800×600 pixel → scale=2.0
_BOUNDS = (100.0, 50.0, 400.0, 300.0)
_FRAME = np.random.default_rng(0).integers(20, 230, (600, 800, 3), dtype=np.uint8)


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr(_quartz, "find_window", lambda t: (42, _BOUNDS, 777))
    monkeypatch.setattr(_quartz, "capture_window", lambda wid: _FRAME.copy())
    monkeypatch.setattr(_quartz, "activate_app", lambda pid: None)
    clicks: list[tuple] = []
    keys: list[int] = []
    monkeypatch.setattr(_quartz, "post_click", lambda x, y: clicks.append((x, y)))
    monkeypatch.setattr(_quartz, "post_key", lambda kc: keys.append(kc))
    b = MacBackend(window_title="Shattered")
    b._test_clicks = clicks  # 暴露断言入口
    b._test_keys = keys
    return b


def test_connect_ok(backend):
    backend.connect()
    assert backend._scale == pytest.approx(2.0)


def test_window_not_found(monkeypatch):
    monkeypatch.setattr(_quartz, "find_window", lambda t: None)
    with pytest.raises(RuntimeError, match="找不到窗口"):
        MacBackend(window_title="Shattered").connect()


def test_click_normalized_to_global_point(backend):
    backend.connect()
    backend.click(0.5, 0.5)
    # 截图像素中心 (400, 300) → point (200, 150) → 全局 (100+200, 50+150)
    assert backend._test_clicks == [(300.0, 200.0)]


def test_click_out_of_range(backend):
    backend.connect()
    with pytest.raises(ValueError, match="\\[0,1\\]"):
        backend.click(1.5, 0.0)


def test_press_named_key(backend):
    backend.connect()
    backend.press_key("escape")
    assert backend._test_keys == [53]


def test_press_unknown_key(backend):
    backend.connect()
    with pytest.raises(ValueError, match="未知按键"):
        backend.press_key("f19")


def test_satisfies_protocol(backend):
    from joker_test.executor.base import ExecutorBackend

    assert isinstance(backend, ExecutorBackend)


def test_type_text_not_implemented(backend):
    with pytest.raises(NotImplementedError):
        backend.type_text("hello")


def test_click_image_match_and_click(backend, tmp_path):
    import cv2

    # 模板 = 帧的一块区域，必能匹配上
    tpl = _FRAME[100:140, 200:260].copy()
    tpl_path = tmp_path / "tpl.png"
    cv2.imwrite(str(tpl_path), tpl)
    backend.connect()
    assert backend.click_image(str(tpl_path), threshold=0.95) is True
    # 中心点：((200+30)/800, (100+20)/600) → point → 全局
    assert len(backend._test_clicks) == 1


def test_click_image_no_match(backend, tmp_path):
    import cv2

    tpl = np.zeros((40, 40, 3), dtype=np.uint8)  # 纯黑，帧里没有
    tpl[20:30, 20:30] = 255
    tpl_path = tmp_path / "tpl.png"
    cv2.imwrite(str(tpl_path), tpl)
    backend.connect()
    assert backend.click_image(str(tpl_path), threshold=0.999) is False
    assert backend._test_clicks == []
