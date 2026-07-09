"""joker_test.ocr —— OCR 抽象层。

让 OCR 引擎可插拔（与 LLMProvider/ExecutorBackend 同思路）。
默认实现 RapidOCR（onnxruntime，轻量，~50MB，中文好）。

引擎无关：上层（_AirtestState / UIExplorer）只面向 OCRProvider 协议。
"""

from joker_test.ocr.base import OCRProvider, OCRResult

__all__ = ["OCRProvider", "OCRResult"]
