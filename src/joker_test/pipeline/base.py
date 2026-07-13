"""Stage 协议与编排器。

薄编排：只持 5 个 Stage 实例，按分支调用，无业务逻辑。
Stage 间只通过 Result 对象单向传递数据，无反向引用。
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from joker_test.pipeline.stages.execute import ExecuteStage
from joker_test.pipeline.stages.explore import ExploreStage
from joker_test.pipeline.stages.reflect import ReflectStage
from joker_test.pipeline.stages.report import ReportStage
from joker_test.pipeline.stages.solidify import SolidifyStage
from joker_test.pipeline.types import ExploreConfig, PipelineResult


@runtime_checkable
class Stage(Protocol):
    """Stage 协议：run 接受参数，返回各阶段 Result。"""

    def run(self, *args: object, **kwargs: object) -> object: ...


class AgenticOrchestrator:
    """四阶段流水线编排器。

    薄编排，按 config 分支调用 Stage：
    - 命中已固化资产 → 复用执行
    - 探索直出（solidify=False）→ 不固化不执行
    - 探索 + 固化 + 执行 → 全链路
    """

    def __init__(
        self,
        explore: ExploreStage,
        solidify: SolidifyStage,
        execute: ExecuteStage,
        report: ReportStage,
        reflect: ReflectStage,
    ) -> None:
        self._explore = explore
        self._solidify = solidify
        self._execute = execute
        self._report = report
        self._reflect = reflect

    def run(self, config: ExploreConfig) -> PipelineResult:
        explore_out = self._explore.run(config)

        # 命中已固化资产 → 复用执行
        if explore_out.skipped:
            execute_out = (
                self._execute.run(explore_out.reused_test_paths, config)
                if config.execute
                else None
            )
            report_out = self._report.run(
                explore=explore_out, solidify=None, execute=execute_out, config=config
            )
            reflect_out = self._reflect.run(
                explore=explore_out,
                execute=execute_out,
                report=report_out,
                config=config,
            )
            return PipelineResult(
                explore=explore_out,
                execute=execute_out,
                report=report_out,
                reflect=reflect_out,
            )

        # 探索直出
        if not config.solidify:
            report_out = self._report.run(
                explore=explore_out, solidify=None, execute=None, config=config
            )
            reflect_out = self._reflect.run(
                explore=explore_out,
                execute=None,
                report=report_out,
                config=config,
            )
            return PipelineResult(
                explore=explore_out, report=report_out, reflect=reflect_out
            )

        # 探索 + 固化 + 执行（探索无操作轨迹时降级为直出）
        if explore_out.flow is None:
            report_out = self._report.run(
                explore=explore_out, solidify=None, execute=None, config=config
            )
            reflect_out = self._reflect.run(
                explore=explore_out,
                execute=None,
                report=report_out,
                config=config,
            )
            return PipelineResult(
                explore=explore_out, report=report_out, reflect=reflect_out
            )
        solidify_out = self._solidify.run(
            explore_out.flow,
            Path(explore_out.flow_dir or "flows"),
            config,
        )
        execute_out = (
            self._execute.run(solidify_out.test_paths, config)
            if config.execute
            else None
        )
        report_out = self._report.run(
            explore=explore_out,
            solidify=solidify_out,
            execute=execute_out,
            config=config,
        )
        reflect_out = self._reflect.run(
            explore=explore_out,
            execute=execute_out,
            report=report_out,
            config=config,
        )
        return PipelineResult(
            explore=explore_out,
            solidify=solidify_out,
            execute=execute_out,
            report=report_out,
            reflect=reflect_out,
        )


def build_orchestrator(
    config: ExploreConfig,
    report_dir: str | Path = "reports",
    gen_dir: str | Path = "tests/generated_smoke",
    flow_dir: str | Path = "flows",
) -> AgenticOrchestrator:
    """根据 config 构造编排器，注入各 Stage 所需依赖。"""
    from joker_test.executor import set_active_backend
    from joker_test.executor.backends.fake import FakeBackend
    from joker_test.llm.providers.mock import MockProvider

    provider = MockProvider()
    backend = FakeBackend()
    set_active_backend(backend)
    return AgenticOrchestrator(
        explore=ExploreStage(provider, backend, gen_dir=gen_dir, flow_dir=flow_dir),
        solidify=SolidifyStage(provider, backend, gen_dir=gen_dir),
        execute=ExecuteStage(),
        report=ReportStage(report_dir=report_dir),
        reflect=ReflectStage(provider),
    )
