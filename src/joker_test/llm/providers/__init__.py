"""joker_test.llm.providers —— LLM provider 实现。

每个 provider 是 LLMProvider 协议的一个实现：
- MockProvider：返回固定 JSON（测试/CI）
- BedrockProvider：包裹 SpecOps-src 调 AWS Bedrock（独立模式主力）
- AnthropicProvider：通用 Anthropic 协议（基础实现）
- GLMProvider：智谱 GLM（Anthropic 协议，Bearer 认证）
- MiMoProvider：小米 MiMo（Anthropic 协议，x-api-key 认证，全模态）
"""

from joker_test.llm.providers.anthropic import AnthropicProvider
from joker_test.llm.providers.bedrock import BedrockProvider, resolve_default_provider
from joker_test.llm.providers.glm import GLMProvider
from joker_test.llm.providers.mimo import MiMoProvider
from joker_test.llm.providers.mock import MockProvider

__all__ = [
    "MockProvider",
    "BedrockProvider",
    "AnthropicProvider",
    "GLMProvider",
    "MiMoProvider",
    "resolve_default_provider",
]
