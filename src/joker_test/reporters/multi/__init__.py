"""MultiReporter —— 组合多个 Reporter，事件广播 + 错误隔离（DESIGN §4.7.3）。"""

from __future__ import annotations

import logging

from joker_test.reporters.base import TestCase, TestReporter, TestResult, TestSession

logger = logging.getLogger(__name__)


class MultiReporter:
    """组合多个 Reporter，事件广播。单个 Reporter 异常不影响其他（错误隔离）。

    满足 TestReporter 协议（结构性子类型）。

    用法::
        multi = MultiReporter([JsonReporter(...), HtmlReporter(...)])
        multi.on_session_start(session)
        ...
        paths = multi.finalize()  # 返回每个 reporter 的路径
    """

    name = "multi"

    def __init__(self, reporters: list[TestReporter]) -> None:
        self._reporters = reporters

    def _broadcast(self, method: str, *args: object) -> None:
        """安全广播：单个 reporter 崩了不影响其他。"""
        for r in self._reporters:
            try:
                getattr(r, method)(*args)
            except Exception as e:  # noqa: BLE001
                logger.warning("Reporter %s.%s 失败: %s", type(r).__name__, method, e)

    def on_session_start(self, session: TestSession) -> None:
        self._broadcast("on_session_start", session)

    def on_test_start(self, test: TestCase) -> None:
        self._broadcast("on_test_start", test)

    def on_test_end(self, result: TestResult) -> None:
        self._broadcast("on_test_end", result)

    def on_session_end(self, session: TestSession) -> None:
        self._broadcast("on_session_end", session)

    def finalize(self) -> str:
        """调用所有 reporter 的 finalize，返回汇总路径字符串。"""
        paths: list[str] = []
        for r in self._reporters:
            try:
                paths.append(f"  • {type(r).__name__}: {r.finalize()}")
            except Exception as e:  # noqa: BLE001
                logger.warning("Reporter %s.finalize 失败: %s", type(r).__name__, e)
        return "\n".join(paths)


__all__ = ["MultiReporter"]
