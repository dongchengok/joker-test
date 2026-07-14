"""插件加载器（DESIGN §4.2.3）。

自动发现并加载 AgentPlugin 插件。类似 pytest 插件机制：
- 扫描 .py 文件
- 找 AgentPlugin 协议的实现（结构性子类型，isinstance 判断）
- 返回第一个找到的插件（或 DefaultAgentPlugin）

MVP 简化：只加载一个插件。多插件管理推迟。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from joker_test.plugins.base import AgentPlugin


def load_plugin(plugin_path: str | Path | None = None) -> AgentPlugin:
    """加载插件。

    Args:
        plugin_path: 插件文件路径（.py）。None 返回 DefaultAgentPlugin。

    Returns:
        AgentPlugin 实现。找不到返回 DefaultAgentPlugin。

    用法::
        plugin = load_plugin("plugins/spd_plugin.py")
        prompt_frag = plugin.inject_system_prompt()
    """
    from joker_test.plugins.base import AgentPlugin, DefaultAgentPlugin  # noqa: PLC0415

    if plugin_path is None:
        return DefaultAgentPlugin()

    path = Path(plugin_path)
    if not path.exists():
        return DefaultAgentPlugin()

    # 动态加载 .py 文件（用 importlib）
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        return DefaultAgentPlugin()

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001  # 插件加载失败不阻断，降级 DefaultAgentPlugin
        return DefaultAgentPlugin()

    # 找 AgentPlugin 协议的实现（结构性子类型）
    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        # 跳过类定义本身和非实例
        if isinstance(obj, AgentPlugin) and not isinstance(obj, type):
            return obj

    return DefaultAgentPlugin()


__all__ = ["load_plugin"]
