"""RapidOCRProvider —— 基于 RapidOCR（onnxruntime）的 OCR 实现。

默认实现。优势：
- 轻量（~50MB，onnxruntime 不含训练框架）
- 中文准确率继承 PaddleOCR（模型是 PaddleOCR 转 onnx）
- numpy ABI 友好（onnxruntime 自己管张量内存，不碰 numpy C 层）

依赖：rapidocr_onnxruntime（在 [ocr] extras）。
导入兜底：未装时给清晰错误。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from joker_test.ocr.base import OCRResult


@lru_cache(maxsize=1)
def _get_engine() -> Any:
    """懒加载 RapidOCR 引擎（首次调用加载模型，~1-2s，之后缓存）。"""
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError as e:
        raise ImportError(
            "RapidOCRProvider 需要 rapidocr_onnxruntime。"
            "请装 ocr extras: pip install -e .[ocr]"
        ) from e
    return RapidOCR()


class RapidOCRProvider:
    """RapidOCR 的 OCRProvider 实现。满足协议（结构性子类型）。

    readtext 把 RapidOCR 的输出（像素坐标 4 点）转成归一化 bbox [x,y,w,h]。
    坐标基准=输入图像尺寸（与 Backend.screenshot/click 契约一致，G6 自洽）。
    """

    def readtext(self, frame) -> list[OCRResult]:
        """识别图像文本，返回归一化 bbox 的 OCRResult 列表。"""

        if frame is None or frame.size == 0:
            return []

        engine = _get_engine()
        # RapidOCR 返回 (result, elapse)
        # result: [[box, text, conf], ...]，box 是 4 点像素坐标 [[x1,y1],...]
        raw, _elapse = engine(frame)
        if not raw:
            return []

        h, w = frame.shape[:2]
        results: list[OCRResult] = []
        for item in raw:
            box, text, conf = item[0], item[1], item[2]
            # box: 4 点 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]（像素）→ 归一化 [x,y,w,h]
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)
            results.append(
                OCRResult(
                    text=str(text),
                    bbox=(
                        x_min / w,
                        y_min / h,
                        (x_max - x_min) / w,
                        (y_max - y_min) / h,
                    ),
                    confidence=float(conf) if conf is not None else 1.0,
                )
            )
        return results


__all__ = ["RapidOCRProvider"]
