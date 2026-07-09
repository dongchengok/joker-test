"""AnthropicProvider —— 通用 Anthropic Message 协议 LLM provider。

适配所有兼容 Anthropic 协议的 LLM 服务（端点 /v1/messages，content blocks 格式）：
- 智谱 GLM（glm-4-flash / glm-5v-turbo，Bearer 认证）
- 小米 MiMo（mimo-v2.5，x-api-key 认证）
- Anthropic 官方 Claude（claude-3.5-sonnet，x-api-key 认证）
- 其他兼容服务

选 Anthropic 协议（而非 OpenAI）：与现有 Message 类型（content: list[dict]）零摩擦，
多模态 image block 原生支持（glm-5v-turbo / mimo-v2.5 等全模态模型）。

认证方式：不同服务商不同（Bearer / x-api-key），由 auth_mode 参数控制。

依赖：纯标准库（urllib），不引 httpx/requests。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Literal

from joker_test.llm.base import Message

AuthMode = Literal["bearer", "x-api-key"]


def load_env() -> dict[str, str]:
    """从 .env 读配置（轻量实现，不引 python-dotenv）。环境变量优先。

    公开给 glm/mimo 等兄弟 provider 复用（§11.3：包外会 import → 公开，无 `_` 前缀）。
    """
    config: dict[str, str] = {}
    env_path = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
    for k, v in os.environ.items():
        if k.startswith(("ZHIPU_", "MIMO_", "ANTHROPIC_")):
            config[k] = v
    return config


class AnthropicProvider:
    """通用 Anthropic Message 协议 LLM provider。

    满足 LLMProvider 协议（结构性子类型）。支持 text + image（多模态）。

    Args:
        api_key: API key
        base_url: 端点根（如 https://open.bigmodel.cn/api/anthropic）
        model: 模型名（如 glm-4-flash / mimo-v2.5）
        auth_mode: 认证方式（"bearer" 或 "x-api-key"），不同服务商不同
        max_tokens: 单次回复上限

    用法::
        provider = AnthropicProvider(
            api_key="tp-xxx", base_url="https://token-plan-cn.xiaomimimo.com/anthropic",
            model="mimo-v2.5", auth_mode="x-api-key",
        )
        msg = provider.simple_converse("你好", [])
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        auth_mode: AuthMode = "x-api-key",
        max_tokens: int = 4096,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._auth_mode = auth_mode
        self._max_tokens = max_tokens

    def _build_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if self._auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {self._api_key}"
        else:  # x-api-key
            headers["x-api-key"] = self._api_key
        return headers

    def simple_converse(
        self,
        prompt: str,
        messages: list[Message],
        *,
        reasoning: int = 0,
        images: list[str] | None = None,
    ) -> Message:
        """发一次对话，返回 assistant 回复（Anthropic message 格式）。

        reason 参数对大多数服务无效（保留接口兼容），忽略。

        Args:
            prompt: 文本 prompt
            messages: 历史消息（多轮对话）
            reasoning: 保留参数（大多数服务忽略）
            images: 图片 base64 列表（不加 data: 前缀）。传入时自动拼成 Anthropic
                image block，让多模态模型（mimo-v2.5/glm-5v 等）能"看图"。
                单张图片典型用法：images=[base64.b64encode(...).decode()]
        """
        full_messages: list[dict[str, Any]] = [dict(m) for m in messages]
        # 拼 content：先图片后文字（多模态标准顺序）
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
        full_messages.append({
            "role": "user",
            "content": content,
        })

        body = json.dumps({
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": full_messages,
        }).encode("utf-8")

        url = f"{self._base_url}/v1/messages"
        req = urllib.request.Request(
            url, data=body, headers=self._build_headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))  # type: ignore[return-value]
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8")
            raise RuntimeError(f"LLM API 错误（HTTP {e.code}, {self._model}@{self._base_url}）: {err}") from e

    def converse_json(
        self,
        messages: list[Message],
        instruction: str,
    ) -> list[dict[str, Any]]:
        """让 LLM 按 instruction 输出 JSON 数组。"""
        msg = self.simple_converse(instruction, messages)
        text = extract_text(msg)
        return parse_json_array(text)


def extract_text(msg: Message) -> str:
    """从 Anthropic message 提取纯文本（拼所有 text block，忽略 thinking）。"""
    parts: list[str] = []
    for block in msg.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def parse_json_array(text: str) -> list[dict[str, Any]]:
    """从文本提取 JSON 数组（容错：```json 围栏 或 裸 [...]）。"""
    import re  # noqa: PLC0415

    m = re.search(r"```(?:json)?\s*\n(\[.*?\])\s*\n```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"无法从 LLM 回复解析 JSON 数组。前 200 字: {text[:200]}")


__all__ = ["AnthropicProvider", "AuthMode", "load_env", "extract_text", "parse_json_array"]
