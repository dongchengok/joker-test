"""TestCaseVerifier —— 试跑验证器：重置游戏→跑 test_case→收集结果→回喂重写。

与 RecordedFlowGenerator 配合：生成器产出 test_case 后，验证器试跑验证，
失败的回喂生成器重写。状态自洽：只持有 reset_fn + gen_dir。

设计要点：
- 状态自洽：不依赖外部状态，reset_fn 是注入的游戏重置函数
- 只重置一次游戏：所有 test 共享一次重置（不是每个 test 重置，省时间）
- 复用 runner.run_tests：试跑直接用现有 pytest 执行链路
- 回喂重写委托给 generator：验证器只负责"跑+判断"，重写逻辑在 generator

用法::

    verifier = TestCaseVerifier(reset_fn=reset_spd, gen_dir="tests/generated_smoke")
    tests = verifier.verify_and_fix(tests, generator, flow, flow_dir, game_meta)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from joker_test.generator.generator import write_tests_to_dir
from joker_test.runner import run_tests
from joker_test.trace import trace_event, trace_stage

if TYPE_CHECKING:
    from joker_test.flow.generator import RecordedFlowGenerator
    from joker_test.flow.types import RecordedFlow
    from joker_test.generator.types import GeneratedTest
    from joker_test.reporters.base import TestSession

logger = logging.getLogger(__name__)


class TestCaseVerifier:
    """试跑验证器（重置→跑→回喂重写闭环）。状态自洽。

    Args:
        reset_fn: 游戏重置函数（每个验证轮前调，确保干净起点）
        gen_dir: 生成的 test 文件落盘目录
        max_retries: 试跑失败后回喂重写的最大轮数（默认 2）
        backend_name: 试跑用的 backend（默认 airtest）
    """

    def __init__(
        self,
        reset_fn: Callable[[], None],
        gen_dir: str | Path,
        max_retries: int = 2,
        backend_name: str = "airtest",
        game_name: str = "verified",
    ) -> None:
        self._reset_fn = reset_fn
        self._gen_dir = Path(gen_dir)
        self._max_retries = max_retries
        self._backend_name = backend_name
        self._game_name = game_name

    def verify_and_fix(
        self,
        tests: list[GeneratedTest],
        generator: RecordedFlowGenerator,
        flow: RecordedFlow,
        flow_dir: Path,
        game_meta: dict,
    ) -> tuple[list[GeneratedTest], list[dict]]:
        """试跑验证 + 回喂重写闭环。

        Args:
            tests: 生成器产出的 test_case 列表
            generator: 生成器（用于回喂重写）
            flow: 录制操作流（回喂时给 LLM 上下文）
            flow_dir: 录制产物目录
            game_meta: 游戏元数据

        Returns:
            (最终 test_case 列表, 验证历史)。验证历史是每轮的 {round, passed, failed, errors}。
        """
        history: list[dict] = []
        current_tests = tests

        with trace_stage("verify"):
            for retry in range(self._max_retries + 1):
                logger.info("试跑验证第 %d 轮（共 %d 轮）", retry + 1, self._max_retries + 1)

                # 重置游戏（干净起点）
                self._reset_fn()

                # 试跑
                session = self._run_tests(current_tests)
                round_info = {
                    "round": retry + 1,
                    "passed": session.passed,
                    "failed": session.failed,
                    "total": len(session.results),
                    "errors": [
                        {"test": r.test.name, "error": (r.error or "")[:200]}
                        for r in session.results
                        if r.status == "failed"
                    ],
                }
                history.append(round_info)
                logger.info(
                    "第 %d 轮：通过 %d，失败 %d", retry + 1, session.passed, session.failed
                )

                # trace：每轮试跑结果（含失败用例的错误信息）
                trace_event("verify_round", {
                    "round": retry + 1, "passed": session.passed,
                    "failed": session.failed, "total": len(session.results),
                    "errors": round_info["errors"],
                })

                if session.failed == 0:
                    logger.info("全部通过，验证完成")
                    trace_event("verify_pass", {"round": retry + 1})
                    return current_tests, history

                if retry < self._max_retries:
                    # 回喂重写：把失败信息喂生成器 LLM 重写
                    logger.info("回喂重写第 %d 轮...", retry + 1)
                    current_tests = generator.rewrite_failed(
                        current_tests, session, flow, flow_dir, game_meta
                    )

            logger.warning("达到最大重试次数 %d，仍有失败", self._max_retries)
            trace_event("verify_exhausted", {"retries": self._max_retries})
        return current_tests, history

    def _run_tests(self, tests: list[GeneratedTest]) -> TestSession:
        """落盘 + 跑 pytest，返回 TestSession。"""
        # 清理旧文件
        for old in self._gen_dir.glob("test_*.py"):
            old.unlink()
        for old in self._gen_dir.glob("*_spec.py"):
            old.unlink()

        test_paths = write_tests_to_dir(tests, self._gen_dir)
        logger.info("试跑 %d 个 test 文件", len(test_paths))

        import os  # noqa: PLC0415

        os.environ["JOKER_BACKEND"] = self._backend_name

        return run_tests(
            test_paths=[str(p) for p in test_paths],
            backend_name=self._backend_name,
            game_name=self._game_name,
        )


__all__ = ["TestCaseVerifier"]
