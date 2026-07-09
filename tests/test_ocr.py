"""OCR 抽象层测试。

验证 OCRProvider 协议 + RapidOCRProvider 在简单图像上的识别。
不依赖真实游戏（用 PIL 画文字图）。
"""

from __future__ import annotations

import numpy as np

from joker_test.ocr import OCRProvider, OCRResult
from joker_test.ocr.providers.rapidocr import RapidOCRProvider


def _make_text_image(texts: list[str], size: tuple[int, int] = (400, 200)) -> np.ndarray:
    """用 PIL 画文字图（BGR ndarray）。"""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    img = Image.new("RGB", size, (30, 30, 30))  # 深色背景
    draw = ImageDraw.Draw(img)
    # 用默认字体（够大，RapidOCR 能识别）
    y = 20
    for t in texts:
        draw.text((20, y), t, fill=(255, 255, 255))  # 白色文字
        y += 50
    # PIL 是 RGB，转 BGR（与 Backend.screenshot 约定一致）
    arr = np.array(img)[:, :, ::-1].copy()
    return arr


# ============== 协议 ==============

def test_rapidocr_provider_satisfies_protocol() -> None:
    """RapidOCRProvider 应满足 OCRProvider 协议。"""
    assert isinstance(RapidOCRProvider(), OCRProvider)


def test_ocr_result_pydantic_model() -> None:
    """OCRResult 应是 pydantic 模型，字段正确。"""
    r = OCRResult(text="hello", bbox=(0.1, 0.2, 0.3, 0.4), confidence=0.9)
    assert r.text == "hello"
    assert r.bbox == (0.1, 0.2, 0.3, 0.4)
    assert r.confidence == 0.9


# ============== RapidOCR 识别（smoke test，慢，标记 slow）==============

def test_rapidocr_reads_simple_text() -> None:
    """RapidOCR 应能识别简单英文文字（HELLO）。"""
    frame = _make_text_image(["HELLO"])
    provider = RapidOCRProvider()
    results = provider.readtext(frame)
    # 至少识别到含 HELLO 的文本（RapidOCR 对大写英文识别稳定）
    texts = [r.text.upper() for r in results]
    assert any("HELLO" in t or "HELL" in t or "HEL" in t for t in texts), (
        f"未识别到 HELLO，实际: {texts}"
    )


def test_rapidocr_returns_normalized_bbox() -> None:
    """RapidOCR 返回的 bbox 应是归一化的（值在 [0,1]）。"""
    frame = _make_text_image(["TEST"])
    provider = RapidOCRProvider()
    results = provider.readtext(frame)
    for r in results:
        assert all(0.0 <= v <= 1.0 for v in r.bbox), f"bbox 非归一化: {r.bbox}"


def test_rapidocr_empty_frame_returns_empty() -> None:
    """空帧应返回空列表（不崩）。"""
    provider = RapidOCRProvider()
    assert provider.readtext(np.zeros((0, 0, 3), dtype="uint8")) == []
    assert provider.readtext(None) == []  # type: ignore[arg-type]
