"""joker_test.plugins —— 游戏插件系统（DESIGN §4.5，ADR-003）。

两套插件：
- GamePlugin：游戏数据/规则/工具（v0.1 已定义，后续接入）
- AgentPlugin：探索注入点（系统提示词/每步/动作建议/校验）
"""

from joker_test.plugins.base import AgentPlugin, DefaultPlugin, GamePlugin
from joker_test.plugins.loader import load_plugin
from joker_test.plugins.manager import PluginManager
from joker_test.plugins.ocr import OCRPlugin

# 内置 AgentPlugin 注册表（配置文件里的插件名 → 类）
BUILTIN_PLUGINS: dict[str, type[AgentPlugin]] = {
    "ocr": OCRPlugin,
}

__all__ = [
    "GamePlugin",
    "DefaultPlugin",
    "AgentPlugin",
    "PluginManager",
    "OCRPlugin",
    "BUILTIN_PLUGINS",
    "load_plugin",
]
