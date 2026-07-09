"""ReflectStage 测试。"""
from __future__ import annotations

from unittest.mock import MagicMock

from joker_test.pipeline.stages.reflect import ReflectStage
from joker_test.pipeline.types import (
    ExploreConfig,
    ExploreResult,
    ReportResult,
)


def test_reflect_with_explore_only() -> None:
    """有探索结果时反思覆盖可信度+风险。"""
    mock_provider = MagicMock()
    mock_provider.simple_converse.return_value = {
        "content": [
            {
                "type": "text",
                "text": (
                    '{"confidence": 0.8, "risks": '
                    '[{"category": "low_confidence", "description": "断言较弱", "severity": "P2"}], '
                    '"reasoning": "基本可信"}'
                ),
            }
        ]
    }
    stage = ReflectStage(provider=mock_provider)
    explore = ExploreResult(skipped=False, explore_log=["dfs 探索 3 屏"])
    report = ReportResult(
        report_paths=[],
        summary="s",
        stage_coverage={"explore": True, "solidify": False, "execute": False},
    )
    cfg = ExploreConfig(intent="x")
    result = stage.run(explore=explore, execute=None, report=report, config=cfg)
    assert 0.0 <= result.confidence_score <= 1.0
    assert len(result.risks) >= 1


def test_reflect_llm_failure_degrades_to_low_confidence() -> None:
    """LLM 失败时降级为低可信度，不阻断。"""
    mock_provider = MagicMock()
    mock_provider.simple_converse.side_effect = RuntimeError("network down")
    stage = ReflectStage(provider=mock_provider)
    explore = ExploreResult(skipped=False)
    report = ReportResult(
        report_paths=[],
        summary="",
        stage_coverage={"explore": True, "solidify": False, "execute": False},
    )
    cfg = ExploreConfig(intent="x")
    result = stage.run(explore=explore, execute=None, report=report, config=cfg)
    assert result.confidence_score == 0.3
    assert "降级" in result.reflect_reasoning


def test_reflect_no_crash_on_empty_log() -> None:
    """反思阶段空 explore_log 不崩溃。"""
    mock_provider = MagicMock()
    mock_provider.simple_converse.return_value = {
        "content": [
            {"type": "text", "text": '{"confidence": 0.7, "risks": [], "reasoning": "ok"}'}
        ]
    }
    stage = ReflectStage(provider=mock_provider)
    explore = ExploreResult(
        skipped=False, explore_log=["step1", "step2", "step3", "step3", "step3"]
    )
    report = ReportResult(
        report_paths=[],
        summary="",
        stage_coverage={"explore": True, "solidify": False, "execute": False},
    )
    cfg = ExploreConfig(intent="x")
    result = stage.run(explore=explore, execute=None, report=report, config=cfg)
    assert result.reflect_reasoning != ""
