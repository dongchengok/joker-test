"""MacBackend —— macOS 原生 ExecutorBackend 实现（Quartz 截图 + CGEvent 输入）。

见 docs/superpowers/specs/2026-07-20-macos-compat-design.md。

关键约束：
- 坐标契约与协议一致：对外全部归一化 [0,1]，基准 = screenshot 图像尺寸（pixel）。
  Quartz bounds 是 point、截图是 pixel（Retina 2x），_scale 实测换算在内部消化。
- 前置权限：屏幕录制（截图）+ 辅助功能（CGEvent 输入），connect 时检测并提示。
- 点击发给最上层窗口：connect 时 activate_app 把游戏置前；游戏中途被别的窗口
  盖住时点击会落空（与 airtest 的 G7 限制同类），保持游戏窗口在前。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from joker_test.executor.backends.mac import _quartz
from joker_test.executor.backends.mac.state import _MacState
from joker_test.executor.base import LazyState, NDArray
from joker_test.executor.coords import analyze_screenshot

if TYPE_CHECKING:
    from joker_test.ocr.base import OCRProvider

_LOGGER = logging.getLogger(__name__)


class MacBackend:
    """macOS 原生 Backend。满足 ExecutorBackend 协议（结构性子类型）。

    Args:
        window_title: 目标窗口标题（子串匹配）
        ocr_enabled: 是否启用 OCR（click_text/state.texts 用）。默认 True
        ocr: OCRProvider（None 时 _MacState 懒加载默认 RapidOCR）
    """

    _TITLE_BAR_POINTS = 28.0  # macOS 标准标题栏高（point）

    def __init__(
        self,
        window_title: str,
        ocr_enabled: bool = True,
        ocr: OCRProvider | None = None,
    ) -> None:
        self._window_title = window_title
        self._ocr_enabled = ocr_enabled
        self._ocr = ocr
        self._window_id: int | None = None
        self._bounds: tuple[float, float, float, float] | None = None  # point (x,y,w,h)
        self._pid: int | None = None  # 窗口所属进程（输入前置前用）
        self._layer: int = 0  # 窗口 layer（0=窗口化有标题栏，≠0=全屏无标题栏）
        self._scale: float = 1.0  # 截图像素宽 / bounds point 宽（Retina=2.0）
        self._current_frame: NDArray | None = None  # type: ignore[valid-type]
        self._state: _MacState | None = None

    # ===== 生命周期 =====

    def connect(self) -> None:
        """找窗口 + 置前 + 首帧截图（实测 scale）+ 健康检测。

        Raises:
            RuntimeError: 窗口找不到（游戏未启动，或未授权屏幕录制导致标题为空）。
        """
        found = _quartz.find_window(self._window_title)
        if found is None:
            raise RuntimeError(
                f"找不到窗口 '{self._window_title}'。请确认游戏已启动；"
                "若游戏已在运行仍找不到，检查 系统设置→隐私与安全性→屏幕录制 "
                "是否已授权当前终端（授权后需重启终端）。"
            )
        self._window_id, self._bounds, pid, self._layer = found
        self._pid = pid
        _quartz.activate_app(pid)
        time.sleep(0.5)  # 等窗口置前动画
        frame = self.screenshot()
        health = analyze_screenshot(frame)
        if "失败" in health or "异常" in health:
            _LOGGER.warning(
                "窗口截图异常: %s。可能未授权屏幕录制。请保持游戏窗口可见。", health
            )
        _LOGGER.info(
            "已连接窗口 '%s' (id=%d, bounds=%s, scale=%.2f)",
            self._window_title, self._window_id, self._bounds, self._scale,
        )

    def close(self) -> None:
        self._window_id = None
        self._bounds = None
        self._pid = None
        self._current_frame = None

    # ===== 感知 =====

    def screenshot(self) -> NDArray:  # type: ignore[valid-type]
        """截取游戏窗口内容区，返回 BGR ndarray。每次调用刷新 bounds 与 scale。

        窗口化时裁掉顶部标题栏（28pt × scale），对齐 Windows 客户区截图语义——
        LLM/OCR 坐标与游戏内容四角对齐，避免内容被标题栏挤压偏移。
        """
        if self._window_id is None:
            raise RuntimeError("未 connect，先调用 connect()")
        found = _quartz.find_window(self._window_title)  # 窗口可能移动/缩放
        if found is not None:
            self._window_id, self._bounds, self._pid, self._layer = found
        frame = _quartz.capture_window(self._window_id)
        if self._bounds is not None and self._bounds[2] > 0:
            self._scale = frame.shape[1] / self._bounds[2]
        if self._layer == 0:
            tb_px = round(self._TITLE_BAR_POINTS * self._scale)
            if 0 < tb_px < frame.shape[0]:
                frame = frame[tb_px:, :, :]
        self._current_frame = frame
        if self._state is not None:
            self._state.invalidate()
        return frame

    # ===== 操作（归一化坐标，基准=screenshot 图像尺寸）=====

    def _content_top(self) -> float:
        """内容区相对窗口 bounds 顶部的偏移（point）：窗口化=标题栏高，全屏=0。"""
        return self._TITLE_BAR_POINTS if self._layer == 0 else 0.0

    def _to_screen_point(self, x: float, y: float) -> tuple[float, float]:
        """归一化坐标 → 全局 point 坐标（CGEvent 用）。"""
        if self._current_frame is None or self._bounds is None:
            raise RuntimeError("无当前帧/窗口信息，先 screenshot()")
        h, w = self._current_frame.shape[:2]
        bx, by = self._bounds[0], self._bounds[1] + self._content_top()
        return bx + x * w / self._scale, by + y * h / self._scale

    def _before_input(self) -> None:
        """输入前确保游戏在前台（CGEvent 对后台窗口首击只会聚焦，不会触发）。"""
        if self._pid is not None:
            _quartz.activate_app(self._pid)
            time.sleep(0.15)

    def click(self, x: float, y: float) -> None:
        """归一化坐标点击。"""
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            raise ValueError(f"click 坐标必须在 [0,1]，收到 ({x}, {y})")
        sx, sy = self._to_screen_point(x, y)
        self._before_input()
        _quartz.post_click(sx, sy)

    def click_text(self, text: str) -> bool:
        """OCR 定位文本 → 点 bbox 中心。找不到返回 False。"""
        if not self._ocr_enabled:
            raise RuntimeError("OCR 未启用（ocr_enabled=False）")
        bbox = self.state.find_text(text)
        if bbox is None:
            return False
        self.click(bbox.x + bbox.w / 2, bbox.y + bbox.h / 2)
        return True

    def click_image(self, template: str | NDArray, threshold: float = 0.8) -> bool:  # type: ignore[valid-type]
        """cv2 模板匹配定位并点击（mac 无 airtest Template 可复用，简单实现）。

        Args:
            template: 图像文件路径。ndarray 不支持（与 AirtestBackend 对齐）。
            threshold: TM_CCOEFF_NORMED 匹配阈值 [0,1]，默认 0.8

        Returns:
            匹配成功并点击返回 True，找不到返回 False。
        """
        if not isinstance(template, str):
            raise ValueError("click_image 暂只支持文件路径模板。")
        import cv2  # noqa: PLC0415

        frame = self.screenshot()
        tpl = cv2.imread(template)
        if tpl is None:
            raise ValueError(f"模板图像读不到: {template}")
        res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val < threshold:
            return False
        th, tw = tpl.shape[:2]
        fh, fw = frame.shape[:2]  # type: ignore[attr-defined]
        self.click((max_loc[0] + tw / 2) / fw, (max_loc[1] + th / 2) / fh)
        return True

    def press_key(self, key: str) -> None:
        """按键。key 如 'escape'/'enter'/'i'（映射见 _quartz.KEYCODES）。

        Raises:
            ValueError: 未映射的键名。
        """
        keycode = _quartz.KEYCODES.get(key.lower())
        if keycode is None:
            raise ValueError(f"未知按键: '{key}'（已映射: {sorted(_quartz.KEYCODES)}）")
        self._before_input()
        _quartz.post_key(keycode)

    def type_text(self, text: str) -> None:
        raise NotImplementedError("MacBackend 初版不支持 type_text")

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration: float = 0.5) -> None:
        """滑动（归一化坐标 [0,1]）。"""
        sx1, sy1 = self._to_screen_point(x1, y1)
        sx2, sy2 = self._to_screen_point(x2, y2)
        self._before_input()
        _quartz.post_swipe(sx1, sy1, sx2, sy2, duration=duration)

    def long_press(self, x: float, y: float, duration: float = 2.0) -> None:
        """长按（归一化坐标 [0,1]）。"""
        sx, sy = self._to_screen_point(x, y)
        self._before_input()
        _quartz.post_long_press(sx, sy, duration=duration)

    # ===== 同步 =====

    def wait_until(self, predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
        """轮询 predicate（每次先 screenshot 刷帧），满足返回 True；超时 False。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.screenshot()
            if predicate():
                return True
            time.sleep(0.5)
        self.screenshot()
        return predicate()

    # ===== 状态 =====

    @property
    def state(self) -> LazyState:
        if self._state is None:
            self._state = _MacState(get_frame=self._get_current_frame, ocr=self._ocr)
        return self._state

    def _get_current_frame(self) -> NDArray:  # type: ignore[valid-type]
        """_MacState 的帧回调：懒截图（若无当前帧则现截一张）。"""
        if self._current_frame is None:
            self.screenshot()
        return self._current_frame  # type: ignore[return-value]


__all__ = ["MacBackend"]
