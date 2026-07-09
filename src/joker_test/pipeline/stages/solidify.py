"""固化阶段：操作轨迹 → LLM 生成 pytest test_case。

一次固化多次回归。可选试跑回喂（TestCaseVerifier）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from joker_test.executor.base import ExecutorBackend
from joker_test.flow.generator import RecordedFlowGenerator
from joker_test.flow.types import RecordedFlow
from joker_test.generator import write_tests_to_dir
from joker_test.llm.base import LLMProvider
from joker_test.pipeline.types import ExploreConfig, SolidifyResult

_LOGGER = logging.getLogger(__name__)

_VERIFY_MAX_RETRIES = 2


class SolidifyStage:
    """固化操作轨迹为 pytest test_case。"""

    def __init__(
        self,
        provider: LLMProvider,
        backend: ExecutorBackend,
        gen_dir: str | Path = "tests/generated_smoke",
    ) -> None:
        self._provider = provider
        self._backend = backend
        self._gen_dir = Path(gen_dir)
        self._generator = RecordedFlowGenerator(provider)

    def run(
        self,
        flow: RecordedFlow,
        flow_dir: Path,
        config: ExploreConfig,
    ) -> SolidifyResult:
        game_meta: dict[str, Any] = {"game_name": config.game_name}
        tests = self._generator.generate(flow, flow_dir, game_meta)
        test_paths = write_tests_to_dir(tests, self._gen_dir)
        spec_paths = [str(self._gen_dir / t.spec_filename) for t in tests]

        warnings: list[str] = []
        verify_ok = False
        verify_rounds = 0

        if config.verify_during_solidify and test_paths:
            verify_ok, verify_rounds, vw = self._verify(
                tests, flow, flow_dir, game_meta, config
            )
            warnings.extend(vw)

        return SolidifyResult(
            test_paths=[str(p) for p in test_paths],
            spec_paths=spec_paths,
            verify_ok=verify_ok,
            verify_rounds=verify_rounds,
            warnings=warnings,
        )

    def _verify(
        self,
        tests: list[Any],
        flow: RecordedFlow,
        flow_dir: Path,
        game_meta: dict[str, Any],
        config: ExploreConfig,
    ) -> tuple[bool, int, list[str]]:
        """试跑回喂闭环。返回 (是否全通过, 重试轮数, 警告)。"""
        from joker_test.flow.verifier import TestCaseVerifier

        def reset_fn() -> None:
            self._backend.connect()

        verifier = TestCaseVerifier(
            reset_fn=reset_fn,
            gen_dir=self._gen_dir,
            max_retries=_VERIFY_MAX_RETRIES,
            backend_name=config.backend_name,
            game_name=config.game_name or "verified",
        )
        fixed_tests, verify_warnings = verifier.verify_and_fix(
            tests, self._generator, flow, flow_dir, game_meta
        )
        all_pass = len(verify_warnings) == 0
        return all_pass, _VERIFY_MAX_RETRIES, [str(w) for w in verify_warnings]
