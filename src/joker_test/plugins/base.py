"""Plugin 接口最小集（DESIGN §4.5 v0.1，ADR-003）。

游戏特化逻辑（数据/规则/工具/Reporter）走 Python 插件类，不走配置文件。
v0.1 只开放 4 类扩展点，不开放 Persona/Heuristics 扩展（ADR-007）。

设计要点：
- Protocol + @runtime_checkable（与全仓抽象一致）
- 结构性子类型：游戏插件不用显式继承 GamePlugin，实现方法即可
- 状态自洽：每个插件持有自己的上下文，不互相反向引用
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GamePlugin(Protocol):
    """游戏插件基类（v0.1 最小集，DESIGN §4.5.2）。

    一个游戏可以有专属插件，提供数据源/校验规则/工具/Reporter。
    v0.1 故意不开放 Persona/Heuristics 扩展（接口稳定性优先，ADR-007）。

    实现者只需实现需要的方法，不需要的返回空列表/dict。
    """

    name: str
    version: str

    def get_data_schemas(self) -> list[dict[str, Any]]:
        """提供游戏数据表 schema（如 NPC/武器/任务的合法值）。

        Returns:
            数据 schema 列表，每个形如 {"name": "weapons", "data": [...]}
        """
        ...

    def get_validation_rules(self) -> list[dict[str, Any]]:
        """提供自定义 Bug 检测规则。

        Returns:
            规则列表，每个形如 {"name": "gold_non_negative", "check": callable, "severity": "P0"}
        """
        ...

    def get_tools(self) -> dict[str, Callable[..., Any]]:
        """提供自定义操作工具（如内存读取、控制台命令）。

        Returns:
            工具名 → 可调用对象的映射
        """
        ...


# 默认空插件（无游戏特化时用）
class DefaultPlugin:
    """默认空插件（无任何游戏特化逻辑）。满足 GamePlugin 协议。"""

    name = "default"
    version = "0.1.0"

    def get_data_schemas(self) -> list[dict[str, Any]]:
        return []

    def get_validation_rules(self) -> list[dict[str, Any]]:
        return []

    def get_tools(self) -> dict[str, Callable[..., Any]]:
        return {}


__all__ = ["GamePlugin", "DefaultPlugin"]
