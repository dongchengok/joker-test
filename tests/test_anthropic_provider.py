"""AnthropicProvider 测试。

覆盖 create 的两个 Anthropic extended thinking 兼容性约束：
1. thinking_enabled 时 max_tokens 必须 > thinking.budget_tokens（否则 API 400）
2. tool_use 重试时不得回传无 signature 的 thinking block（否则 API 400）

不依赖真实 API：mock 掉 client.messages.create，用 SimpleNamespace 模拟 SDK block。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("anthropic")

from joker_test.explorer.strategy import EXPLORE_TOOL_SCHEMA
from joker_test.llm.providers.anthropic import AnthropicProvider


def _block(typ: str, **kw: object) -> SimpleNamespace:
    """构造一个模拟 SDK 的 content block。"""
    return SimpleNamespace(type=typ, **kw)


def _response(blocks: list[SimpleNamespace]) -> SimpleNamespace:
    """构造一个模拟 SDK 的 Message response。"""
    return SimpleNamespace(content=blocks)


def _make_provider(
    *,
    thinking_enabled: bool = True,
    thinking_budget_tokens: int = 8000,
    max_tokens: int = 4096,
) -> AnthropicProvider:
    """构造一个不触发真实请求的 provider（api_key 假、base_url 假）。"""
    return AnthropicProvider(
        api_key="test-key",
        base_url="http://fake.invalid",
        model="test-model",
        max_tokens=max_tokens,
        max_retries=0,
        thinking_enabled=thinking_enabled,
        thinking_budget_tokens=thinking_budget_tokens,
    )


def _user_msg() -> list[dict]:
    return [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]


def test_create_enforces_max_tokens_above_thinking_budget() -> None:
    """thinking_enabled 时，默认 max_tokens(4096) < budget(8000) 应被自动抬高。

    Anthropic 约束：max_tokens 必须 > thinking.budget_tokens（thinking 预算含在
    max_tokens 总量内）。当前默认值 4096 < 8000 违反，真 Claude 端点会 400。
    """
    provider = _make_provider(
        thinking_enabled=True, thinking_budget_tokens=8000, max_tokens=4096
    )
    captured: dict = MagicMock()

    def fake_create(**kwargs: object) -> SimpleNamespace:
        nonlocal captured
        captured = kwargs
        return _response([_block("tool_use", name="execute_action", input={})])

    provider._client.messages.create = fake_create  # type: ignore[method-assign]

    provider.create(messages=_user_msg(), tools=[EXPLORE_TOOL_SCHEMA])

    assert captured["max_tokens"] > captured["thinking"]["budget_tokens"]


def test_create_retry_strips_thinking_without_signature() -> None:
    """tool_use 未返回触发重试时，回传的 assistant 消息必须剥离 thinking block。

    根因：_response_to_dict 生成 thinking block 时丢失 signature 字段，而 Anthropic
    要求回传 thinking 必须带完整 signature。重试时若回传无 signature 的 thinking
    会 400。修复策略：重试路径与主路径一致——不回传 thinking。
    """
    provider = _make_provider(thinking_enabled=True)
    calls: list[dict] = []
    # 第一次：thinking + text，无 tool_use → 触发重试
    # 第二次：有 tool_use → 成功
    responses = [
        _response(
            [
                _block("thinking", thinking="reasoning", signature="sig-abc"),
                _block("text", text="no tool here"),
            ]
        ),
        _response([_block("tool_use", name="execute_action", input={"action": "stop"})]),
    ]

    def fake_create(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)  # type: ignore[arg-type]
        return responses[len(calls) - 1]

    provider._client.messages.create = fake_create  # type: ignore[method-assign]

    provider.create(messages=_user_msg(), tools=[EXPLORE_TOOL_SCHEMA])

    assert len(calls) == 2, "应触发一次重试"
    retry_messages = calls[1]["messages"]
    assistant_blocks = [
        b
        for m in retry_messages
        if isinstance(m, dict) and m.get("role") == "assistant"
        for b in m["content"]
        if isinstance(b, dict)
    ]
    thinking_blocks = [b for b in assistant_blocks if b.get("type") == "thinking"]
    assert thinking_blocks == [], "重试回传的 assistant 消息不得含 thinking block"


def test_create_passes_system_as_top_level_param() -> None:
    """system 必须走顶层 kwargs，且不得出现在 messages 数组里（Anthropic 约束）。"""
    provider = _make_provider()
    captured: dict = MagicMock()

    def fake_create(**kwargs: object) -> SimpleNamespace:
        nonlocal captured
        captured = kwargs
        return _response([_block("text", text="ok")])

    provider._client.messages.create = fake_create  # type: ignore[method-assign]

    provider.create(messages=_user_msg(), system="你是探索助手")

    assert captured.get("system") == "你是探索助手"
    roles = [m.get("role") for m in captured["messages"] if isinstance(m, dict)]
    assert "system" not in roles, "messages 数组不得含 role=system"


def test_retry_escalates_tool_choice_to_any() -> None:
    """auto 下模型纯文本回复时，第一次重试必须把 tool_choice 升级为强制 any。

    背景：tool_choice=auto 下部分端点/模型会长上下文漂移成纯文本回复，
    重试保持 auto 只会重复同样的漂移（真机实测 kimi-k3 连续 22 次未返回
    tool_use）。升级为 any 后由 API 层 constrained decoding 强制返回 tool_use。
    """
    provider = _make_provider(thinking_enabled=True)
    calls: list[dict] = []
    responses = [
        _response([_block("text", text="我直接文字回答")]),
        _response([_block("tool_use", name="execute_action", input={"action": "stop"})]),
    ]

    def fake_create(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)  # type: ignore[arg-type]
        return responses[len(calls) - 1]

    provider._client.messages.create = fake_create  # type: ignore[method-assign]

    provider.create(
        messages=_user_msg(), tools=[EXPLORE_TOOL_SCHEMA], tool_choice={"type": "auto"}
    )

    assert len(calls) == 2
    assert calls[0]["tool_choice"] == {"type": "auto"}
    assert calls[1]["tool_choice"] == {"type": "any"}, "第一次重试应升级为强制 any"


def test_forced_tool_choice_400_falls_back_to_auto() -> None:
    """强制 any 被端点 400 拒绝时，必须降回原始 tool_choice 继续重试。

    真 Anthropic 端点 thinking 开启时不允许强制 tool_choice（400），
    升级策略在该端点上必须无损降级，行为与升级前一致。
    """
    provider = _make_provider(thinking_enabled=True)
    calls: list[dict] = []

    class Fake400(Exception):
        status_code = 400

    outcomes: list[object] = [
        _response([_block("text", text="no tool")]),
        Fake400("thinking 时不允许强制 tool_choice"),
        _response([_block("tool_use", name="execute_action", input={"action": "stop"})]),
    ]

    def fake_create(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)  # type: ignore[arg-type]
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    provider._client.messages.create = fake_create  # type: ignore[method-assign]

    provider.create(
        messages=_user_msg(), tools=[EXPLORE_TOOL_SCHEMA], tool_choice={"type": "auto"}
    )

    assert len(calls) == 3, "升级 → 400 → 降回 auto 再试，共 3 次调用"
    assert calls[1]["tool_choice"] == {"type": "any"}
    assert calls[2]["tool_choice"] == {"type": "auto"}, "400 后必须降回原始 tool_choice"


def test_caller_forced_tool_choice_not_escalated() -> None:
    """调用方显式强制 tool_choice 时，重试不得改动它。"""
    provider = _make_provider(thinking_enabled=True)
    calls: list[dict] = []
    forced = {"type": "tool", "name": "execute_action"}
    responses = [
        _response([_block("text", text="no tool")]),
        _response([_block("tool_use", name="execute_action", input={"action": "stop"})]),
    ]

    def fake_create(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)  # type: ignore[arg-type]
        return responses[len(calls) - 1]

    provider._client.messages.create = fake_create  # type: ignore[method-assign]

    provider.create(messages=_user_msg(), tools=[EXPLORE_TOOL_SCHEMA], tool_choice=forced)

    assert len(calls) == 2
    assert calls[1]["tool_choice"] == forced, "显式强制的 tool_choice 不得被改动"
