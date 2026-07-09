"""joker_test.reporters —— 测试报告（DESIGN §4.7）。

TestReporter 协议 + Json/Html/Multi 三个实现。
数据契约（TestSession/TestCase/TestResult）在 base.py。
"""

from joker_test.reporters.base import (
    Attachment,
    TestCase,
    TestReporter,
    TestResult,
    TestSession,
    TestStatus,
    TestStep,
)
from joker_test.reporters.html import HtmlReporter
from joker_test.reporters.json import JsonReporter
from joker_test.reporters.multi import MultiReporter

__all__ = [
    "TestReporter",
    "TestSession",
    "TestCase",
    "TestResult",
    "TestStep",
    "Attachment",
    "TestStatus",
    "JsonReporter",
    "HtmlReporter",
    "MultiReporter",
]
