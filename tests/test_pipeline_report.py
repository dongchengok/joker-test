"""ReportStage 测试。"""
from __future__ import annotations

import json
from pathlib import Path

from joker_test.pipeline.stages.report import ReportStage
from joker_test.pipeline.types import ExploreConfig, ExploreResult
from joker_test.reporters.explore import ExploreReport, ExploreReporter


def test_report_pure_explore(tmp_path: Path) -> None:
    """纯探索（无固化/执行）出报告。"""
    stage = ReportStage(report_dir=tmp_path)
    explore = ExploreResult(skipped=False, explore_log=["llm 探索 3 屏"])
    cfg = ExploreConfig(intent="测试登录", mode="llm")
    result = stage.run(explore=explore, solidify=None, execute=None, config=cfg)
    assert len(result.report_paths) >= 1
    assert result.stage_coverage["explore"] is True
    assert result.stage_coverage["solidify"] is False
    assert "探索" in result.summary


def test_report_with_skipped_explore(tmp_path: Path) -> None:
    """命中复用时 stage_coverage 标记 explore=True。"""
    stage = ReportStage(report_dir=tmp_path)
    explore = ExploreResult(skipped=True, reused_test_paths=["test_a.py"])
    cfg = ExploreConfig(intent="x")
    result = stage.run(explore=explore, solidify=None, execute=None, config=cfg)
    assert result.stage_coverage["explore"] is True


def test_explore_report_serialization(tmp_path: Path) -> None:
    """ExploreReporter 能输出 JSON 文件。"""
    reporter = ExploreReporter(output_dir=tmp_path)
    report = ExploreReport(
        intent="登录测试",
        mode="llm",
        stage_coverage={"explore": True, "solidify": False, "execute": False},
        explore_summary="探索了 3 个界面",
        confidence_score=0.8,
        risks=[],
    )
    path = reporter.render(report)
    assert Path(path).exists()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["intent"] == "登录测试"
