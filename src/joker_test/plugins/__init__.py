"""joker_test.plugins —— 游戏插件系统（DESIGN §4.5，ADR-003）。

让每个游戏可以有专属扩展（数据/规则/工具/Reporter）。
v0.1 最小集：GamePlugin 协议 + DefaultPlugin + 加载器。
"""

from joker_test.plugins.base import DefaultPlugin, GamePlugin
from joker_test.plugins.loader import load_plugin

__all__ = ["GamePlugin", "DefaultPlugin", "load_plugin"]
