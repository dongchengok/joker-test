"""OCRProvider 协议 + OCRResult 数据结构。

OCR 引擎可插拔的接缝。上层（_AirtestState）只面向此协议。
实现：RapidOCRProvider（默认）、EasyOCRProvider（可选）等。

设计要点：
- Protocol + @runtime_checkable（与 LLMProvider/ExecutorBackend 一致）
- OCRResult 用归一化 bbox（[0,1]，基准=screenshot 图像尺寸），与 Backend 坐标契约一致（G6 自洽）
- 异步友好：当前同步，签名兼容未来 async
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class OCRResult(BaseModel):
    """单条 OCR 识别结果。

    Attributes:
        text: 识别出的文本
        bbox: 归一化边界框 [x, y, w, h]，基准=screenshot 图像尺寸（与 Backend click 契约一致）
        confidence: 置信度 [0,1]（部分引擎不提供，默认 1.0）
    """

    text: str
    bbox: tuple[float, float, float, float]
    confidence: float = 1.0

    # pydantic 类名以 OCR 开头不含 Test，但加 __test__ 防御性声明
    __test__ = False


@runtime_checkable
class OCRProvider(Protocol):
    """OCR 引擎协议。输入图像，输出文本+位置。

    实现者：
      - RapidOCRProvider（默认，onnxruntime 轻量）
      - 其他实现（EasyOCR/PaddleOCR/Tesseract）按需添加

    用法::
        ocr = RapidOCRProvider()
        results = ocr.readtext(frame)  # list[OCRResult]
    """

    def readtext(self, frame) -> list[OCRResult]:
        """识别图像中的文本，返回带归一化 bbox 的结果列表。

        Args:
            frame: BGR ndarray（与 Backend.screenshot 返回一致）

        Returns:
            OCRResult 列表，每个含文本 + 归一化 bbox。空列表表示无文本。
        """
        ...


__all__ = ["OCRProvider", "OCRResult"]
