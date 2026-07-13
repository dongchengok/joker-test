"""MockProvider —— 固定回复的 LLMProvider 实现，用于离线/CI 验证。

无网络、无 LLM。实现 LLMProvider 协议（create/parse/stream/count_tokens）。
内置 trace_llm，与 AnthropicProvider 行为一致。
"""
from __future__ import annotations

import copy
import json
import time
from typing import Any

from joker_test.llm.base import Message, format_trace_messages

# 固定回复（content block 带 type 字段，对齐 SDK 格式）
_ARCHITECT_REPLY: Message = {
    "content": [{"type": "text", "text": "[Mock Architect] 已生成 Charter 草稿（模拟）。"}]
}
_ANALYST_REPLY: Message = {
    "content": [{"type": "text", "text": "[Mock Analyst] 已按 checklist 反思（模拟），无需修订。"}]
}

_MOCK_CHARTERS: list[dict[str, Any]] = [
    {
        "charter_id": 1,
        "target_id": 1,
        "persona": "破坏狂",
        "target_system": "背包系统",
        "target_description": "测试背包的增删改查",
        "load_save": "chapter1",
        "goal": "验证背包在各种操作下不丢物品",
        "exploration_targets": ["背包打开", "物品拖拽", "背包关闭"],
        "heuristics": ["边界值", "时序错乱"],
        "expected_behaviors": ["物品数量正确", "不出现负数"],
        "coverage_dimensions": {
            "region": ["背包界面"],
            "function": ["增", "删", "改", "查"],
            "operation": ["点击", "拖拽"],
            "state": ["空背包", "满背包"],
        },
        "time_budget_minutes": 30,
        "charter_changes_game_state": "no",
        "severity_threshold": "P1",
    },
    {
        "charter_id": 2,
        "target_id": 1,
        "persona": "贪婪者",
        "target_system": "背包系统",
        "target_description": "测试背包容量边界",
        "load_save": "chapter1",
        "goal": "验证背包满时行为",
        "exploration_targets": ["背包满", "溢出"],
        "heuristics": ["边界值"],
        "expected_behaviors": ["满时不崩溃", "提示背包已满"],
        "coverage_dimensions": {
            "region": ["背包界面"],
            "function": ["容量"],
            "operation": ["放入"],
            "state": ["满背包"],
        },
        "time_budget_minutes": 20,
        "charter_changes_game_state": "no",
        "severity_threshold": "P2",
    },
]

_MOCK_SMOKE_TEST_REPLY: Message = {
    "content": [{"type": "text", "text": (
        "### test_inventory.py\n"
        "```python\n"
        '"""背包系统冒烟测试（Mock 生成）。"""\n'
        "from inventory_spec import InventorySpec\n"
        "\n"
        "\n"
        "def test_inventory_open_close(backend):\n"
        '    """测试：打开再关闭背包。"""\n'
        '    backend.click_text("背包")\n'
        '    backend.wait_until(lambda: "背包" in backend.state.texts, timeout=3.0)\n'
        '    assert "背包" in backend.state.texts\n'
        '    backend.press_key("escape")\n'
        "\n"
        "\n"
        "def test_inventory_spec_loads():\n"
        '    """测试：spec 模型能正常构造。"""\n'
        '    spec = InventorySpec(target_save="chapter1", expected_items=["村长剑"])\n'
        '    assert spec.severity == "P1"\n'
        "```\n"
        "\n"
        "### inventory_spec.py\n"
        "```python\n"
        '"""背包测试数据 spec（Mock 生成）。"""\n'
        "from typing import Literal\n"
        "\n"
        "from pydantic import BaseModel\n"
        "\n"
        "\n"
        "class InventorySpec(BaseModel):\n"
        '    target_save: str = "chapter1"\n'
        "    expected_items: list[str] = []\n"
        '    severity: Literal["P0", "P1", "P2"] = "P1"\n'
        "```\n"
    )}]
}

_MOCK_REVIEW_REPLY: Message = {
    "content": [{"type": "text", "text": '{"is_false_positive": true, "reason": "Mock 判定为误报（测试逻辑问题）"}'}]
}


def _get_prompt_text(messages: list[dict[str, Any]]) -> str:
    """从 messages 最后一条 user message 提取文本。"""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", [])
            if isinstance(content, str):
                return content
            for block in content:
                if isinstance(block, dict) and block.get("text"):
                    return block["text"]
    return ""


class MockProvider:
    """固定回复的 LLMProvider 实现（CI 测试用）。

    实现 LLMProvider 协议（create/parse/stream/count_tokens）。
    内置 trace_llm，与 AnthropicProvider 行为一致。
    """

    def create(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> Message:
        """模拟 create：根据 messages 内容返回固定回复。"""
        from joker_test.llm.providers.anthropic import extract_text  # noqa: PLC0415
        from joker_test.trace import trace_llm  # noqa: PLC0415

        prompt = _get_prompt_text(messages)
        start = time.monotonic()

        # tool_use 模式
        if tools:
            tool_name = tools[0].get("name", "execute_action") if tools else "execute_action"
            reply: Message = {
                "content": [{
                    "type": "tool_use",
                    "name": tool_name,
                    "input": {"action": "click", "target": "设置"},
                }]
            }
        elif "冒烟测试用例" in prompt:
            reply = _MOCK_SMOKE_TEST_REPLY
        elif "is_false_positive" in prompt:
            reply = _MOCK_REVIEW_REPLY
        elif ("JSON 数组" in prompt or "json" in prompt.lower()) and len(messages) > 1:
            reply = {"content": [{"type": "text", "text": json.dumps(copy.deepcopy(_MOCK_CHARTERS), ensure_ascii=False)}]}
        elif not messages or len(messages) <= 1:
            reply = _ARCHITECT_REPLY
        else:
            reply = _ANALYST_REPLY

        duration = round(time.monotonic() - start, 2)
        reply_text = extract_text(reply)
        trace_llm(
            prompt[:200],
            reply_text[:200] if reply_text else "(空)",
            duration,
            model="mock",
            prompt_dump=format_trace_messages(messages),
            reply_dump=json.dumps(reply, ensure_ascii=False, indent=2),
        )
        return reply

    def parse(
        self,
        *,
        messages: list[dict[str, Any]],
        response_model: type,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        """模拟 parse：用 response_model 默认值构造返回。"""
        try:
            return response_model()
        except Exception:
            return None

    def stream(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        """模拟 stream：返回简单迭代器。"""

        class _MockStream:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def text_stream(self):
                yield "Mock stream output"

        return _MockStream()

    def count_tokens(
        self,
        *,
        messages: list[dict[str, Any]],
    ) -> int:
        """模拟 count_tokens：返回固定值。"""
        return 100


__all__ = ["MockProvider"]
