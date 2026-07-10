"""pipeline 数据契约往返测试。"""
from __future__ import annotations

from joker_test.pipeline.types import (
    ExploreConfig,
    ExploreResult,
    PipelineResult,
    ReflectResult,
    ReportResult,
    Risk,
    SolidifyResult,
)


def test_explore_config_defaults() -> None:
    cfg = ExploreConfig(intent="进入退出游戏")
    assert cfg.mode == "llm"
    assert cfg.reuse is None
    assert cfg.check_reuse is True
    assert cfg.solidify is True
    assert cfg.execute is True
    assert cfg.backend_name == "fake"
    assert cfg.max_explore_steps == 30


def test_explore_config_mode_literal() -> None:
    cfg = ExploreConfig(intent="x", mode="manual")
    assert cfg.mode == "manual"


def test_explore_result_skipped() -> None:
    r = ExploreResult(skipped=True, reused_test_paths=["tests/generated_smoke/test_a.py"])
    assert r.flow is None
    assert r.state_map is None
    assert r.match_reason is None


def test_solidify_result_defaults() -> None:
    r = SolidifyResult(test_paths=["tests/generated_smoke/test_a.py"])
    assert r.spec_paths == []
    assert r.verify_ok is False
    assert r.verify_rounds == 0
    assert r.warnings == []


def test_risk_model() -> None:
    risk = Risk(category="uncovered_area", description="未探索设置界面", severity="P2")
    assert risk.severity == "P2"


def test_reflect_result_defaults() -> None:
    r = ReflectResult(confidence_score=0.5, reflect_reasoning="ok")
    assert r.false_positives == []
    assert r.stuck_warnings == []
    assert r.risks == []


def test_pipeline_result_optional_fields() -> None:
    e = ExploreResult(skipped=True)
    rep = ReportResult(
        report_paths=[],
        summary="s",
        stage_coverage={"explore": True, "solidify": False, "execute": False},
    )
    ref = ReflectResult(confidence_score=0.3, reflect_reasoning="low")
    p = PipelineResult(explore=e, report=rep, reflect=ref)
    assert p.solidify is None
    assert p.execute is None


def test_models_not_collected_by_pytest() -> None:
    for cls in (ExploreConfig, ExploreResult, Risk):
        assert getattr(cls, "__test__", True) is False
