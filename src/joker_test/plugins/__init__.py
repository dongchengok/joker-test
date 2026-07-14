"""joker_test.plugins —— AgentPlugin 插件系统（DESIGN §4.2，ADR-013）。

统一一套插件 Protocol：AgentPlugin（4 注入点：系统提示词/每步/动作建议/校验）。
游戏特化的数据/规则/工具也通过这 4 个注入点提供，不单独定义 GamePlugin。
"""

from joker_test.plugins.base import AgentPlugin, DefaultAgentPlugin
from joker_test.plugins.loader import load_plugin
from joker_test.plugins.manager import PluginManager
from joker_test.plugins.ocr import OCRPlugin

# 内置 AgentPlugin 注册表（配置文件里的插件名 → 类）
BUILTIN_PLUGINS: dict[str, type[AgentPlugin]] = {
    "ocr": OCRPlugin,
}

__all__ = [
    "AgentPlugin",
    "DefaultAgentPlugin",
    "PluginManager",
    "OCRPlugin",
    "BUILTIN_PLUGINS",
    "load_plugin",
]
