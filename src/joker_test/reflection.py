"""测试反思钩子。

在执行回路注入 LLM 反思，防"傻干"和"误报"：
- **防傻干**：检测连续 N 次操作无状态变化（卡死循环）
- **防误报（冒烟）**：对失败的测试用 LLM 二次审查（可能是断言写错而非真 bug）

设计要点（守住 DESIGN §2.2 LLM 克制红线）：
- 反思只在"判断点"调 LLM（执行不调）
- 卡死检测是确定性规则（连续操作无变化），不调 LLM
- 误报审查才调 LLM，且只对 failed 结果调（passed 不调，省 token）

依赖：LLMProvider（误报审查用）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from joker_test.reporters.base import TestResult

if TYPE_CHECKING:
    from joker_test.llm.base import LLMProvider, Message

logger = logging.getLogger(__name__)

# 连续多少次操作无状态变化算"卡死"
_STUCK_THRESHOLD = 3


def detect_stuck_loop(operation_signals: list[str]) -> bool:
    """检测卡死循环：连续 N 次操作的状态信号完全相同。

    确定性规则，不调 LLM（LLM 克制红线）。

    Args:
        operation_signals: 每次操作后的状态信号序列（如 state.texts 的 hash）

    Returns:
        True 表示检测到卡死（最后 N 个信号完全相同）
    """
    if len(operation_signals) < _STUCK_THRESHOLD:
        return False
    recent = operation_signals[-_STUCK_THRESHOLD:]
    return len(set(recent)) == 1  # 最后 N 个全相同


def review_failure(
    provider: LLMProvider,
    result: TestResult,
    context: str = "",
) -> tuple[bool, str]:
    """LLM 二次审查失败的测试，判断是否误报。

    只对 failed 测试调（防误报）。passed 不调（省 token，LLM 克制）。

    Args:
        provider: LLM provider
        result: 失败的 TestResult
        context: 额外上下文（如操作历史）

    Returns:
        (是否误报, 理由)
    """
    if result.status != "failed":
        return False, "非失败测试，不审查"

    prompt = (
        "<failed_test>\n"
        f"测试名: {result.test.name}\n"
        f"错误: {result.error or '无错误信息'}\n"
        f"耗时: {result.duration:.3f}s\n"
        f"</failed_test>\n\n"
        f"<context>{context}</context>\n\n"
        "请判断这个测试失败是【真 bug】还是【误报】（断言写错/fixture 问题/环境问题）。\n"
        "误报的典型特征：断言了不存在的元素、fixture 状态错位、测试逻辑本身有 bug。\n"
        "请只回答 JSON：{\"is_false_positive\": true/false, \"reason\": \"...\"}"
    )
    try:
        msg = provider.simple_converse(prompt, [], reasoning=8000)
    except Exception as e:  # noqa: BLE001
        logger.warning("误报审查 LLM 调用失败: %s，保守判为真 bug", e)
        return False, f"LLM 调用失败: {e}"

    text = _extract_text(msg)
    return _parse_verdict(text)


def _extract_text(msg: Message) -> str:
    parts = [b.get("text", "") for b in msg.get("content", []) if "text" in b]
    return "\n".join(parts)


def _parse_verdict(text: str) -> tuple[bool, str]:
    """从 LLM 回复解析误报判定（容错，解析失败保守判为真 bug）。"""
    import json  # noqa: PLC0415

    # 尝试找到 JSON 块
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{") and "is_false_positive" in line:
            try:
                data = json.loads(line)
                return bool(data.get("is_false_positive", False)), data.get("reason", "")
            except json.JSONDecodeError:
                continue
    # 容错：解析失败保守判为真 bug（不轻易推翻测试结论）
    return False, f"LLM 回复无法解析: {text[:100]}"


__all__ = ["detect_stuck_loop", "review_failure"]

