"""flow 包单元测试。

测纯函数（斐波那契间隔、坐标换算、语义化、名字清洗）+ 程序化录制闭环 + generator 解析。
不依赖真实游戏（用 FakeBackend / mock provider）。

pynput 监听本身（全局钩子 + 回调冻结几何 + mss 截图）需要真人在屏幕前操作，
无法自动化测试，由用户在 Unity 上手动验证。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from joker_test.flow.namer import FlowNamer
from joker_test.flow.recorder import GlobalRecorder, _fibonacci_intervals
from joker_test.flow.semantics import semanticize_step
from joker_test.flow.types import (
    RecordedFlow,
    RecordedStep,
    WindowInfo,
)

# ============== 斐波那契式截图间隔 ==============


def test_fibonacci_starts_at_half_second() -> None:
    """操作后第一个截图间隔 = 0.5 秒。"""
    from itertools import islice  # noqa: PLC0415

    seq = list(islice(_fibonacci_intervals(max_interval=15.0), 1))
    assert seq[0] == 0.5


def test_fibonacci_pattern() -> None:
    """前几个值：0.5, 1, 1.5, 2.5, 4, 6.5, 10.5（每次=前两次之和）。"""
    seq = []
    it = _fibonacci_intervals(max_interval=15.0)
    for _ in range(7):
        seq.append(next(it))
    assert seq == [0.5, 1.0, 1.5, 2.5, 4.0, 6.5, 10.5]


def test_fibonacci_caps_at_max() -> None:
    """序列最终收敛到 max_interval（15）。"""
    it = _fibonacci_intervals(max_interval=15.0)
    seq = [next(it) for _ in range(20)]
    assert all(s <= 15.0 for s in seq)
    assert all(s == 15.0 for s in seq[-10:])


# ============== 文件名清洗 ==============


def test_sanitize_removes_illegal_chars() -> None:
    assert FlowNamer._sanitize_filename("a/b:c*d") == "a_b_c_d"


def test_sanitize_strips_whitespace() -> None:
    assert FlowNamer._sanitize_filename("  进入地牢  ") == "进入地牢"


def test_sanitize_limits_length() -> None:
    long_name = "非常长的名字" * 10
    assert len(FlowNamer._sanitize_filename(long_name)) <= 30


# ============== 坐标语义化 ==============


def test_semanticize_press_key_returns_none() -> None:
    """press_key 不需要语义化。"""
    step = RecordedStep(action="press_key", key="escape")
    result = semanticize_step(step, Path("/tmp"), ocr_provider=None)
    assert result.locator_type == "none"


def test_semanticize_click_text_already_semantic() -> None:
    """已经是 click_text 的步骤直接返回 high confidence。"""
    step = RecordedStep(action="click_text", text="进入地牢")
    result = semanticize_step(step, Path("/tmp"), ocr_provider=None)
    assert result.locator_type == "click_text"
    assert result.text == "进入地牢"
    assert result.confidence == "high"


def test_semanticize_click_without_coords(tmp_path: Path) -> None:
    """无坐标的 click 步骤无法语义化。"""
    step = RecordedStep(action="click", screenshot_after="shot.png")
    result = semanticize_step(step, tmp_path, ocr_provider=None)
    assert result.locator_type == "none"
    assert "无归一化坐标" in result.warning


def test_semanticize_click_falls_back_to_coord_without_ocr(tmp_path: Path) -> None:
    """无 OCR 的 click 步骤（纯图标界面）应回退到 click_coord 归一化坐标。

    不再用模板匹配——纯图标界面的截图含动态内容（等级/血条/时间），
    做模板第二次跑必然匹配失败。固定窗口+分辨率下坐标更可靠。
    """
    import cv2  # noqa: PLC0415

    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    frame[50:150, 50:150] = 255
    shot_path = tmp_path / "shot.png"
    cv2.imwrite(str(shot_path), frame)

    step = RecordedStep(action="click", x=0.5, y=0.5, screenshot_after="shot.png")
    result = semanticize_step(step, tmp_path, ocr_provider=None)
    assert result.locator_type == "click_coord"
    assert result.x == 0.5
    assert result.y == 0.5
    assert result.confidence == "high"
    # 不应生成模板文件
    assert not (tmp_path / "templates").exists()


def test_semanticize_click_no_screenshot_uses_coord(tmp_path: Path) -> None:
    """无截图的 click 步骤无法做 OCR，但仍可回退到 click_coord。"""
    step = RecordedStep(action="click", x=0.3, y=0.7)
    result = semanticize_step(step, tmp_path, ocr_provider=None)
    assert result.locator_type == "click_coord"
    assert result.x == 0.3
    assert result.y == 0.7


# ============== 程序化录制闭环 ==============


def test_record_action_collects_steps(tmp_path: Path) -> None:
    """record_action 应累积 RecordedStep。"""
    recorder = GlobalRecorder(output_dir=tmp_path, backend=None, pynput_mode=False)
    recorder.record_action("click_text", text="开始", x=0.5, y=0.3, note="点开始")
    recorder.record_action("press_key", key="escape")
    recorder.record_action("type_text", text="hello")

    flow = recorder.stop()
    assert len(flow.steps) == 3
    assert flow.steps[0].action == "click_text"
    assert flow.steps[0].text == "开始"
    assert flow.steps[1].action == "press_key"
    assert flow.steps[1].key == "escape"
    assert flow.steps[2].action == "type_text"
    assert flow.steps[2].text == "hello"


def test_record_elapsed_s_increases(tmp_path: Path) -> None:
    """elapsed_s 应随时间增加。"""
    import time  # noqa: PLC0415

    recorder = GlobalRecorder(output_dir=tmp_path, pynput_mode=False)
    recorder.record_action("click_text", text="a")
    time.sleep(0.1)
    recorder.record_action("click_text", text="b")

    flow = recorder.stop()
    assert flow.steps[1].elapsed_s > flow.steps[0].elapsed_s


def test_save_flow_yaml_round_trip(tmp_path: Path) -> None:
    """save_flow_yaml 写出可读 yaml，能反序列化回来。"""
    import yaml  # noqa: PLC0415

    recorder = GlobalRecorder(output_dir=tmp_path, pynput_mode=False)
    recorder.record_action("press_key", key="enter")
    flow = recorder.stop()

    yaml_path = recorder.save_flow_yaml(flow)
    assert yaml_path.exists()
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert data["steps"][0]["action"] == "press_key"
    assert data["steps"][0]["key"] == "enter"


def test_flow_dir_is_temp_during_recording(tmp_path: Path) -> None:
    """录制中目录是 .tmp_ 前缀（等 namer rename）。"""
    recorder = GlobalRecorder(output_dir=tmp_path, pynput_mode=False)
    assert recorder.flow_dir.name.startswith(".tmp_")
    assert recorder.flow_dir.is_dir()
    recorder.stop()


# ============== RecordedFlow 序列化 ==============


def test_recorded_flow_round_trip() -> None:
    """model_dump → model_validate 往返保真。"""
    flow = RecordedFlow(
        name="测试流程",
        description="进入地牢",
        steps=[
            RecordedStep(action="click_text", text="进入地牢", x=0.5, y=0.8),
            RecordedStep(
                action="click",
                x=0.3,
                y=0.4,
                screen_x=300,
                screen_y=400,
                window=WindowInfo(
                    hwnd=123,
                    title="游戏",
                    rect=(0, 0, 1000, 800),
                    width=1000,
                    height=800,
                ),
            ),
        ],
    )
    data = flow.model_dump()
    restored = RecordedFlow(**data)
    assert restored.name == "测试流程"
    assert len(restored.steps) == 2
    assert restored.steps[0].text == "进入地牢"
    assert restored.steps[1].window.hwnd == 123
    assert restored.steps[1].window.title == "游戏"


# ============== FlowNamer（mock provider） ==============


def test_namer_parses_llm_reply() -> None:
    """LLM 返回 JSON 格式的名字，能正确解析。"""
    mock_provider = MagicMock()
    mock_provider.create.return_value = {
        "content": [
            {"type": "text", "text": '{"name": "进入地牢流程", "description": "测试主菜单到角色选择"}'}
        ]
    }
    namer = FlowNamer(mock_provider)
    flow = RecordedFlow(
        name="20260708_153000",
        steps=[RecordedStep(action="click_text", text="进入地牢")],
    )

    name, desc = namer.name_flow(flow, screenshots_dir=None)
    assert name == "进入地牢流程"
    assert "角色选择" in desc


def test_namer_fallback_on_failure() -> None:
    """LLM 失败时回退到时间戳名。"""
    mock_provider = MagicMock()
    mock_provider.create.side_effect = RuntimeError("network error")
    namer = FlowNamer(mock_provider)
    flow = RecordedFlow(name="20260708_153000", steps=[])

    name, desc = namer.name_flow(flow, screenshots_dir=None)
    assert name == "20260708_153000"
    assert desc == ""


def test_namer_parses_code_block_json() -> None:
    """LLM 用 ```json 包裹的回复也能解析。"""
    mock_provider = MagicMock()
    mock_provider.create.return_value = {
        "content": [
            {
                "type": "text",
                "text": '好的\n```json\n{"name": "购买装备", "description": "商店流程"}\n```\n',
            }
        ]
    }
    namer = FlowNamer(mock_provider)
    flow = RecordedFlow(name="tmp", steps=[])

    name, desc = namer.name_flow(flow, screenshots_dir=None)
    assert name == "购买装备"


# ============== RecordedFlowGenerator（mock provider） ==============


def test_generator_parses_generated_code(tmp_path: Path) -> None:
    """mock provider 返回 pytest 代码，能解析成 GeneratedTest。"""
    from joker_test.flow.generator import RecordedFlowGenerator  # noqa: PLC0415
    from joker_test.generator.types import GeneratedTest  # noqa: PLC0415

    mock_provider = MagicMock()
    mock_provider.create.return_value = {
        "content": [
            {
                "type": "text",
                "text": (
                    "### test_menu.py\n```python\n"
                    '"""主菜单测试。"""\n'
                    "from joker_test.executor.base import ExecutorBackend\n\n\n"
                    "def test_menu(backend: ExecutorBackend) -> None:\n"
                    '    assert any("菜单" in t for t in backend.state.texts)\n'
                    "```\n"
                    "### menu_spec.py\n"
                    "```python\nfrom pydantic import BaseModel\n\n"
                    "class MenuSpec(BaseModel):\n    name: str = ''\n```\n"
                ),
            }
        ]
    }

    import cv2  # noqa: PLC0415

    shot = tmp_path / "shot.png"
    cv2.imwrite(str(shot), np.zeros((100, 100, 3), dtype=np.uint8))

    flow = RecordedFlow(
        name="test",
        steps=[
            RecordedStep(
                action="click_text",
                text="进入",
                screenshot_after="shot.png",
            ),
        ],
    )

    gen = RecordedFlowGenerator(provider=mock_provider, ocr_provider=None)
    tests = gen.generate(flow, tmp_path, {"game_name": "测试游戏", "overview": ""})

    assert len(tests) == 1
    assert isinstance(tests[0], GeneratedTest)
    assert tests[0].test_filename == "test_menu.py"
    assert "test_menu" in tests[0].test_code
    assert tests[0].spec_filename == "menu_spec.py"


# ============== prompt 模板渲染 ==============


def test_render_prompt_with_warnings() -> None:
    """带警告的渲染不报错。"""
    from joker_test.prompts import render_recorded_flow_prompt  # noqa: PLC0415

    flow_json = '{"name": "test", "steps": [{"action": "click_text", "text": "开始"}]}'
    game_meta = {"game_name": "测试游戏", "overview": "测试"}
    prompt = render_recorded_flow_prompt(flow_json, game_meta, warnings=["坐标冲突"])
    assert "测试游戏" in prompt
    assert "开始" in prompt
    assert "坐标冲突" in prompt


def test_render_prompt_without_warnings() -> None:
    """无警告的渲染不报错（warnings 段不出现）。"""
    from joker_test.prompts import render_recorded_flow_prompt  # noqa: PLC0415

    flow_json = '{"name": "test", "steps": []}'
    prompt = render_recorded_flow_prompt(flow_json, {"game_name": "游戏"}, [])
    assert "游戏" in prompt
    assert "<warnings>" not in prompt
