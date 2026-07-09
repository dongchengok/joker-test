"""LLM Provider 协议 —— joker-test 的 LLM 调用接缝。

这是 M0 解耦 SpecOps-src 硬依赖的核心产物（roadmap R-ADR-3 / R-ADR-6）。

设计要点：
- **async-friendly**：当前为同步协议。签名不阻碍未来加 async 变体
  （MCP sampling 是异步的，roadmap R-ADR-9 保留余地）。当前调用方都是同步的，
  强行 async 反而要改 charter_gen 主流程，故现在不引入。
- **对齐现有调用点**：协议方法签名严格对齐 charter_gen.py 原来对
  converse.simple_converse / operate.converse_json 的调用（行 198/205/211）。
- **三种实现**：BedrockProvider（独立模式主力）、MockProvider（测试/CI）、
  MCPSamplingProvider（R-ADR-9 降级，仅签名兼容，不预留实现）。
"""

from __future__ import annotations

from typing import Any, Protocol, TypedDict, runtime_checkable


class Message(TypedDict):
    """LLM 一次回复的消息结构。

    对齐现有 charter_gen._log_message 解析的 dict 格式（Bedrock converse 返回结构）：
      content: list[dict]，每个 dict 形如
        {"text": "..."}                 —— 文本回复
        {"reasoningContent": {...}}      —— thinking 块
    """

    content: list[dict[str, Any]]


@runtime_checkable
class LLMProvider(Protocol):
    """LLM 调用接缝协议。所有业务代码（generate_charters 等）只面向此协议，
    不关心 LLM 从哪来（自带 / 借 harness / Mock）。

    实现者：
      - BedrockProvider：从 SpecOps-src 包裹 AWS Bedrock（独立模式）
      - MockProvider：返回固定 JSON（测试/CI，M0 实现）
      - MCPSamplingProvider：借 harness LLM（R-ADR-9 低优先级，仅签名兼容）
    """

    def simple_converse(
        self,
        prompt: str,
        messages: list[Message],
        *,
        reasoning: int = 0,
        images: list[str] | None = None,
    ) -> Message:
        """发送一次对话，返回 assistant 回复消息。

        Args:
            prompt: 本次要发的 prompt（user message 内容）
            messages: 已有对话历史（Architect 的输出会追加进来给 Analyst）
            reasoning: reasoning token 预算（Bedrock 特有，0 表示不启用）
            images: 图片 base64 列表（多模态，无 data: 前缀）。
                None=纯文本；传入=多模态模型看图（mimo-v2.5/glm-5v 等）
        """
        ...

    def converse_json(
        self,
        messages: list[Message],
        instruction: str,
    ) -> list[dict[str, Any]]:
        """让 LLM 按 instruction 把对话内容提取为结构化 JSON 数组。

        Args:
            messages: 完整对话历史（含 Architect + Analyst 的回复）
            instruction: 提取指令（通常是 JSON schema 描述）

        Returns:
            结构化 JSON 数组。charter_gen 当 list[dict] 用（每个 dict 是一个 Charter）。
        """
        ...


__all__ = ["LLMProvider", "Message"]
