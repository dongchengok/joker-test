"""ExploreReporter：消费 ExploreReport 输出 JSON。

综合探索报告数据模型，由 ReportStage 聚合各阶段产物。
"""
from __future__ import annotations

import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from joker_test.pipeline.types import Risk


class ExploreReport(BaseModel):
    """综合探索报告数据模型。"""

    __test__ = False
    intent: str
    mode: str
    stage_coverage: dict[str, bool]
    explore_summary: str = ""
    flow_steps_count: int = 0
    state_map_screen_count: int | None = None
    solidify_summary: str | None = None
    test_count: int | None = None
    verify_ok: bool | None = None
    execute_summary: str | None = None
    passed: int | None = None
    failed: int | None = None
    duration_s: float | None = None
    confidence_score: float = 0.0
    risks: list[Risk] = Field(default_factory=list)


class ExploreReporter:
    """输出综合探索报告 JSON。"""

    name = "explore"

    def __init__(self, output_dir: str | Path = "reports") -> None:
        self._output_dir = Path(output_dir)

    def render(self, report: ExploreReport) -> str:
        """渲染报告到 JSON 文件，返回路径。"""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._output_dir / f"explore_report_{ts}.json"
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return str(path)
