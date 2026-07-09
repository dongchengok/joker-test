"""JsonReporter —— 把测试结果序列化为 JSON 文件（机器可读）。"""

from __future__ import annotations

import json
from pathlib import Path

from joker_test.reporters.base import TestCase, TestResult, TestSession


class JsonReporter:
    """JSON 报告器。累积 TestResult，finalize 时写 JSON 文件。

    满足 TestReporter 协议（结构性子类型）。
    """

    name = "json"

    def __init__(self, output_path: str | Path) -> None:
        self._output_path = Path(output_path)
        self._session: TestSession | None = None
        self._results: list[TestResult] = []

    def on_session_start(self, session: TestSession) -> None:
        self._session = session

    def on_test_start(self, test: TestCase) -> None:
        pass  # JSON 报告只在结束时汇总

    def on_test_end(self, result: TestResult) -> None:
        self._results.append(result)

    def on_session_end(self, session: TestSession) -> None:
        if self._session is not None:
            self._session.results = self._results

    def finalize(self) -> str:
        """写 JSON 文件，返回路径。"""
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        data = (self._session or TestSession(
            id="unknown", started_at="", results=self._results
        )).model_dump()
        self._output_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return str(self._output_path)


__all__ = ["JsonReporter"]
