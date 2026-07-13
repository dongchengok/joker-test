"""AgenticOrchestrator 三分支测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from joker_test.executor.backends.fake import FakeBackend, ScreenCfg
from joker_test.pipeline.base import AgenticOrchestrator, build_orchestrator
from joker_test.pipeline.stages.execute import ExecuteStage
from joker_test.pipeline.stages.explore import ExploreStage
from joker_test.pipeline.stages.reflect import ReflectStage
from joker_test.pipeline.stages.report import ReportStage
from joker_test.pipeline.stages.solidify import SolidifyStage
from joker_test.pipeline.types import ExploreConfig


def _reflect_provider() -> MagicMock:
    """反思阶段需要的 LLM 回复。"""
    mock = MagicMock()
    mock.create.return_value = {
        "content": [
            {"type": "text", "text": '{"confidence": 0.7, "risks": [], "reasoning": "ok"}'}
        ]
    }
    return mock


def test_branch_reuse_hit(tmp_path: Path) -> None:
    """命中分支：config.reuse → 跳过探索+固化，直接执行。"""
    test_file = tmp_path / "test_reuse.py"
    test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    cfg = ExploreConfig(
        intent="x",
        reuse=str(test_file),
        check_reuse=False,
    )
    reflect_provider = _reflect_provider()
    orch = AgenticOrchestrator(
        explore=ExploreStage(MagicMock(), FakeBackend()),
        solidify=SolidifyStage(MagicMock(), FakeBackend(), gen_dir=tmp_path),
        execute=ExecuteStage(),
        report=ReportStage(report_dir=tmp_path),
        reflect=ReflectStage(reflect_provider),
    )
    result = orch.run(cfg)
    assert result.explore.skipped is True
    assert result.solidify is None
    assert result.execute is not None
    assert result.execute.session.passed >= 1


def test_branch_explore_only(tmp_path: Path) -> None:
    """直出分支：solidify=False → 不固化不执行。"""
    explore_provider = MagicMock()
    explore_provider.create.return_value = {
        "content": [{"type": "text", "text": '{"hit": false, "reason": "无"}'}]
    }
    cfg = ExploreConfig(
        intent="未知",
        mode="dfs",
        check_reuse=False,
        solidify=False,
        execute=False,
    )
    backend = FakeBackend(
        screens={"root": ScreenCfg(texts_map={})},
        initial_screen="root",
    )
    reflect_provider = _reflect_provider()
    orch = AgenticOrchestrator(
        explore=ExploreStage(
            explore_provider,
            backend,
            gen_dir=tmp_path / "gen",
            flow_dir=tmp_path / "flows",
        ),
        solidify=SolidifyStage(MagicMock(), backend, gen_dir=tmp_path / "gen"),
        execute=ExecuteStage(),
        report=ReportStage(report_dir=tmp_path),
        reflect=ReflectStage(reflect_provider),
    )
    result = orch.run(cfg)
    assert result.explore.skipped is False
    assert result.solidify is None
    assert result.execute is None


def test_build_orchestrator_factory(tmp_path: Path) -> None:
    """build_orchestrator 能构造完整编排器。"""
    cfg = ExploreConfig(intent="x", backend_name="fake")
    orch = build_orchestrator(cfg, report_dir=tmp_path)
    assert orch is not None
