"""测试执行器（M3b）。

跑 pytest 测试，收集结果，转译成 TestSession/TestResult 喂给 Reporter。

设计：
- 不写 pytest 插件文件，用 pytest.main + inline plugin 对象（hookimpl）
- 收集 runtest protocol 的 setup/call/teardown 三阶段结果
- 转译成 TestResult（status/duration/error），按 reporter 事件驱动送出
- 纯函数式：run_tests 返回 TestSession，调用方决定怎么报告

不依赖真实游戏：测试用 FakeBackend 跑（conftest 提供 fixture）。
"""

from __future__ import annotations

import datetime
import time
import uuid
from pathlib import Path

import pytest

from joker_test.reporters.base import TestCase, TestResult, TestSession, TestStatus


class _ResultCollector:
    """pytest inline plugin，收集每个 test 的结果。

    pytest 的 hookimpl：挂到 runtest_protocol，捕获 setup/call/teardown 报告。
    """

    def __init__(self) -> None:
        self.results: list[TestResult] = []
        self._test_cases: dict[str, TestCase] = {}
        self._timings: dict[str, float] = {}

    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        """收集所有 test case 元信息。"""
        for item in items:
            if item.nodeid not in self._test_cases:
                self._test_cases[item.nodeid] = TestCase(
                    name=item.name,
                    module=item.module.__name__ if hasattr(item, "module") else "",
                )

    def pytest_runtest_setup(self, item: pytest.Item) -> None:
        self._timings[item.nodeid] = time.monotonic()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call):
        """捕获每个阶段的报告，call 阶段决定最终 status。"""
        outcome = yield
        report = outcome.get_result()
        if report.when == "call":  # 只用 call 阶段定最终结果
            nodeid = item.nodeid
            duration = max(0.0, report.duration) if hasattr(report, "duration") else 0.0
            status: TestStatus = "passed"
            error: str | None = None
            if report.failed:
                status = "failed"
                error = str(report.longrepr) if report.longrepr else "未知失败"
            elif report.skipped:
                status = "skipped"
            tc = self._test_cases.get(nodeid, TestCase(name=item.name))
            self.results.append(
                TestResult(test=tc, status=status, duration=duration, error=error)
            )


def run_tests(
    test_paths: list[str | Path],
    backend_name: str = "fake",
    game_name: str = "unknown",
) -> TestSession:
    """执行 pytest 测试，返回 TestSession（含所有 TestResult）。

    Args:
        test_paths: 测试文件/目录路径列表
        backend_name: backend 类型名（记录到 session.backend）
        game_name: 游戏名（记录到 session.game）

    Returns:
        TestSession（含 results）。失败用例在 results 里 status=failed。
    """
    session = TestSession(
        id=str(uuid.uuid4())[:8],
        started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        game=game_name,
        backend=backend_name,
    )
    collector = _ResultCollector()

    args = [str(p) for p in test_paths] + ["-q", "--no-header", "--tb=short"]
    pytest.main(args, plugins=[collector])

    session.results = collector.results
    session.tests = list(collector._test_cases.values())  # noqa: SLF001
    session.finished_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return session


__all__ = ["run_tests"]
