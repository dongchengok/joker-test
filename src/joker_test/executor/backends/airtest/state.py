"""_AirtestState —— AirtestBackend 专有的 LazyState 实现（带 `_`，只给 airtest 用）。

按需 OCR（OCRProvider），缓存到当前帧。Backend 每次 screenshot 后通过 invalidate() 清缓存。

M3 改造（2026-07-07）：OCR 从写死 easyocr 改为可插拔 OCRProvider（默认 RapidOCR）。
这让 OCR 引擎可替换（RapidOCR/PaddleOCR/Tesseract），且不绑死某个重依赖。
"""

from __future__ import annotations

from typing import Any

from joker_test.executor.base import BBox
from joker_test.ocr.base import OCRProvider


class _AirtestState:
    """AirtestBackend 的状态代理。按需 OCR，缓存到当前帧。"""

    def __init__(self, get_frame: Any, ocr: OCRProvider | None = None) -> None:
        self._get_frame = get_frame  # 回调，返回当前帧 ndarray
        self._ocr = ocr  # None 时懒加载默认 RapidOCR
        self._ocr_results: list[dict[str, Any]] | None = None  # [{text, bbox}, ...]

    def _ensure_ocr_provider(self) -> OCRProvider:
        """懒加载默认 OCR（RapidOCR）。首次访问才加载模型。"""
        if self._ocr is None:
            from joker_test.ocr.providers.rapidocr import RapidOCRProvider  # noqa: PLC0415
            self._ocr = RapidOCRProvider()
        return self._ocr

    def _ensure_ocr(self) -> None:
        """首次访问触发 OCR，之后缓存。"""
        if self._ocr_results is not None:
            return

        ocr = self._ensure_ocr_provider()
        frame = self._get_frame()
        if frame is None or frame.size == 0:
            self._ocr_results = []
            return

        results = ocr.readtext(frame)
        self._ocr_results = [
            {"text": r.text, "bbox": BBox(r.bbox[0], r.bbox[1], r.bbox[2], r.bbox[3])}
            for r in results
        ]

    @property
    def texts(self) -> list[str]:
        self._ensure_ocr()
        return [r["text"] for r in self._ocr_results]  # type: ignore[union-attr]

    def find_text(self, text: str) -> BBox | None:
        """查找指定文本的位置。精确匹配优先，降级子串匹配。

        精确匹配避免"设置"误匹配"显示设置"标题（子串 bug）。
        """
        self._ensure_ocr()
        # 第一遍：精确匹配（strip 后比，容 OCR 前后空格）
        text_stripped = text.strip()
        for r in self._ocr_results:  # type: ignore[union-attr]
            if r["text"].strip() == text_stripped:
                return r["bbox"]  # type: ignore[index]
        # 第二遍：子串匹配降级（精确匹配找不到才用）
        for r in self._ocr_results:  # type: ignore[union-attr]
            if text in r["text"]:
                return r["bbox"]  # type: ignore[index]
        return None

    def invalidate(self) -> None:
        """新帧到达时清缓存（AirtestBackend.screenshot 后调用）。"""
        self._ocr_results = None


# _AirtestState 是包内私有（_ 前缀，§11.4：不进 __all__）。
# 同包 backend.py 通过 from .state import _AirtestState 直接导入。
