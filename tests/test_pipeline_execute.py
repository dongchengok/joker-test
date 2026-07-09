"""ExecuteStage 测试。"""
from __future__ import annotations

from pathlib import Path

from joker_test.pipeline.stages.execute import ExecuteStage
from joker_test.pipeline.types import ExploreConfig


def test_execute_runs_tests(tmp_path: Path) -> None:
    """执行阶段能跑 pytest 并返回 ExecuteResult。"""
    test_file = tmp_path / "test_simple.py"
    test_file.write_text(
        "def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    cfg = ExploreConfig(intent="test", game_name="demo", backend_name="fake")
    stage = ExecuteStage()
    result = stage.run([str(test_file)], cfg)
    assert result.session.passed == 1
    assert result.session.failed == 0


def test_execute_collects_failure(tmp_path: Path) -> None:
    """失败用例被收集，不抛异常。"""
    test_file = tmp_path / "test_fail.py"
    test_file.write_text(
        "def test_bad():\n    assert False\n", encoding="utf-8"
    )
    cfg = ExploreConfig(intent="test")
    stage = ExecuteStage()
    result = stage.run([str(test_file)], cfg)
    assert result.session.failed == 1
