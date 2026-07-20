"""LLM Provider 协议 —— 对齐 anthropic SDK 接口。

唯一真实实现：AnthropicProvider（anthropic SDK + instructor）。
测试用：MockProvider（固定回复）。
trace 内置到 provider 内部。
"""
from __future__ import annotations

import json
from typing import Any, Protocol, TypedDict, runtime_checkable


class Message(TypedDict):
    """LLM 消息（对齐 Anthropic Messages API）。

    content: list[dict]，每个 block：
      {"type": "text", "text": "..."}
      {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}
      {"type": "tool_use", "name": "...", "input": {...}}
      {"type": "tool_result", "tool_use_id": "...", "content": [...]}
    """

    content: list[dict[str, Any]]


def build_user_message(
    prompt: str, images: list[str] | None = None
) -> dict[str, Any]:
    """拼装标准 user message（图片在前，文本在后）。

    Args:
        prompt: 文本内容
        images: base64 图片列表（无 data: 前缀）

    Returns:
        {"role": "user", "content": [image_blocks..., text_block]}
    """
    content: list[dict[str, Any]] = []
    if images:
        for img_b64 in images:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": img_b64,
                },
            })
    content.append({"type": "text", "text": prompt})
    return {"role": "user", "content": content}


def format_trace_messages(messages: list[dict[str, Any]]) -> str:
    """把 messages 序列化为可读的调试字符串（截断图片数据，保留完整文本）。

    供 provider 内部调 trace_llm(prompt_dump=...) 用。
    图片只显示类型和大小（base64 太长）；文本内容完整保留（trace 是诊断用的，
    截断会导致看不到完整的 OCR 元素列表等关键信息）。
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(f"[{role}] {content}")
        elif isinstance(content, list):
            texts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type", "")
                    if btype == "text" or "text" in block:
                        texts.append(block.get("text", ""))
                    elif btype == "image":
                        src = block.get("source", {})
                        texts.append(f"[图片: {src.get('media_type', '?')} {len(src.get('data', ''))}B]")
                    elif btype == "tool_use":
                        inp = block.get("input", {})
                        texts.append(f"[tool_use: {block.get('name', '?')} {json.dumps(inp, ensure_ascii=False)}]")
            parts.append(f"[{role}] {' '.join(texts)}")
    return "\n\n".join(parts)


@runtime_checkable
class LLMProvider(Protocol):
    """LLM 调用协议（对齐 anthropic SDK）。"""

    def create(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> Message:
        """创建消息（对齐 client.messages.create）。

        Args:
            messages: 完整对话历史（含当前 user message）
            model: 模型名（None=用默认）
            max_tokens: 最大输出 token（None=用默认）
            tools: 工具定义列表
            tool_choice: 工具选择策略

        Returns:
            Message（content 含 text/tool_use blocks）
        """
        ...

    def parse(
        self,
        *,
        messages: list[dict[str, Any]],
        response_model: type,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        """结构化输出（对齐 client.messages.parse，Pydantic 校验）。

        Args:
            messages: 对话历史
            response_model: Pydantic BaseModel 类
            model: 模型名
            max_tokens: 最大输出 token

        Returns:
            response_model 实例（Pydantic 校验过的对象）
        """
        ...

    def stream(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        """流式输出（对齐 client.messages.stream）。

        Returns:
            流式上下文管理器
        """
        ...

    def count_tokens(
        self,
        *,
        messages: list[dict[str, Any]],
    ) -> int:
        """估算 token 用量（对齐 client.messages.count_tokens）。"""
        ...


__all__ = ["LLMProvider", "Message", "build_user_message", "format_trace_messages"]
