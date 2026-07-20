"""平台分发工厂 —— 按当前平台创建原生桌面 Backend。

脚本不直接实例化 AirtestBackend/MacBackend，统一走 create_native_backend，
由工厂按 sys.platform 分发（win32 → AirtestBackend，darwin → MacBackend）。
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from joker_test.executor.base import ExecutorBackend
    from joker_test.ocr.base import OCRProvider


def create_native_backend(
    window_title: str, ocr: OCRProvider | None = None
) -> ExecutorBackend:
    """按 sys.platform 创建原生桌面 backend。

    Args:
        window_title: 目标窗口标题（子串匹配）
        ocr: OCRProvider（None 时各 backend 懒加载默认 RapidOCR）

    Returns:
        win32 → AirtestBackend；darwin → MacBackend。

    Raises:
        RuntimeError: 不支持的平台。
    """
    if sys.platform == "win32":
        from joker_test.executor.backends.airtest import AirtestBackend  # noqa: PLC0415

        return AirtestBackend(window_title=window_title, ocr=ocr)
    if sys.platform == "darwin":
        from joker_test.executor.backends.mac import MacBackend  # noqa: PLC0415

        return MacBackend(window_title=window_title, ocr=ocr)
    raise RuntimeError(f"不支持的平台: {sys.platform}（仅支持 Windows/macOS 桌面）")


__all__ = ["create_native_backend"]
