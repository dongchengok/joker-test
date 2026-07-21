"""AnthropicProvider —— 用 anthropic SDK + instructor 的 LLM provider。

对齐 anthropic SDK 接口：create/parse/stream/count_tokens。
通过构造参数配置连接任意 Anthropic 兼容服务（MiMo/GLM/Claude 等）。
trace 内置：每次 create 自动调全局 trace_llm。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, cast

from joker_test.llm.base import format_trace_messages

_LOGGER = logging.getLogger(__name__)

_ENV_CANDIDATES = [
    Path.cwd() / ".env",
    Path.cwd().parent / ".env",
]


def load_env() -> dict[str, str]:
    """从 .env 文件加载配置。"""
    for env_path in _ENV_CANDIDATES:
        if env_path.exists():
            cfg: dict[str, str] = {}
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
            for k in cfg:
                cfg[k] = os.environ.get(k, cfg[k])
            return cfg
    return {}


def extract_text(msg: Any) -> str:
    """从 Message 提取文本（兼容 text/tool_use block 及无 type 旧格式）。"""
    content = msg.get("content", []) if isinstance(msg, dict) else getattr(msg, "content", [])
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            btype = block.get("type", "text" if "text" in block else "")
            if btype == "text" or ("text" in block and "type" not in block):
                parts.append(block.get("text", ""))
            elif btype == "tool_use":
                parts.append(str(block.get("input", {})))
    return "\n".join(parts)


def parse_json_array(text: str) -> list[dict[str, Any]]:
    """从文本提取 JSON 数组（容错）。"""
    import json  # noqa: PLC0415

    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
    except json.JSONDecodeError:
        pass
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return []




def _has_tool_use(result: dict[str, Any]) -> bool:
    """检查返回是否含 tool_use block。"""
    return any(
        isinstance(b, dict) and b.get("type") == "tool_use"
        for b in result.get("content", [])
    )


class AnthropicProvider:
    """Anthropic 协议 LLM provider（对齐 SDK 接口）。

    Args:
        api_key: API key（默认从 .env 读 MIMO_API_KEY）
        base_url: API 端点（默认从 .env 读 MIMO_BASE_URL）
        model: 模型名（默认从 .env 读 MIMO_MODEL）
        max_tokens: 默认最大输出 token
        max_retries: SDK 自动重试次数（默认 5）
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        max_retries: int = 5,
        thinking_enabled: bool = True,
        thinking_budget_tokens: int = 8000,
    ) -> None:
        cfg = load_env()
        self._api_key = api_key or cfg.get("MIMO_API_KEY", "")
        self._base_url = base_url or cfg.get("MIMO_BASE_URL", "")
        self._model = model or cfg.get("MIMO_MODEL", "mimo-v2.5")
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._thinking_enabled = thinking_enabled
        self._thinking_budget_tokens = thinking_budget_tokens

        if not self._api_key:
            raise ValueError("缺 API key。请在 .env 设 MIMO_API_KEY 或传 api_key 参数。")

        import anthropic  # noqa: PLC0415

        self._client = anthropic.Anthropic(
            api_key=self._api_key,
            base_url=self._base_url,
            max_retries=self._max_retries,
        )
        self._instructor_client: Any = None

    def create(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """创建消息（对齐 client.messages.create），内置 trace + tool_use 重试。"""
        from joker_test.trace import trace_llm  # noqa: PLC0415

        # Anthropic 约束：thinking 启用时 max_tokens 必须 > thinking.budget_tokens
        # （thinking 预算含在 max_tokens 总量内）。默认值或调用方传入值过小时自动抬高，
        # 否则真 Claude 端点会 400（MiMo 等宽松端点不校验，但换端点即炸）。
        effective_max = max_tokens or self._max_tokens
        if self._thinking_enabled and effective_max <= self._thinking_budget_tokens:
            effective_max = self._thinking_budget_tokens + 4096

        kwargs: dict[str, Any] = {
            "model": model or self._model,
            "max_tokens": effective_max,
            "messages": messages,
        }
        if system:
            # Anthropic 要求 system 走顶层参数，不能塞 messages 数组（role=system 会 400）
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        if self._thinking_enabled:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": self._thinking_budget_tokens}

        # tool_use 自动重试：重试时把 tool_choice 升级为强制 any（模型漂移的硬兜底），
        # 端点拒绝强制（如真 Anthropic 的 thinking 限制，400）时自动降回原始值。
        retry_messages = messages
        original_tool_choice = kwargs.get("tool_choice")
        caller_forced = (
            isinstance(original_tool_choice, dict)
            and original_tool_choice.get("type") not in (None, "auto", "none")
        )
        escalated = False
        for attempt in range(3):
            start = time.monotonic()
            try:
                response = self._client.messages.create(**kwargs)
            except Exception as e:
                if escalated and getattr(e, "status_code", None) == 400:
                    _LOGGER.warning(
                        "强制 tool_choice=any 被端点拒绝(400)，降回 %s 重试",
                        original_tool_choice,
                    )
                    if original_tool_choice is None:
                        kwargs.pop("tool_choice", None)
                    else:
                        kwargs["tool_choice"] = original_tool_choice
                    escalated = False
                    continue
                duration = round(time.monotonic() - start, 2)
                trace_llm(
                    _get_last_text(retry_messages)[:200] or "",
                    f"ERROR: {e}",
                    duration,
                    model=kwargs["model"],
                    prompt_dump=format_trace_messages(retry_messages),
                    reply_dump=str(e),
                )
                raise RuntimeError(f"LLM API 错误（{kwargs['model']}@{self._base_url}）: {e}") from e

            duration = round(time.monotonic() - start, 2)
            result = _response_to_dict(response)
            reply_text = extract_text(result)
            trace_llm(
                _get_last_text(retry_messages)[:200] or "",
                reply_text[:200] if reply_text else "(空)",
                duration,
                model=kwargs["model"],
                prompt_dump=format_trace_messages(retry_messages),
                reply_dump=json.dumps(result, ensure_ascii=False, indent=2),
            )

            # 有 tools 但无 tool_use block → 重试
            if tools and not _has_tool_use(result) and attempt < 2:
                _LOGGER.warning("tool_use 未返回（attempt %d），重试", attempt + 1)
                # 剥离 thinking block：_response_to_dict 丢了 signature 字段，
                # 而 Anthropic 要求回传 thinking 必须带完整 signature，否则 400。
                # 与主路径（不回传 thinking）保持一致。
                clean_content = [b for b in result["content"] if b.get("type") != "thinking"]
                retry_messages = list(messages) + [
                    {"role": "assistant", "content": clean_content},
                    {"role": "user", "content": [{"type": "text", "text": (
                        "你没有调用工具。必须调用提供的工具输出结果，禁止纯文本回复。"
                    )}]},
                ]
                kwargs["messages"] = retry_messages
                # 模型漂移（auto 下纯文本回复）→ 升级为强制工具调用。
                # 强制被端点拒绝时上面的 except 会降回原始值。
                if not caller_forced and not escalated:
                    kwargs["tool_choice"] = {"type": "any"}
                    escalated = True
                continue

            return result

        return result

    def parse(
        self,
        *,
        messages: list[dict[str, Any]],
        response_model: type,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        """结构化输出（instructor + Pydantic 校验）。"""
        import instructor  # noqa: PLC0415

        if self._instructor_client is None:
            self._instructor_client = instructor.from_anthropic(self._client)

        return self._instructor_client.messages.create(
            model=model or self._model,
            max_tokens=max_tokens or self._max_tokens,
            messages=messages,
            response_model=response_model,
        )

    def stream(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        """流式输出（返回上下文管理器）。"""
        return self._client.messages.stream(
            model=model or self._model,
            max_tokens=max_tokens or self._max_tokens,
            # 我们的 dict 结构与 SDK MessageParam 结构一致，cast 消除类型差异
            messages=cast("Any", messages),
        )

    def count_tokens(
        self,
        *,
        messages: list[dict[str, Any]],
    ) -> int:
        """估算 token 用量。"""
        result = self._client.messages.count_tokens(
            model=self._model,
            messages=cast("Any", messages),
        )
        return result.input_tokens


def _response_to_dict(response: Any) -> dict[str, Any]:
    """把 SDK response 对象转为 dict（对齐 Message 格式）。"""
    result: dict[str, Any] = {"content": []}
    for block in response.content:
        if block.type == "text":
            result["content"].append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result["content"].append({
                "type": "tool_use",
                "name": block.name,
                "input": block.input,
            })
        elif block.type == "thinking":
            result["content"].append({
                "type": "thinking",
                "thinking": block.thinking,
            })
    return result


def _get_last_text(messages: list[dict[str, Any]]) -> str:
    """从 messages 最后一条 user message 提取文本。"""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", [])
            if isinstance(content, str):
                return content
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
                if isinstance(block, dict) and "text" in block:
                    return block.get("text", "")
    return ""


__all__ = ["AnthropicProvider", "load_env", "extract_text", "parse_json_array"]
