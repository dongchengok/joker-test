"""FakeBackend —— 内存模拟的 ExecutorBackend（CI/测试用，全实现）。

M1：单屏无状态（texts_map 固定）。
M2 扩展：多屏状态机（screens + transitions），支持探索器测试"点击→切屏→去重→回退"。
单屏构造方式（只传 texts_map）完全向后兼容 M1。

不依赖 airtest/cv2/win32，纯 Python + numpy。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from joker_test.executor.base import BBox, LazyState, NDArray


@dataclass
class ScreenCfg:
    """多屏 FakeBackend 的单个屏幕配置（M2 新增）。

    Args:
        texts_map: 文本 → BBox 映射（同 M1 单屏的 texts_map）
        bg_pixel: 该屏的背景色 BGR（不同屏用不同颜色，让 pixel_diff_ratio 能检测到切屏）
    """

    texts_map: dict[str, BBox] = field(default_factory=dict)
    bg_pixel: tuple[int, int, int] = (50, 50, 50)


class _FakeState:
    """FakeBackend 的 LazyState 实现（FakeBackend 专用，带 `_`）。

    texts 直接返回预设列表（不做真 OCR）。find_text 查预设映射返回 BBox。
    缓存验证：_resolve_count 记录解析次数，测试可断言"只解析一次"。
    """

    def __init__(self, texts_map: dict[str, BBox], frame: Any) -> None:
        self._texts_map = texts_map
        self._frame = frame
        self._texts_cache: list[str] | None = None
        self.resolve_count = 0  # 测试用：记录解析次数

    @property
    def texts(self) -> list[str]:
        if self._texts_cache is None:
            self.resolve_count += 1
            self._texts_cache = list(self._texts_map.keys())
        return self._texts_cache

    def find_text(self, text: str) -> BBox | None:
        return self._texts_map.get(text)


class FakeBackend:
    """内存模拟的 Backend。满足 ExecutorBackend 协议（结构性子类型）。

    两种用法：
      1. 单屏（M1 风格，向后兼容）::
            fb = FakeBackend(texts_map={"开始": BBox(...)})
      2. 多屏状态机（M2+ 探索器测试用）::
            fb = FakeBackend(
                screens={"root": ScreenCfg(...), "settings": ScreenCfg(...)},
                transitions={("root","设置"): "settings", ("settings","返回"): "root"},
                initial_screen="root",
            )

    Args:
        width, height: 截图返回图像的像素尺寸
        texts_map: 单屏模式的文本映射（与 screens 互斥；M1 兼容）
        bg_pixel: 单屏模式的背景色（M1 兼容）
        screens: 多屏模式的屏幕配置 dict[screen_id, ScreenCfg]
        transitions: 多屏模式的转移 dict[(screen_id, action) -> screen_id]。
                     action 是被点击的文本（click_text）或坐标字符串（click）
        initial_screen: 多屏模式的初始 screen_id
    """

    def __init__(
        self,
        width: int = 100,
        height: int = 100,
        texts_map: dict[str, BBox] | None = None,
        bg_pixel: tuple[int, int, int] = (50, 50, 50),
        # M2 新增：多屏状态机参数
        screens: dict[str, ScreenCfg] | None = None,
        transitions: dict[tuple[str, str], str] | None = None,
        initial_screen: str | None = None,
    ) -> None:
        self._width = width
        self._height = height
        self._texts_map: dict[str, BBox] = texts_map or {}
        self._bg_pixel = bg_pixel
        self._connected = False
        self._frame_count = 0
        self._current_state: _FakeState | None = None

        # 多屏状态机（M2）
        self._screens = screens
        self._transitions = transitions or {}
        self._current_screen_id: str | None = None
        if screens and initial_screen:
            if initial_screen not in screens:
                raise ValueError(f"initial_screen '{initial_screen}' 不在 screens 中")
            self._current_screen_id = initial_screen
            self._apply_screen(initial_screen)

        # 操作历史（测试断言用）
        self.click_history: list[tuple[str, Any]] = []
        self.key_history: list[str] = []
        self.type_history: list[str] = []

    def _apply_screen(self, screen_id: str) -> None:
        """切换当前屏幕：更新 texts_map 和 bg_pixel（多屏模式）。"""
        if self._screens is None or screen_id not in self._screens:
            return
        screen = self._screens[screen_id]
        self._texts_map = screen.texts_map
        self._bg_pixel = screen.bg_pixel

    @property
    def current_screen_id(self) -> str | None:
        """当前屏幕 id（多屏模式，测试断言用）。单屏模式为 None。"""
        return self._current_screen_id

    # ===== 生命周期 =====

    def connect(self) -> None:
        self._connected = True

    def close(self) -> None:
        self._connected = False

    # ===== 感知 =====

    def screenshot(self) -> NDArray:  # type: ignore[valid-type]
        """返回固定 BGR ndarray（当前屏的背景色填充）。"""
        import numpy as np  # noqa: PLC0415

        if not self._connected:
            raise RuntimeError("Backend 未 connect")
        self._frame_count += 1
        # 新帧 → state 缓存失效
        self._current_state = None
        img = np.zeros((self._height, self._width, 3), dtype="uint8")
        img[:] = self._bg_pixel
        return img

    # ===== 操作（归一化坐标，基准=screenshot 图像尺寸）=====

    def click(self, x: float, y: float) -> None:
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            raise ValueError(f"click 坐标必须在 [0,1]，收到 ({x}, {y})")
        self.click_history.append(("click", (x, y)))
        self._maybe_transition(f"click@{x:.3f},{y:.3f}")

    def click_text(self, text: str) -> bool:
        bbox = self._texts_map.get(text)
        if bbox is None:
            return False
        self.click_history.append(("click_text", text))
        self._maybe_transition(text)
        return True

    def click_image(self, template: str | NDArray, threshold: float = 0.8) -> bool:  # type: ignore[valid-type]
        # FakeBackend 总是返回 True，记录历史（不做真实图像匹配）。
        # threshold 参数仅为对齐协议签名，FakeBackend 不用。
        self.click_history.append(("click_image", template))
        return True

    def press_key(self, key: str) -> None:
        self.key_history.append(key)
        # 多屏：press_key 也可能触发转移（如 escape 回上一屏）
        self._maybe_transition(f"key@{key}")

    def type_text(self, text: str) -> None:
        self.type_history.append(text)

    def _maybe_transition(self, action_key: str) -> None:
        """多屏模式：查 transitions，命中则切屏（单屏模式无操作）。"""
        if self._current_screen_id is None:
            return
        key = (self._current_screen_id, action_key)
        target = self._transitions.get(key)
        if target is not None:
            self._current_screen_id = target
            self._apply_screen(target)
            # 切屏后 state 缓存失效（旧 state 绑定的是旧屏的 texts_map）
            self._current_state = None

    # ===== 同步 =====

    def wait_until(self, predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)  # FakeBackend 用更短间隔加速测试
        return predicate()

    # ===== 状态 =====

    @property
    def state(self) -> LazyState:
        if self._current_state is None:
            self._current_state = _FakeState(self._texts_map, frame=self._frame_count)
        return self._current_state


__all__ = ["FakeBackend", "ScreenCfg"]
