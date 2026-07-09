"""M3b 执行+报告测试。

验证：run_tests 跑手写冒烟测试 → 收集结果 → Json/Html/Multi Reporter 产出报告。
这是 M3b 的完成标志（roadmap: "一条命令：生成用例→pytest 跑→JSON+HTML 报告"）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joker_test.reporters import (
    HtmlReporter,
    JsonReporter,
    MultiReporter,
    TestCase,
    TestReporter,
    TestResult,
    TestSession,
)
from joker_test.runner import run_tests


@pytest.fixture
def smoke_test_dir() -> str:
    """手写冒烟测试目录。"""
    return "tests/smoke"


def test_run_tests_collects_results(smoke_test_dir: str) -> None:
    """run_tests 应执行测试并收集 TestResult。"""
    session = run_tests([smoke_test_dir], backend_name="fake", game_name="测试游戏")
    assert len(session.results) >= 1
    assert session.game == "测试游戏"
    assert session.backend == "fake"
    # 至少有一个 passed
    assert any(r.status == "passed" for r in session.results)


def test_json_reporter_writes_file(tmp_path: Path, smoke_test_dir: str) -> None:
    """JsonReporter 应产出合法 JSON 文件。"""
    session = run_tests([smoke_test_dir], backend_name="fake")
    out = tmp_path / "report.json"
    reporter = JsonReporter(out)
    reporter.on_session_start(session)
    for r in session.results:
        reporter.on_test_end(r)
    reporter.on_session_end(session)
    path = reporter.finalize()

    assert Path(path).exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "results" in data
    assert len(data["results"]) >= 1


def test_html_reporter_writes_file(tmp_path: Path, smoke_test_dir: str) -> None:
    """HtmlReporter 应产出 HTML 文件。"""
    session = run_tests([smoke_test_dir], backend_name="fake")
    out = tmp_path / "report.html"
    reporter = HtmlReporter(out)
    reporter.on_session_start(session)
    for r in session.results:
        reporter.on_test_end(r)
    reporter.on_session_end(session)
    path = reporter.finalize()

    assert Path(path).exists()
    html = out.read_text(encoding="utf-8")
    assert "<table>" in html
    assert "joker-test" in html


def test_multi_reporter_broadcasts(tmp_path: Path, smoke_test_dir: str) -> None:
    """MultiReporter 应广播到 Json + Html，两份都产出。"""
    session = run_tests([smoke_test_dir], backend_name="fake")
    json_out = tmp_path / "r.json"
    html_out = tmp_path / "r.html"
    multi = MultiReporter([JsonReporter(json_out), HtmlReporter(html_out)])
    multi.on_session_start(session)
    for r in session.results:
        multi.on_test_end(r)
    multi.on_session_end(session)
    summary = multi.finalize()

    assert json_out.exists()
    assert html_out.exists()
    assert "JsonReporter" in summary
    assert "HtmlReporter" in summary


def test_multi_reporter_error_isolation(tmp_path: Path) -> None:
    """MultiReporter 错误隔离：一个 reporter 崩了不影响其他。"""

    class CrashingReporter:
        name = "crash"

        def on_session_start(self, s: TestSession) -> None: pass
        def on_test_start(self, t: TestCase) -> None: pass
        def on_test_end(self, r: TestResult) -> None:
            raise RuntimeError("故意崩溃")
        def on_session_end(self, s: TestSession) -> None: pass
        def finalize(self) -> str: return ""

    json_out = tmp_path / "safe.json"
    multi = MultiReporter([CrashingReporter(), JsonReporter(json_out)])
    session = TestSession(id="t", started_at="2026", game="g", backend="b")
    multi.on_session_start(session)
    multi.on_test_end(TestResult(test=TestCase(name="x"), status="passed"))  # crash 被隔离
    multi.on_session_end(session)
    multi.finalize()
    # JsonReporter 不受崩溃影响，正常产出
    assert json_out.exists()


def test_reporters_satisfy_protocol() -> None:
    """Json/Html/Multi 应满足 TestReporter 协议。"""
    assert isinstance(JsonReporter("x.json"), TestReporter)
    assert isinstance(HtmlReporter("x.html"), TestReporter)
    assert isinstance(MultiReporter([]), TestReporter)


def test_testsession_passed_failed_counts() -> None:
    """TestSession.passed/failed 计数。"""
    session = TestSession(
        id="t", started_at="2026",
        results=[
            TestResult(test=TestCase(name="a"), status="passed"),
            TestResult(test=TestCase(name="b"), status="failed"),
            TestResult(test=TestCase(name="c"), status="passed"),
        ],
    )
    assert session.passed == 2
    assert session.failed == 1
    # computed_field 必须进 model_dump 顶层（report.json 依赖此序列化路径）
    dumped = session.model_dump()
    assert dumped["passed"] == 2
    assert dumped["failed"] == 1
