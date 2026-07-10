"""报告阶段：汇聚各阶段产物为综合探索报告。"""
from __future__ import annotations

from pathlib import Path

from joker_test.pipeline.types import (
    ExecuteResult,
    ExploreConfig,
    ExploreResult,
    ReportResult,
    SolidifyResult,
)
from joker_test.reporters.explore import ExploreReport, ExploreReporter


class ReportStage:
    """汇聚综合探索报告。"""

    def __init__(self, report_dir: str | Path = "reports") -> None:
        self._reporter = ExploreReporter(output_dir=report_dir)

    def run(
        self,
        explore: ExploreResult,
        solidify: SolidifyResult | None,
        execute: ExecuteResult | None,
        config: ExploreConfig,
    ) -> ReportResult:
        coverage = {
            "explore": True,
            "solidify": solidify is not None,
            "execute": execute is not None,
        }
        report = self._build_report(explore, solidify, execute, config, coverage)
        path = self._reporter.render(report)
        summary = self._build_summary(explore, solidify, execute)
        return ReportResult(
            report_paths=[path],
            summary=summary,
            stage_coverage=coverage,
        )

    def _build_report(
        self,
        explore: ExploreResult,
        solidify: SolidifyResult | None,
        execute: ExecuteResult | None,
        config: ExploreConfig,
        coverage: dict[str, bool],
    ) -> ExploreReport:
        return ExploreReport(
            intent=config.intent,
            mode=config.mode,
            stage_coverage=coverage,
            explore_summary=(
                "; ".join(explore.explore_log) or f"探索完成（{config.mode}）"
            ),
            flow_steps_count=len(explore.flow.steps) if explore.flow else 0,
            state_map_screen_count=(
                len(explore.state_map.screens) if explore.state_map else None
            ),
            solidify_summary=(
                f"固化 {len(solidify.test_paths)} 个测试" if solidify else None
            ),
            test_count=len(solidify.test_paths) if solidify else None,
            verify_ok=solidify.verify_ok if solidify else None,
            execute_summary=(
                f"passed={execute.session.passed}, "
                f"failed={execute.session.failed}"
                if execute
                else None
            ),
            passed=execute.session.passed if execute else None,
            failed=execute.session.failed if execute else None,
            duration_s=execute.duration_s if execute else None,
            confidence_score=0.0,
        )

    def _build_summary(
        self,
        explore: ExploreResult,
        solidify: SolidifyResult | None,
        execute: ExecuteResult | None,
    ) -> str:
        parts = [f"探索: {'; '.join(explore.explore_log) or '完成'}"]
        if solidify:
            parts.append(f"固化: {len(solidify.test_paths)} 个测试")
        if execute:
            parts.append(
                f"执行: {execute.session.passed}通过/{execute.session.failed}失败"
            )
        return " | ".join(parts)
