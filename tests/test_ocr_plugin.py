"""OCRPlugin 测试：格式正确 + backend 兼容 + 空返回。"""
from __future__ import annotations

from joker_test.executor.backends.fake import FakeBackend
from joker_test.executor.base import BBox
from joker_test.plugins.ocr import OCRPlugin


def test_inject_system_prompt_has_format_explanation():
    """系统提示词包含坐标格式说明。"""
    p = OCRPlugin()
    frag = p.inject_system_prompt()
    assert "文字@" in frag
    assert "归一化" in frag or "[0,1]" in frag


def test_inject_step_with_fake_backend():
    """FakeBackend 有 OCR 文字时返回带坐标的界面元素。"""
    backend = FakeBackend(texts_map={
        "设置": BBox(0.4, 0.7, 0.2, 0.1),
        "退出": BBox(0.4, 0.8, 0.2, 0.1),
    })
    backend.connect()
    screenshot = backend.screenshot()

    p = OCRPlugin()
    result = p.inject_step(screenshot, backend, ctx=None)
    assert "设置" in result
    assert "退出" in result
    assert "@(" in result
    backend.close()


def test_inject_step_no_texts_returns_empty():
    """backend 无文字时返回空串。"""
    backend = FakeBackend()
    backend.connect()
    screenshot = backend.screenshot()

    p = OCRPlugin()
    result = p.inject_step(screenshot, backend, ctx=None)
    assert result == ""
    backend.close()


def test_inject_action_hint_always_empty():
    """OCR 插件不做动作建议。"""
    assert OCRPlugin().inject_action_hint(None, None, None) == ""


def test_validate_always_none():
    """OCR 插件不做校验。"""
    assert OCRPlugin().validate(None, None) is None


def test_name_is_ocr():
    assert OCRPlugin().name == "ocr"
