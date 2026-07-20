"""跨平台窗口等待 —— 脚本启动前等游戏窗口就绪（替代脚本里的 win32gui 轮询）。

按 sys.platform 分派：darwin → Quartz 枚举；win32 → win32gui。
两边均函数内懒导入，互不污染依赖。
"""
from __future__ import annotations

import sys
import time


def window_exists(title_substr: str) -> bool:
    """检查是否存在标题含 title_substr 的可见窗口。

    Args:
        title_substr: 窗口标题子串（如 "Shattered"）

    Returns:
        存在返回 True。

    Raises:
        RuntimeError: 不支持的平台（仅 win32/darwin）。
    """
    if sys.platform == "darwin":
        import importlib  # noqa: PLC0415

        # 用 import_module 而非 from-import：from-import 优先取包属性，
        # 测试向 sys.modules 注入假 _quartz 时会被真实模块顶掉。
        _quartz = importlib.import_module("joker_test.executor.backends.mac._quartz")
        return _quartz.find_window(title_substr) is not None
    if sys.platform == "win32":
        import win32gui  # noqa: PLC0415

        found: list[int] = []

        def _cb(h: int, _: object) -> bool:
            if win32gui.IsWindowVisible(h) and title_substr in win32gui.GetWindowText(h):
                found.append(h)
            return True

        win32gui.EnumWindows(_cb, None)
        return bool(found)
    raise RuntimeError(f"不支持的平台: {sys.platform}（仅支持 Windows/macOS）")


def wait_for_window(title_substr: str, timeout: float = 10.0, interval: float = 1.0) -> bool:
    """轮询等窗口出现。

    Args:
        title_substr: 窗口标题子串
        timeout: 超时秒数
        interval: 轮询间隔秒数

    Returns:
        超时前出现返回 True，否则 False。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if window_exists(title_substr):
            return True
        time.sleep(interval)
    return window_exists(title_substr)


__all__ = ["wait_for_window", "window_exists"]
