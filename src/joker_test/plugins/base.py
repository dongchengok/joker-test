"""插件接口（DESIGN §4.2，ADR-003/ADR-013）。

AgentPlugin 是唯一的插件协议——4 个注入点（系统提示词/每轮对话/动作建议/校验）。
游戏特化逻辑（数据/规则/工具）也通过这 4 个注入点提供，不单独定义 GamePlugin。

设计要点：
- Protocol + @runtime_checkable（与全仓抽象一致）
- 结构性子类型：插件不用显式继承，实现方法即可
- 状态自洽：每个插件持有自己的上下文，不互相反向引用
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class AgentPlugin(Protocol):
    """测试 Agent 插件。通过注入点向探索流程提供信息。

    4 个注入点，每个返回空值 = 该插件不贡献此注入点的内容。
    实现者只需实现需要的方法，不需要的返回空串/None。
    """

    name: str

    def inject_system_prompt(self) -> str:
        """系统提示词注入点（固定，只拼接一次）。
        告诉 LLM 如何理解本插件提供的信息格式。返回空串 = 不注入。"""
        ...

    def inject_step(self, screenshot: Any, backend: Any, ctx: Any) -> str:
        """每轮对话注入点（动态，每步都调）。
        返回本步要追加到 user message 的文本。返回空串 = 不注入。"""
        ...

    def inject_action_hint(self, screenshot: Any, backend: Any, ctx: Any) -> str:
        """动作建议注入点（动态，每步都调）。
        给 LLM 额外的动作提示。返回空串 = 不注入。"""
        ...

    def validate(self, decision: Any, result: Any, backend: Any = None) -> str | None:
        """校验注入点（动作执行后调）。
        检查动作结果，返回问题描述（None = 无问题）。
        反馈文本会注入到下一步的 step_text 让 LLM 自我纠错。

        Args:
            decision: 本步决策（动作/目标/坐标）
            result: 执行结果（success/screen_changed/pixel_diff_ratio/error）
            backend: 当前 backend（操作后已刷新帧，可读 state 检查语义变化）
        """
        ...


class DefaultAgentPlugin:
    """默认空插件（无任何注入内容）。满足 AgentPlugin 协议。"""

    @property
    def name(self) -> str:
        return "default"

    def inject_system_prompt(self) -> str:
        return ""

    def inject_step(self, screenshot: Any, backend: Any, ctx: Any) -> str:
        return ""

    def inject_action_hint(self, screenshot: Any, backend: Any, ctx: Any) -> str:
        return ""

    def validate(self, decision: Any, result: Any, backend: Any = None) -> str | None:
        return None


__all__ = ["AgentPlugin", "DefaultAgentPlugin"]
