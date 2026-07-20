"""Quartz 封装 —— MacBackend 的窗口查找/截图/输入底层（包内私有）。

macOS 专属。Quartz 模块级 import + ImportError 兜底（仿 airtest/backend.py），
单元测试通过 sys.modules 注入假 Quartz 后 reload 本模块。

关键约束：
- CGWindowList 的 bounds 是 point（逻辑坐标），CGWindowListCreateImage 输出是 pixel
  （Retina 2x），scale 换算在 backend.py 做。
- 未授权"屏幕录制"时，其他 App 的 kCGWindowName 为空串（find_window 找不到）
  且截图内容全黑——调用方负责给出授权提示。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    import Quartz
except ImportError as e:
    raise ImportError(
        "MacBackend 需要 pyobjc-framework-Quartz。请装 mac extras: pip install -e .[mac]"
    ) from e

if TYPE_CHECKING:
    from numpy import ndarray

# (windowID, (x, y, w, h) point 坐标, ownerPID)
WindowInfo = tuple[int, tuple[float, float, float, float], int]


def find_window(title_substr: str) -> WindowInfo | None:
    """按标题子串找第一个 layer=0 的在屏窗口。

    Args:
        title_substr: 窗口标题子串（如 "Shattered"）

    Returns:
        (windowID, (x, y, w, h), ownerPID)；找不到返回 None。
    """
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
    )
    for w in wins:
        name = w.get("kCGWindowName") or ""
        if w.get("kCGWindowLayer", -1) == 0 and title_substr in name:
            b = w["kCGWindowBounds"]
            bounds = (float(b["X"]), float(b["Y"]), float(b["Width"]), float(b["Height"]))
            return int(w["kCGWindowNumber"]), bounds, int(w.get("kCGWindowOwnerPID", 0))
    return None


def capture_window(window_id: int) -> ndarray:
    """截取指定窗口，返回 BGR ndarray。

    CGWindowListCreateImage 默认像素格式 BGRA（little-endian），取前三通道即 BGR。
    窗口被遮挡也能截（合成器里有完整内容）；未授权屏幕录制时内容全黑。

    Args:
        window_id: find_window 返回的 windowID

    Returns:
        BGR ndarray，形状 (h, w, 3)。

    Raises:
        RuntimeError: CGWindowListCreateImage 返回 None。
    """
    import numpy as np  # noqa: PLC0415

    img = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        window_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming,
    )
    if img is None:
        raise RuntimeError(
            "截图失败（CGWindowListCreateImage 返回 None）。窗口可能已关闭。"
        )
    w = Quartz.CGImageGetWidth(img)
    h = Quartz.CGImageGetHeight(img)
    bpr = Quartz.CGImageGetBytesPerRow(img)
    data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(img))
    buf = np.frombuffer(data, dtype=np.uint8)
    bgra = buf[: h * bpr].reshape(h, bpr)[:, : w * 4].reshape(h, w, 4)
    return bgra[:, :, :3].copy()


def activate_app(pid: int) -> None:
    """把指定 PID 的 App 窗口置前（点击事件发给最上层窗口，必须先置前）。

    Args:
        pid: find_window 返回的 ownerPID。
    """
    from AppKit import (  # noqa: PLC0415
        NSApplicationActivateIgnoringOtherApps,
        NSRunningApplication,
    )

    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is not None:
        app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)


# ===== 输入事件（CGEvent，全局 point 坐标）=====

# mac 虚拟键码（kVK_*，来自 HIToolbox/Events.h）
KEYCODES: dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37,
    "j": 38, "k": 40, "n": 45, "m": 46,
    "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22,
    "7": 26, "8": 28, "9": 25, "0": 29,
    "enter": 36, "tab": 48, "space": 49, "backspace": 51, "escape": 53,
    "left": 123, "right": 124, "down": 125, "up": 126,
}


def post_click(x: float, y: float) -> None:
    """在全局 point 坐标 (x, y) 单击左键。"""
    point = (x, y)
    for etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        ev = Quartz.CGEventCreateMouseEvent(None, etype, point, Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def post_key(keycode: int) -> None:
    """按一下指定键码（down + up）。"""
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def post_swipe(x1: float, y1: float, x2: float, y2: float, duration: float = 0.5) -> None:
    """从 (x1, y1) 拖到 (x2, y2)（全局 point 坐标），duration 秒内完成。"""
    import time  # noqa: PLC0415

    steps = max(int(duration / 0.01), 2)
    down = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventLeftMouseDown, (x1, y1), Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    for i in range(1, steps + 1):
        xi = x1 + (x2 - x1) * i / steps
        yi = y1 + (y2 - y1) * i / steps
        ev = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseDragged, (xi, yi), Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(duration / steps)
    up = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventLeftMouseUp, (x2, y2), Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def post_long_press(x: float, y: float, duration: float = 2.0) -> None:
    """在 (x, y) 长按左键 duration 秒。"""
    import time  # noqa: PLC0415

    down = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventLeftMouseDown, (x, y), Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    time.sleep(duration)
    up = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventLeftMouseUp, (x, y), Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


__all__ = [
    "WindowInfo", "find_window", "capture_window", "activate_app",
    "KEYCODES", "post_click", "post_key", "post_swipe", "post_long_press",
]
