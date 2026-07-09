"""joker_test.llm —— LLM 调用抽象层（M0 解耦地基）。

公开 LLMProvider 协议、Message 类型、MockProvider 实现。
具体实现（Mock/Bedrock）在 providers/ 子目录。
"""

from joker_test.llm.base import LLMProvider, Message
from joker_test.llm.providers.mock import MockProvider

__all__ = ["LLMProvider", "Message", "MockProvider"]
