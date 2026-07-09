"""执行阶段：跑 pytest，脱离 LLM。

复用 runner.run_tests，纯 pytest 收集结果。
"""
from __future__ import annotations

import time

from joker_test.pipeline.types import ExecuteResult, ExploreConfig
from joker_test.runner import run_tests


class ExecuteStage:
    """执行固化产物，脱离 LLM。"""

    def run(self, test_paths: list[str], config: ExploreConfig) -> ExecuteResult:
        start = time.monotonic()
        session = run_tests(
            test_paths,
            backend_name=config.backend_name,
            game_name=config.game_name or "unknown",
        )
        return ExecuteResult(session=session, duration_s=time.monotonic() - start)
