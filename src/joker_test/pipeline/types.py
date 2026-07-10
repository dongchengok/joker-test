"""流水线数据契约。

所有模型设 __test__ = False 防止 pytest 误收集。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from joker_test.explorer.types import StateMap
from joker_test.flow.types import RecordedFlow
from joker_test.reporters.base import TestSession


class ExploreConfig(BaseModel):
    """流水线统一配置。"""

    __test__ = False
    intent: str
    mode: Literal["manual", "dfs", "llm"] = "llm"
    reuse: str | None = None
    check_reuse: bool = True
    solidify: bool = True
    execute: bool = True
    game_name: str = ""
    backend_name: str = "fake"
    max_explore_steps: int = 30
    verify_during_solidify: bool = True


class ExploreResult(BaseModel):
    """探索阶段产出。"""

    __test__ = False
    flow: RecordedFlow | None = None
    flow_dir: str | None = None
    state_map: StateMap | None = None
    skipped: bool = False
    reused_test_paths: list[str] = Field(default_factory=list)
    match_reason: str | None = None
    explore_log: list[str] = Field(default_factory=list)


class SolidifyResult(BaseModel):
    """固化阶段产出。"""

    __test__ = False
    test_paths: list[str] = Field(default_factory=list)
    spec_paths: list[str] = Field(default_factory=list)
    verify_ok: bool = False
    verify_rounds: int = 0
    warnings: list[str] = Field(default_factory=list)


class ExecuteResult(BaseModel):
    """执行阶段产出。total/passed/failed 从 session 聚合。"""

    __test__ = False
    session: TestSession
    duration_s: float = 0.0


class ReportResult(BaseModel):
    """报告阶段产出。"""

    __test__ = False
    report_paths: list[str] = Field(default_factory=list)
    summary: str
    stage_coverage: dict[str, bool]


class Risk(BaseModel):
    """反思阶段的风险项。"""

    __test__ = False
    category: str
    description: str
    severity: str  # P1 / P2


class ReflectResult(BaseModel):
    """反思阶段产出。"""

    __test__ = False
    confidence_score: float
    false_positives: list[dict[str, Any]] = Field(default_factory=list)
    stuck_warnings: list[str] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    reflect_reasoning: str


class PipelineResult(BaseModel):
    """流水线总产出。solidify/execute 可选。"""

    __test__ = False
    explore: ExploreResult
    solidify: SolidifyResult | None = None
    execute: ExecuteResult | None = None
    report: ReportResult
    reflect: ReflectResult
