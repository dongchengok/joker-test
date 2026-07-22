"""AgentToolExecutor 工具分发与结果结构测试（FakeBackend）。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from joker_test.executor.backends.fake import FakeBackend
from joker_test.executor.base import BBox
from joker_test.explorer.agent_tools import AGENT_TOOL_SCHEMAS, AgentToolExecutor


def _make_backend() -> FakeBackend:
    backend = FakeBackend(texts_map={"设置": BBox(0.4, 0.4, 0.2, 0.1)})
    backend.connect()
    return backend


def _parse_result(out: dict) -> dict:
    """从 execute 返回里解析动作结果 JSON 文本。"""
    blocks = out["tool_result_content"]
    assert len(blocks) == 1 and blocks[0]["type"] == "text"
    return json.loads(blocks[0]["text"])


def test_schemas_cover_ten_tools() -> None:
    """10 个工具 schema 齐全且为 Anthropic 格式。"""
    names = {t["name"] for t in AGENT_TOOL_SCHEMAS}
    assert names == {
        "get_screenshot", "get_ocr_text", "click", "click_text",
        "press_key", "swipe", "long_press", "back", "finish", "match_icon",
    }
    for t in AGENT_TOOL_SCHEMAS:
        assert "description" in t and "input_schema" in t


def test_click_executes_and_result_structure() -> None:
    """click 分发到 backend，结果含 success/screen_changed/ocr_after 等键。"""
    backend = _make_backend()
    ex = AgentToolExecutor(backend)
    out = ex.execute("click", {"x": 0.5, "y": 0.5})
    assert backend.click_history == [("click", (0.5, 0.5))]

    result = _parse_result(out)
    assert result["success"] is True
    assert result["screen_changed"] is False  # 单屏 FakeBackend 背景不变
    assert result["error"] is None
    assert "pixel_diff_ratio" in result
    assert result["ocr_after"] == ["设置"]

    assert out["executed_action"] == {
        "tool": "click", "success": True, "screen_changed": False,
    }


def test_click_text_hit_and_miss() -> None:
    """click_text 命中走 backend.click_text；未命中 success=False 带错误信息。"""
    backend = _make_backend()
    ex = AgentToolExecutor(backend)

    hit = _parse_result(ex.execute("click_text", {"text": "设置"}))
    assert hit["success"] is True
    assert ("click_text", "设置") in backend.click_history

    miss = _parse_result(ex.execute("click_text", {"text": "不存在"}))
    assert miss["success"] is False
    assert "文本未找到" in miss["error"]


def test_press_key_and_back() -> None:
    """press_key / back 都走 backend.press_key（back = escape）。"""
    backend = _make_backend()
    ex = AgentToolExecutor(backend)
    assert _parse_result(ex.execute("press_key", {"key": "enter"}))["success"] is True
    assert _parse_result(ex.execute("back", {}))["success"] is True
    assert backend.key_history == ["enter", "escape"]


def test_swipe_and_long_press() -> None:
    """swipe / long_press 分发到 backend，参数透传。"""
    backend = _make_backend()
    ex = AgentToolExecutor(backend)
    assert _parse_result(
        ex.execute("swipe", {"x1": 0.7, "y1": 0.5, "x2": 0.35, "y2": 0.5})
    )["success"] is True
    assert _parse_result(
        ex.execute("long_press", {"x": 0.5, "y": 0.5, "duration": 3.0})
    )["success"] is True
    assert backend.swipe_history == [("swipe", (0.7, 0.5, 0.35, 0.5, 0.5))]
    assert backend.long_press_history == [("long_press", (0.5, 0.5, 3.0))]


def test_get_ocr_text_format() -> None:
    """get_ocr_text 返回 [{text, x, y}]，坐标为 bbox 中心（归一化）。"""
    backend = _make_backend()
    ex = AgentToolExecutor(backend)
    out = ex.execute("get_ocr_text", {})
    assert out["executed_action"] is None
    items = json.loads(out["tool_result_content"][0]["text"])
    assert items == [{"text": "设置", "x": 0.5, "y": 0.45}]


def test_get_screenshot_returns_text_and_image_blocks() -> None:
    """get_screenshot 返回 text 简述 + base64 png image 块。"""
    backend = _make_backend()
    ex = AgentToolExecutor(backend)
    out = ex.execute("get_screenshot", {})
    assert out["executed_action"] is None
    blocks = out["tool_result_content"]
    assert blocks[0]["type"] == "text"
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"]["media_type"] == "image/png"
    assert len(blocks[1]["source"]["data"]) > 0


def test_get_screenshot_region_crop() -> None:
    """get_screenshot 带 region 参数时裁剪对应区域（图像尺寸与区域一致）。"""
    import base64

    backend = _make_backend()
    frame = backend.screenshot()
    fh, fw = frame.shape[:2]
    ex = AgentToolExecutor(backend)
    out = ex.execute("get_screenshot", {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.25})
    blocks = out["tool_result_content"]
    assert "已裁剪到区域" in blocks[0]["text"]
    import cv2
    import numpy as np

    img = cv2.imdecode(
        np.frombuffer(base64.b64decode(blocks[1]["source"]["data"]), dtype=np.uint8),
        cv2.IMREAD_COLOR,
    )
    assert img.shape[0] == round(fh * 0.25)
    assert img.shape[1] == round(fw * 0.5)


def test_match_icon_locates_and_clicks_true_position() -> None:
    """match_icon：给偏一点的区域内相对位置，模板匹配纠正到图标真实位置点击。"""
    import numpy as np

    # 200x400 黑帧，20x20 白块在 (col 100-120, row 50-70)，真实中心 (0.275, 0.3)
    frame = np.zeros((200, 400, 3), dtype=np.uint8)
    frame[50:70, 100:120] = 255

    backend = _make_backend()
    backend.screenshot = lambda: frame.copy()  # type: ignore[method-assign]
    ex = AgentToolExecutor(backend)

    # 区域 (0.2,0.15,0.3x0.4) 覆盖白块；相对位置故意偏一点 (0.35,0.45)
    out = ex.execute("match_icon", {
        "x": 0.2, "y": 0.15, "w": 0.3, "h": 0.4, "px": 0.35, "py": 0.45,
    })
    result = _parse_result(out)
    assert result["success"] is True, result
    assert result["match_score"] > 0.9
    assert abs(result["matched_x"] - 0.275) < 0.05
    assert abs(result["matched_y"] - 0.3) < 0.05
    assert len(backend.click_history) == 1
    _, (cx, cy) = backend.click_history[0]
    assert abs(cx - 0.275) < 0.05 and abs(cy - 0.3) < 0.05


def test_match_icon_low_score_no_click() -> None:
    """区域内无图标（匹配不到）→ 不点击并报错。"""
    import numpy as np

    frame = np.zeros((200, 400, 3), dtype=np.uint8)
    backend = _make_backend()
    backend.screenshot = lambda: frame.copy()  # type: ignore[method-assign]
    ex = AgentToolExecutor(backend)

    result = _parse_result(ex.execute("match_icon", {
        "x": 0.2, "y": 0.15, "w": 0.3, "h": 0.4, "px": 0.5, "py": 0.5,
    }))
    assert result["success"] is False
    assert "无特征" in result["error"]
    assert backend.click_history == []


def test_unknown_tool_returns_error_not_raise() -> None:
    """未知工具名不抛异常，返回 success=False + error。"""
    backend = _make_backend()
    ex = AgentToolExecutor(backend)
    result = _parse_result(ex.execute("nope", {}))
    assert result["success"] is False
    assert "未知工具" in result["error"]


def test_backend_exception_wrapped_not_raised() -> None:
    """backend 抛异常（如未 connect）时不抛出，包进 error。"""
    backend = FakeBackend(texts_map={})  # 未 connect，screenshot 会 RuntimeError
    ex = AgentToolExecutor(backend)
    result = _parse_result(ex.execute("click", {"x": 0.5, "y": 0.5}))
    assert result["success"] is False
    assert result["error"]


def test_recorder_sync() -> None:
    """有 recorder 时动作同步 record_action（映射参考 LLMExplorer._record）。"""
    backend = _make_backend()
    recorder = MagicMock()
    ex = AgentToolExecutor(backend, recorder=recorder)
    ex.execute("click_text", {"text": "设置"})
    ex.execute("press_key", {"key": "enter"})
    ex.execute("back", {})
    recorder.record_action.assert_any_call("click_text", text="设置")
    recorder.record_action.assert_any_call("press_key", key="enter")
    recorder.record_action.assert_any_call("press_key", key="escape")
