"""PerceptionEngine 测试（CI 友好，mock OCR/LLM，真实 ImageMatcher）。

重点测三层漏斗的组合逻辑：
- match 层接入（注入 ImageMatcher + templates → matches 字段填充）
- layer_used 标记正确（ocr / match / ocr+match / +llm）
- 无 matcher 时行为同旧版（向后兼容）

OCR 和 LLM 用 mock（避免依赖真引擎），ImageMatcher 用真实实现（已在上个文件测过）。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from joker_test.perception import ImageMatcher, PerceptionEngine, PerceptionResult

# ============== 测试辅助 ==============

def _make_frame_with_block(
    color: tuple[int, int, int] = (0, 0, 255),
    size: int = 200,
) -> np.ndarray:
    """生成含一个带边框图标（模拟真实 UI）的帧，避免纯色匹配不稳定。"""
    rng = np.random.RandomState(42)
    frame = np.zeros((size, size, 3), dtype=np.uint8)
    for c in range(3):
        frame[:, :, c] = np.linspace(40, 90, size, dtype=np.uint8)[None, :]
        frame[:, :, c] += rng.randint(0, 20, (size, size), dtype=np.uint8)
    border = tuple(max(c - 80, 0) for c in color)
    x, y, w, h = 50, 50, 30, 30
    frame[y : y + h, x : x + w] = color
    frame[y : y + 2, x : x + w] = border
    frame[y + h - 2 : y + h, x : x + w] = border
    frame[y : y + h, x : x + 2] = border
    frame[y : y + h, x + w - 2 : x + w] = border
    return frame


def _make_icon_template(color: tuple[int, int, int], size: int = 30) -> np.ndarray:
    """裁一个和 _make_frame_with_block 同构的带边框图标当模板。"""
    rng = np.random.RandomState(42)
    total = size + 10
    frame = np.zeros((total, total, 3), dtype=np.uint8)
    for c in range(3):
        frame[:, :, c] = np.linspace(40, 90, total, dtype=np.uint8)[None, :]
        frame[:, :, c] += rng.randint(0, 20, (total, total), dtype=np.uint8)
    border = tuple(max(c - 80, 0) for c in color)
    frame[5 : 5 + size, 5 : 5 + size] = color
    frame[5 : 7, 5 : 5 + size] = border
    frame[5 + size - 2 : 5 + size, 5 : 5 + size] = border
    frame[5 : 5 + size, 5 : 7] = border
    frame[5 : 5 + size, 5 + size - 2 : 5 + size] = border
    return frame[5 : 5 + size, 5 : 5 + size]


def _make_ocr_mock(texts: list[str]) -> MagicMock:
    """造一个 OCR mock，readtext 返回带 .text 属性的结果列表。"""
    mock = MagicMock()
    results = []
    for t in texts:
        r = MagicMock()
        r.text = t
        results.append(r)
    mock.readtext.return_value = results
    return mock


def _make_llm_mock() -> MagicMock:
    """造一个 LLM mock，simple_converse 返回带文本 content 的消息。"""
    mock = MagicMock()
    mock.create.return_value = {
        "content": [{"type": "text", "text": '{"description": "主菜单", "elements": []}'}]
    }
    return mock


# ============== 三层组合 ==============

def test_perceive_match_layer_populates_matches() -> None:
    """注入 matcher + templates → matches 字段填充，layer_used 含 'match'。"""
    RED = (0, 0, 255)
    frame = _make_frame_with_block(RED)
    tpl = _make_icon_template(RED)

    engine = PerceptionEngine(matcher=ImageMatcher(threshold=0.8))
    result = engine.perceive(frame, templates={"red_btn": tpl})

    assert isinstance(result, PerceptionResult)
    assert len(result.matches) == 1
    assert result.matches[0].name == "red_btn"
    assert result.matches[0].confidence >= 0.8
    assert "match" in result.layer_used


def test_perceive_ocr_and_match_both_run() -> None:
    """OCR + match 都注入 → 两层都跑，layer_used='ocr+match'。"""
    RED = (0, 0, 255)
    frame = _make_frame_with_block(RED)
    tpl = _make_icon_template(RED)

    engine = PerceptionEngine(
        ocr=_make_ocr_mock(["开始游戏"]),
        matcher=ImageMatcher(threshold=0.8),
    )
    result = engine.perceive(frame, templates={"red_btn": tpl})

    assert result.texts == ["开始游戏"]
    assert len(result.matches) == 1
    assert result.layer_used == "ocr+match"


def test_perceive_no_matcher_backward_compat() -> None:
    """不注入 matcher → 行为同旧版（只 OCR），matches 为空，不崩。"""
    frame = _make_frame_with_block()
    engine = PerceptionEngine(ocr=_make_ocr_mock(["标题"]))
    result = engine.perceive(frame)

    assert result.matches == []
    assert result.texts == ["标题"]
    assert result.layer_used == "ocr"


def test_perceive_matcher_without_templates_skips_match() -> None:
    """注入 matcher 但没传 templates → 不跑 match 层（无可匹配）。"""
    frame = _make_frame_with_block()
    engine = PerceptionEngine(
        ocr=_make_ocr_mock(["标题"]),
        matcher=ImageMatcher(),
    )
    result = engine.perceive(frame)  # 没传 templates

    assert result.matches == []
    assert result.layer_used == "ocr"  # match 没跑


def test_perceive_full_three_layers() -> None:
    """三层全开：OCR + match + LLM，layer_used='ocr+match+llm'。"""
    RED = (0, 0, 255)
    frame = _make_frame_with_block(RED)
    tpl = _make_icon_template(RED)

    engine = PerceptionEngine(
        ocr=_make_ocr_mock(["标题"]),
        llm=_make_llm_mock(),
        use_llm=True,
        matcher=ImageMatcher(threshold=0.8),
    )
    result = engine.perceive(frame, templates={"red": tpl})

    assert result.texts == ["标题"]
    assert len(result.matches) == 1
    assert result.description == "主菜单"
    assert result.layer_used == "ocr+match+llm"


def test_perceive_no_layers_layer_used_none() -> None:
    """什么引擎都没注入 → layer_used='none'。"""
    frame = _make_frame_with_block()
    engine = PerceptionEngine()  # 空 engine
    result = engine.perceive(frame)

    assert result.layer_used == "none"
    assert result.texts == []
    assert result.matches == []


def test_perception_result_has_matches_field() -> None:
    """PerceptionResult 有 matches 字段（默认空列表）。"""
    r = PerceptionResult()
    assert r.matches == []
    assert isinstance(r.matches, list)


def test_perception_result_test_marker() -> None:
    """PerceptionResult 有 __test__=False，不被 pytest 收集。"""
    assert PerceptionResult.__test__ is False


def test_perceive_empty_frame_with_matcher() -> None:
    """空帧 + matcher → matches 空（ImageMatcher 内部处理空帧），不崩。"""
    engine = PerceptionEngine(matcher=ImageMatcher())
    result = engine.perceive(np.array([]), templates={"x": "fake.png"})
    assert result.matches == []
