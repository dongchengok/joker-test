"""TracingProvider —— 包装任意 LLMProvider，自动记录到全局 tracer。

不侵入业务代码：把真实 provider 包一层，simple_converse/converse_json 调用自动记录。
完整 prompt/reply 内联进 Tracer 的 trace.html 折叠卡片，方便调试。

全局 tracer 模式：不再传 tracer 参数，内部调全局 trace_llm（首次打点惰性建 Tracer，
进程退出 atexit 自动 finalize）。业务代码零感知。

用法::

    real = MiMoProvider()
    wrapped = TracingProvider(real, model="mimo-v2.5")
    # 之后用 wrapped 代替 real，所有 LLM 调用自动被 trace
    charter_gen.generate_charters(..., provider=wrapped)
"""

from __future__ import annotations

import time
from typing import Any

from joker_test.llm.base import LLMProvider, Message


class TracingProvider:
    """LLMProvider 包装器：自动把每次 LLM 调用记录到全局 tracer。

    满足 LLMProvider 协议（结构性子类型，无需继承）。
    状态自洽：只持有被包装的 provider 引用。

    Args:
        inner: 被包装的真实 provider（MiMo/GLM/Bedrock）
        model: 模型名（记录到 trace，如 "mimo-v2.5"）
    """

    def __init__(
        self,
        inner: LLMProvider,
        model: str = "",
    ) -> None:
        self._inner = inner
        self._model = model

    def simple_converse(
        self,
        prompt: str,
        messages: list[Message],
        *,
        reasoning: int = 0,
        images: list[str] | None = None,
    ) -> Message:
        """包装 simple_converse：记录 prompt 摘要 + 完整 dump + 耗时，再返回原结果。"""
        from joker_test.trace import trace_error, trace_llm  # noqa: PLC0415

        start = time.monotonic()
        try:
            reply = self._inner.simple_converse(
                prompt, messages, reasoning=reasoning, images=images
            )
        except Exception as e:
            duration = round(time.monotonic() - start, 2)
            trace_error(
                f"LLM simple_converse 异常: {e}",
                {"duration_s": duration, "prompt_chars": len(prompt),
                 "image_count": len(images) if images else 0},
            )
            raise

        duration = time.monotonic() - start
        reply_text = _extract_text(reply)
        trace_llm(
            prompt_summary=_summarize_prompt(prompt),
            reply_summary=reply_text[:200] if reply_text else "(空)",
            duration=duration,
            model=self._model,
            prompt_dump=prompt,
            reply_dump=reply_text,
        )
        return reply

    def converse_json(
        self,
        messages: list[Message],
        instruction: str,
    ) -> list[dict[str, Any]]:
        """包装 converse_json：记录指令 + 结果摘要 + 耗时。"""
        from joker_test.trace import trace_error, trace_llm  # noqa: PLC0415

        start = time.monotonic()
        try:
            result = self._inner.converse_json(messages, instruction)
        except Exception as e:
            duration = round(time.monotonic() - start, 2)
            trace_error(
                f"LLM converse_json 异常: {e}",
                {"duration_s": duration, "instruction_chars": len(instruction)},
            )
            raise

        duration = time.monotonic() - start
        trace_llm(
            prompt_summary=f"converse_json 指令: {instruction[:100]}",
            reply_summary=f"返回 {len(result)} 个 JSON 对象",
            duration=duration,
            model=self._model,
            prompt_dump=instruction,
            reply_dump=str(result)[:2000],
        )
        return result


def _extract_text(msg: Message) -> str:
    """从 Message 提取文本（兼容 Anthropic content block 格式）。"""
    content = msg.get("content", [])
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def _summarize_prompt(prompt: str) -> str:
    """给 prompt 生成一行摘要（取前 100 字 + 检测关键标记）。"""
    markers = []
    if "<uimap>" in prompt.lower() or "uimap" in prompt.lower():
        markers.append("UIMap")
    if "charter" in prompt.lower():
        markers.append("Charter")
    if "测试" in prompt and "生成" in prompt:
        markers.append("生成测试")
    prefix = f"[{'+'.join(markers)}] " if markers else ""
    return prefix + prompt.replace("\n", " ")[:100]


__all__ = ["TracingProvider"]
