"""AgentToolExecutor 工具分发与结果结构测试（FakeBackend）。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from joker_test.executor.backends.fake import FakeBackend, ScreenCfg
from joker_test.executor.base import BBox
from joker_test.explorer.agent_tools import AGENT_TOOL_SCHEMAS, AgentToolExecutor


def _make_backend() -> FakeBackend:
    backend = FakeBackend(texts_map={"设置": BBox(0.4, 0.4, 0.2, 0.1)})
    backend.connect()
    return backend


def _parse_result(out: dict) -> dict:
    """从 execute 返回里解析动作结果 JSON 文本（第一个 text block）。"""
    blocks = out["tool_result_content"]
    assert blocks and blocks[0]["type"] == "text"
    return json.loads(blocks[0]["text"])


def test_schemas_cover_twelve_tools() -> None:
    """12 个工具 schema 齐全且为 Anthropic 格式。"""
    names = {t["name"] for t in AGENT_TOOL_SCHEMAS}
    assert names == {
        "get_screenshot", "get_ocr_text", "click", "click_text", "click_candidate",
        "press_key", "swipe", "long_press", "back", "finish", "match_icon",
        "inspect_region",
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
    """get_ocr_text 返回带候选编号的 JSON：candidates[{text,x,y,index}] + 提示。"""
    backend = _make_backend()
    ex = AgentToolExecutor(backend)
    out = ex.execute("get_ocr_text", {})
    assert out["executed_action"] is None
    payload = json.loads(out["tool_result_content"][0]["text"])
    assert payload["candidates"] == [{"text": "设置", "x": 0.5, "y": 0.45, "index": 0}]
    assert payload["no_position_texts"] == []
    assert "click_candidate" in payload["note"]


def test_click_candidate_from_ocr() -> None:
    """候选制：get_ocr_text 登记候选 → click_candidate(0) 按 bbox 中心精确点击。"""
    backend = _make_backend()
    ex = AgentToolExecutor(backend)
    ex.execute("get_ocr_text", {})
    out = ex.execute("click_candidate", {"index": 0})
    result = _parse_result(out)
    assert result["success"] is True
    assert result["candidate"] == {"index": 0, "kind": "text", "label": "设置"}
    assert backend.click_history == [("click", (0.5, 0.45))]


def test_click_candidate_without_perception() -> None:
    """未感知就 click_candidate → 报错提示先感知，不点击。"""
    backend = _make_backend()
    ex = AgentToolExecutor(backend)
    result = _parse_result(ex.execute("click_candidate", {"index": 0}))
    assert result["success"] is False
    assert "先调 get_ocr_text 或 inspect_region" in result["error"]
    assert backend.click_history == []


def test_click_candidate_out_of_range_and_stale() -> None:
    """候选编号越界报错；点击后候选清空（界面已变旧编号失效）。"""
    backend = _make_backend()
    ex = AgentToolExecutor(backend)
    ex.execute("get_ocr_text", {})
    result = _parse_result(ex.execute("click_candidate", {"index": 5}))
    assert result["success"] is False
    assert "越界" in result["error"]
    # 正常点击一次后候选被清空
    assert _parse_result(ex.execute("click_candidate", {"index": 0}))["success"] is True
    stale = _parse_result(ex.execute("click_candidate", {"index": 0}))
    assert stale["success"] is False
    assert "先调 get_ocr_text 或 inspect_region" in stale["error"]


def test_click_candidate_from_inspect_region() -> None:
    """inspect_region 的组件也登记为候选，click_candidate 按组件中心点击。"""
    import numpy as np

    frame = np.zeros((200, 400, 3), dtype=np.uint8)
    frame[50:90, 100:140] = 255  # 白块中心 (0.3, 0.35)
    backend = _make_backend()
    backend.screenshot = lambda: frame.copy()  # type: ignore[method-assign]
    ex = AgentToolExecutor(backend)

    ex.execute("inspect_region", {})
    out = ex.execute("click_candidate", {"index": 0})
    result = _parse_result(out)
    assert result["success"] is True
    assert result["candidate"]["kind"] == "component"
    _, (cx, cy) = backend.click_history[0]
    assert abs(cx - 0.3) < 0.03 and abs(cy - 0.35) < 0.03


def test_repeat_hard_block_after_threshold() -> None:
    """重复硬阻断：同一动作+参数连续无变化达 3 次后拒绝执行。"""
    backend = _make_backend()
    ex = AgentToolExecutor(backend)
    for _ in range(3):
        r = _parse_result(ex.execute("press_key", {"key": "tab"}))
        assert r["success"] is True
        assert "blocked" not in r
    blocked = _parse_result(ex.execute("press_key", {"key": "tab"}))
    assert blocked["success"] is False
    assert blocked["blocked"] is True
    assert "重复硬阻断" in blocked["error"]
    # 不同参数不受影响
    other = _parse_result(ex.execute("press_key", {"key": "enter"}))
    assert other["success"] is True
    assert backend.key_history == ["tab", "tab", "tab", "enter"]


def test_repeat_counter_reset_on_change() -> None:
    """动作产生界面变化后重复计数清零（有效操作不会被误阻断）。"""
    backend = FakeBackend(
        screens={
            "root": ScreenCfg(texts_map={"设置": BBox(0.4, 0.4, 0.2, 0.1)}, bg_pixel=(50, 50, 50)),
            "settings": ScreenCfg(bg_pixel=(200, 0, 0)),
        },
        transitions={("root", "设置"): "settings"},
        initial_screen="root",
    )
    backend.connect()
    ex = AgentToolExecutor(backend)
    # 第一次 click_text 切屏（changed=True），之后再点两次无变化也不会到阈值
    assert _parse_result(ex.execute("click_text", {"text": "设置"}))["screen_changed"] is True
    assert _parse_result(ex.execute("click_text", {"text": "设置"}))["success"] is False
    # 换 press_key 重复 3 次后阻断仍生效（计数互不影响）
    for _ in range(3):
        _parse_result(ex.execute("press_key", {"key": "x"}))
    assert _parse_result(ex.execute("press_key", {"key": "x"}))["blocked"] is True


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


def test_click_missed_gets_marker_image() -> None:
    """click 未产生界面变化时，结果附红叉标记截图块（闭环修正反馈）。"""
    backend = _make_backend()
    ex = AgentToolExecutor(backend)
    out = ex.execute("click", {"x": 0.5, "y": 0.5})
    blocks = out["tool_result_content"]
    types = [b["type"] for b in blocks]
    assert types == ["text", "text", "image"]
    assert "实际点击位置" in blocks[1]["text"]
    assert blocks[2]["source"]["media_type"] == "image/png"


def test_click_changed_no_marker_image() -> None:
    """click 产生界面变化时不附标记图（命中无需修正）。"""
    backend = FakeBackend(
        screens={
            "root": ScreenCfg(texts_map={"设置": BBox(0.4, 0.4, 0.2, 0.1)}, bg_pixel=(50, 50, 50)),
            "settings": ScreenCfg(bg_pixel=(200, 0, 0)),
        },
        transitions={("root", "设置"): "settings"},
        initial_screen="root",
    )
    backend.connect()
    ex = AgentToolExecutor(backend)
    out = ex.execute("click_text", {"text": "设置"})
    types = [b["type"] for b in out["tool_result_content"]]
    assert types == ["text"]


def test_snap_to_feature_snaps_to_blob() -> None:
    """click 附近有显著块时吸附到其中心；无特征时保持原坐标。"""
    import numpy as np

    frame = np.zeros((200, 400, 3), dtype=np.uint8)
    frame[50:90, 100:140] = 255  # 40x40 白块，中心 (0.3, 0.35)

    backend = _make_backend()
    backend.screenshot = lambda: frame.copy()  # type: ignore[method-assign]
    ex = AgentToolExecutor(backend)

    ex.execute("click", {"x": 0.35, "y": 0.4})  # 偏离块中心但在半径内
    _, (cx, cy) = backend.click_history[0]
    assert abs(cx - 0.3) < 0.03 and abs(cy - 0.35) < 0.03

    frame2 = np.zeros((200, 400, 3), dtype=np.uint8)
    backend.screenshot = lambda: frame2.copy()  # type: ignore[method-assign]
    ex.execute("click", {"x": 0.5, "y": 0.5})
    assert backend.click_history[1] == ("click", (0.5, 0.5))


def test_inspect_region_reports_blob() -> None:
    """inspect_region：有组件的区域返回组件坐标；空区域返回证伪 note。"""
    import numpy as np

    frame = np.zeros((200, 400, 3), dtype=np.uint8)
    frame[50:90, 100:140] = 255  # 白块中心 (0.3, 0.35)

    backend = _make_backend()
    backend.screenshot = lambda: frame.copy()  # type: ignore[method-assign]
    ex = AgentToolExecutor(backend)

    out = ex.execute("inspect_region", {"x": 0.2, "y": 0.2, "w": 0.3, "h": 0.3})
    assert out["executed_action"] is None
    report = json.loads(out["tool_result_content"][0]["text"])
    assert len(report["components"]) == 1
    assert abs(report["components"][0]["x"] - 0.3) < 0.03
    assert abs(report["components"][0]["y"] - 0.35) < 0.03

    empty = json.loads(
        ex.execute("inspect_region", {"x": 0.7, "y": 0.7, "w": 0.2, "h": 0.2})
        ["tool_result_content"][0]["text"]
    )
    assert empty["components"] == []
    assert "未检测到" in empty["note"]


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


def test_front_screen_guard_blocks_escape() -> None:
    """前置界面（标题/选角）按 escape/back 被硬拦截，不杀游戏。"""
    backend = FakeBackend(
        texts_map={"进入地牢": BBox(0.5, 0.8, 0.2, 0.1), "PIXELDUNGEON": BBox(0.5, 0.2, 0.4, 0.1)}
    )
    backend.connect()
    ex = AgentToolExecutor(backend)

    for name, inp in (("back", {}), ("press_key", {"key": "escape"})):
        r = _parse_result(ex.execute(name, inp))
        assert r["success"] is False, name
        assert r["blocked"] is True
        assert "前置界面保护" in r["error"]
    # escape 被拦截，没有真的按键
    assert backend.key_history == []
    # 非 escape 键不受影响
    assert _parse_result(ex.execute("press_key", {"key": "enter"}))["success"] is True
    assert backend.key_history == ["enter"]


def test_front_screen_guard_allows_escape_in_game() -> None:
    """对局内（无前置界面特征）escape 正常放行。"""
    backend = FakeBackend(texts_map={"背包": BBox(0.5, 0.5, 0.1, 0.1), "日志": BBox(0.2, 0.9, 0.1, 0.1)})
    backend.connect()
    ex = AgentToolExecutor(backend)
    r = _parse_result(ex.execute("back", {}))
    assert r["success"] is True
    assert backend.key_history == ["escape"]
