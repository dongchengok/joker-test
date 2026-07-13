"""joker_test.llm —— LLM 调用抽象层。

公开 LLMProvider 协议、Message 类型、MockProvider（CI 测试）。
真实实现 AnthropicProvider 在 providers/ 子目录。
"""
from joker_test.llm.base import LLMProvider, Message
from joker_test.llm.providers.mock import MockProvider

__all__ = ["LLMProvider", "Message", "MockProvider"]
