"""SolidifyStage 测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from joker_test.executor.backends.fake import FakeBackend
from joker_test.flow.types import RecordedFlow, RecordedStep
from joker_test.pipeline.stages.solidify import SolidifyStage
from joker_test.pipeline.types import ExploreConfig


def _make_flow() -> RecordedFlow:
    """构造最小 RecordedFlow。"""
    return RecordedFlow(
        name="test_flow",
        steps=[
            RecordedStep(action="click_text", text="开始", note="点开始"),
        ],
    )


def test_solidify_generates_tests(tmp_path: Path) -> None:
    """固化阶段调 generator 产 test_case。"""
    mock_provider = MagicMock()
    mock_provider.simple_converse.return_value = {
        "content": [
            {
                "type": "text",
                "text": (
                    "### test_demo.py\n"
                    "```python\n"
                    "def test_demo():\n"
                    "    assert True\n"
                    "```\n"
                ),
            }
        ]
    }
    flow = _make_flow()
    flow_dir = tmp_path / "flow_dir"
    flow_dir.mkdir()
    gen_dir = tmp_path / "gen"
    stage = SolidifyStage(
        provider=mock_provider,
        backend=FakeBackend(),
        gen_dir=gen_dir,
    )
    cfg = ExploreConfig(intent="demo", verify_during_solidify=False)
    result = stage.run(flow, flow_dir, cfg)
    assert len(result.test_paths) >= 1
    assert all(Path(p).exists() for p in result.test_paths)


def test_solidify_skips_verify_when_disabled(tmp_path: Path) -> None:
    """verify_during_solidify=False → 不试跑。"""
    mock_provider = MagicMock()
    mock_provider.simple_converse.return_value = {
        "content": [
            {
                "type": "text",
                "text": (
                    "### test_skip.py\n"
                    "```python\n"
                    "def test_s():\n"
                    "    assert True\n"
                    "```\n"
                ),
            }
        ]
    }
    flow = _make_flow()
    flow_dir = tmp_path / "flow_dir"
    flow_dir.mkdir()
    stage = SolidifyStage(
        provider=mock_provider, backend=FakeBackend(), gen_dir=tmp_path / "gen"
    )
    cfg = ExploreConfig(intent="x", verify_during_solidify=False)
    result = stage.run(flow, flow_dir, cfg)
    assert result.verify_ok is False
    assert result.verify_rounds == 0
