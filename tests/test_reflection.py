"""M5 冒烟反思测试。

验证：卡死检测（确定性规则）+ 误报审查（LLM 二次审查 failed 测试）。
守住 LLM 克制红线（执行不调 LLM，只审查 failed）。
"""

from __future__ import annotations

from joker_test.llm.providers.mock import MockProvider
from joker_test.reflection import (
    detect_stuck_loop,
    review_failure,
)
from joker_test.reporters.base import TestCase, TestResult

# ============== 卡死检测（确定性，不调 LLM）==============

def test_detect_stuck_loop_triggered() -> None:
    """连续 3 次相同信号 → 检测到卡死。"""
    signals = ["a", "a", "a"]  # 3 个全相同
    assert detect_stuck_loop(signals) is True


def test_detect_stuck_loop_not_triggered_changing() -> None:
    """信号有变化 → 不算卡死。"""
    assert detect_stuck_loop(["a", "b", "c"]) is False
    assert detect_stuck_loop(["a", "a", "b"]) is False


def test_detect_stuck_loop_not_enough_signals() -> None:
    """少于 3 个信号 → 不检测。"""
    assert detect_stuck_loop(["a", "a"]) is False
    assert detect_stuck_loop([]) is False


def test_detect_stuck_loop_threshold_at_3() -> None:
    """正好 3 个相同 → 触发。"""
    assert detect_stuck_loop(["x", "x", "x"]) is True
    # 但前 2 个相同 + 第 3 个不同 → 不触发
    assert detect_stuck_loop(["x", "x", "y", "y", "y"]) is True  # 最后 3 个相同


# ============== 误报审查（LLM）==============

def test_review_failure_skips_passed() -> None:
    """passed 测试不审查（省 token，LLM 克制）。"""
    result = TestResult(test=TestCase(name="ok"), status="passed")
    is_fp, reason = review_failure(MockProvider(), result)
    assert is_fp is False
    assert "非失败" in reason


def test_review_failure_returns_verdict() -> None:
    """failed 测试 → 调 LLM → 返回误报判定（MockProvider 返回 is_false_positive=True）。"""
    result = TestResult(
        test=TestCase(name="test_x"),
        status="failed",
        error="AssertionError: '背包' not in []",
    )
    is_fp, reason = review_failure(MockProvider(), result)
    # MockProvider 固定返回 is_false_positive=true
    assert is_fp is True
    assert "误报" in reason or reason  # 有理由


def test_review_failure_handles_llm_error() -> None:
    """LLM 调用失败时保守判为真 bug（不轻易推翻测试结论）。"""

    class FailingProvider:
        def create(self, *, messages, system=None, model=None, max_tokens=None, tools=None, tool_choice=None):
            raise RuntimeError("LLM 挂了")

    result = TestResult(test=TestCase(name="x"), status="failed", error="err")
    is_fp, reason = review_failure(FailingProvider(), result)  # type: ignore[arg-type]
    assert is_fp is False  # 保守判真 bug
    assert "失败" in reason
