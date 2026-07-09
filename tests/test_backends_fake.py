"""FakeBackend 全接口测试（M1 核心，CI 用，不依赖真实游戏）。

覆盖 ExecutorBackend 协议的所有方法 + LazyState 缓存语义。
这是 M1 的完成标志（roadmap: "FakeBackend 单测全绿"）。
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from joker_test.executor.backends.fake import FakeBackend
from joker_test.executor.base import BBox, ExecutorBackend, LazyState
from joker_test.executor.coords import analyze_screenshot, pixel_diff_ratio

# ============== 协议校验 ==============

def test_fake_backend_satisfies_protocol() -> None:
    """FakeBackend 必须满足 ExecutorBackend 协议（runtime_checkable）。"""
    fb = FakeBackend()
    assert isinstance(fb, ExecutorBackend)


def test_fake_state_satisfies_protocol() -> None:
    """FakeBackend.state 必须满足 LazyState 协议。"""
    fb = FakeBackend(texts_map={"开始": BBox(0.4, 0.5, 0.2, 0.1)})
    fb.connect()
    assert isinstance(fb.state, LazyState)


# ============== 生命周期 ==============

def test_connect_close() -> None:
    fb = FakeBackend()
    fb.connect()
    fb.screenshot()  # connect 后能截图
    fb.close()
    with pytest.raises(RuntimeError, match="未 connect"):
        fb.screenshot()


def test_screenshot_before_connect_raises() -> None:
    fb = FakeBackend()
    with pytest.raises(RuntimeError, match="未 connect"):
        fb.screenshot()


# ============== 感知 ==============

def test_screenshot_shape_and_dtype() -> None:
    fb = FakeBackend(width=120, height=80)
    fb.connect()
    img = fb.screenshot()
    assert img.shape == (80, 120, 3)  # (H, W, 3)
    assert img.dtype == np.uint8


def test_screenshot_new_frame_invalidates_state() -> None:
    """新 screenshot 后 state 应重建（缓存失效）。"""
    fb = FakeBackend()
    fb.connect()
    fb.screenshot()
    state1 = fb.state
    _ = state1.texts  # 触发缓存
    assert state1.resolve_count == 1

    fb.screenshot()  # 新帧
    state2 = fb.state
    assert state2 is not state1  # 新对象


# ============== click（归一化坐标）==============

def test_click_records_history() -> None:
    fb = FakeBackend()
    fb.connect()
    fb.click(0.5, 0.6)
    assert fb.click_history == [("click", (0.5, 0.6))]


def test_click_rejects_out_of_range() -> None:
    """归一化坐标必须在 [0,1]。"""
    fb = FakeBackend()
    fb.connect()
    with pytest.raises(ValueError, match=r"\[0,1\]"):
        fb.click(1.5, 0.5)
    with pytest.raises(ValueError, match=r"\[0,1\]"):
        fb.click(-0.1, 0.5)


# ============== click_text ==============

def test_click_text_hit() -> None:
    """文本在 texts_map 中 → 点击命中，记录历史。"""
    fb = FakeBackend(texts_map={"开始游戏": BBox(0.4, 0.5, 0.2, 0.1)})
    fb.connect()
    assert fb.click_text("开始游戏") is True
    assert fb.click_history == [("click_text", "开始游戏")]


def test_click_text_miss() -> None:
    """文本不在 → 返回 False，不记录 click。"""
    fb = FakeBackend(texts_map={"开始": BBox(0.4, 0.5, 0.2, 0.1)})
    fb.connect()
    assert fb.click_text("退出") is False
    assert fb.click_history == []


# ============== 其他操作 ==============

def test_click_image_records_history() -> None:
    fb = FakeBackend()
    fb.connect()
    assert fb.click_image("btn.png") is True
    assert fb.click_history == [("click_image", "btn.png")]


def test_press_key_records_history() -> None:
    fb = FakeBackend()
    fb.connect()
    fb.press_key("escape")
    fb.press_key("i")
    assert fb.key_history == ["escape", "i"]


def test_type_text_records_history() -> None:
    fb = FakeBackend()
    fb.connect()
    fb.type_text("hello")
    assert fb.type_history == ["hello"]


# ============== wait_until ==============

def test_wait_until_success() -> None:
    fb = FakeBackend()
    fb.connect()
    flag = {"v": False}
    def pred() -> bool:
        return flag["v"]
    # 异步翻 flag（FakeBackend 轮询间隔 0.01s）
    import threading
    def setter() -> None:
        time.sleep(0.05)
        flag["v"] = True
    threading.Thread(target=setter).start()
    assert fb.wait_until(pred, timeout=2.0) is True


def test_wait_until_timeout() -> None:
    fb = FakeBackend()
    fb.connect()
    assert fb.wait_until(lambda: False, timeout=0.3) is False


# ============== state（LazyState）==============

def test_state_texts_returns_preset() -> None:
    fb = FakeBackend(texts_map={
        "开始": BBox(0.4, 0.5, 0.2, 0.1),
        "设置": BBox(0.7, 0.1, 0.1, 0.05),
    })
    fb.connect()
    fb.screenshot()
    assert set(fb.state.texts) == {"开始", "设置"}


def test_state_find_text_returns_bbox() -> None:
    bbox = BBox(0.4, 0.5, 0.2, 0.1)
    fb = FakeBackend(texts_map={"开始": bbox})
    fb.connect()
    fb.screenshot()
    assert fb.state.find_text("开始") == bbox
    assert fb.state.find_text("不存在") is None


def test_lazy_state_caches_within_frame() -> None:
    """同一帧内多次访问 texts 只解析一次（LazyState 缓存语义）。"""
    fb = FakeBackend(texts_map={"a": BBox(0, 0, 0.1, 0.1)})
    fb.connect()
    fb.screenshot()
    state = fb.state
    _ = state.texts
    _ = state.texts
    _ = state.texts
    assert state.resolve_count == 1  # 只解析一次


# ============== coords.py 公共工具 ==============

def test_analyze_screenshot_real_image() -> None:
    """analyze_screenshot 对多颜色图像应判为"真实画面"。"""
    # 构造有多颜色的图像（不是单色填充，那样会被判"单色异常"）
    img = np.zeros((50, 50, 3), dtype="uint8")
    for i in range(50):
        for j in range(50):
            img[i, j] = [(i * 5) % 256, (j * 4) % 256, ((i + j) * 3) % 256]
    result = analyze_screenshot(img)
    assert "真实画面" in result


def test_analyze_screenshot_black() -> None:
    img = np.zeros((10, 10, 3), dtype="uint8")  # 全黑
    assert "全黑" in analyze_screenshot(img)


def test_pixel_diff_ratio() -> None:
    a = np.zeros((10, 10, 3), dtype="uint8")
    b = np.full((10, 10, 3), 200, dtype="uint8")
    # 全部像素都变化
    assert pixel_diff_ratio(a, b) == 1.0


def test_pixel_diff_ratio_shape_mismatch() -> None:
    a = np.zeros((10, 10, 3), dtype="uint8")
    b = np.zeros((20, 20, 3), dtype="uint8")
    with pytest.raises(ValueError, match="尺寸不一致"):
        pixel_diff_ratio(a, b)
