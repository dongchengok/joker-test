"""PluginManager —— 插件管理器，注入点统一调用入口 + 异常隔离。

职责：
- 拼接：把所有插件的注入内容按顺序拼成完整文本
- 异常隔离：每个插件调用 try/except，崩了跳过（ADR-009 同理）
- validate 反馈收集：收集所有插件的校验结果
"""
from __future__ import annotations

import logging
from typing import Any

from joker_test.plugins.base import AgentPlugin

_LOGGER = logging.getLogger(__name__)


class PluginManager:
    """插件管理器。注入点统一调用入口 + 异常隔离。

    Args:
        plugins: 按顺序排列的激活插件列表
    """

    def __init__(self, plugins: list[AgentPlugin]) -> None:
        self._plugins = plugins

    def build_system_prompt(self, base: str) -> str:
        """base + 所有插件的 system_prompt 片段。

        空片段跳过，异常插件跳过。
        """
        fragments: list[str] = []
        for p in self._plugins:
            try:
                frag = p.inject_system_prompt()
                if frag:
                    fragments.append(frag)
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("插件 %s inject_system_prompt 失败：%s", p.name, e)
        if not fragments:
            return base
        return base + "\n\n" + "\n\n".join(fragments)

    def build_step_text(
        self,
        screenshot: Any,
        backend: Any,
        ctx: Any,
        base: str,
        validate_feedback: str = "",
    ) -> str:
        """base + 所有插件的 step 注入 + action_hint 注入 + validate 反馈。

        每个注入点异常隔离。
        """
        from joker_test.trace import trace_event  # noqa: PLC0415

        parts: list[str] = [base]
        for p in self._plugins:
            try:
                injected = p.inject_step(screenshot, backend, ctx)
                if injected:
                    parts.append(injected)
                    trace_event("plugin_inject", {
                        "plugin": p.name, "inject_point": "step", "length": len(injected),
                        "content": injected,
                    })
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("插件 %s inject_step 失败：%s", p.name, e)
            try:
                hint = p.inject_action_hint(screenshot, backend, ctx)
                if hint:
                    parts.append(hint)
                    trace_event("plugin_inject", {
                        "plugin": p.name, "inject_point": "action_hint", "length": len(hint),
                        "content": hint,
                    })
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("插件 %s inject_action_hint 失败：%s", p.name, e)
        if validate_feedback:
            parts.append(validate_feedback)
        return "\n\n".join(parts)

    def validate(self, decision: Any, result: Any, backend: Any = None) -> str:
        """收集所有插件的校验结果，拼成反馈文本。空串 = 无问题。

        Args:
            decision: 本步决策
            result: 执行结果
            backend: 当前 backend（透传给插件，供语义校验用）
        """
        issues: list[str] = []
        for p in self._plugins:
            try:
                issue = p.validate(decision, result, backend=backend)
                if issue:
                    issues.append(f"[{p.name}] {issue}")
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("插件 %s validate 失败：%s", p.name, e)
        return "\n".join(issues)


__all__ = ["PluginManager"]
