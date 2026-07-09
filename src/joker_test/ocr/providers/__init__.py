"""joker_test.ocr.providers —— OCR 引擎实现。

- RapidOCRProvider（默认，onnxruntime 轻量，~50MB，中文好）
- 其他实现按需添加（EasyOCR/PaddleOCR/Tesseract）
"""

from joker_test.ocr.providers.rapidocr import RapidOCRProvider

__all__ = ["RapidOCRProvider"]
