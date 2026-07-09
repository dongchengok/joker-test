"""插件加载器（DESIGN §4.5.3）。

自动发现并加载游戏插件。类似 pytest 插件机制：
- 扫描 plugin_dir 下的 .py 文件
- 找 GamePlugin 协议的实现（结构性子类型，isinstance 判断）
- 返回第一个找到的插件（或 DefaultPlugin）

MVP 简化：只加载一个插件（一个游戏一个插件）。多插件管理推迟。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from joker_test.plugins.base import GamePlugin


def load_plugin(plugin_path: str | Path | None = None) -> GamePlugin:
    """加载游戏插件。

    Args:
        plugin_path: 插件文件路径（.py）。None 返回 DefaultPlugin。

    Returns:
        GamePlugin 实现。找不到返回 DefaultPlugin。

    用法::
        plugin = load_plugin("plugins/spd_plugin.py")
        rules = plugin.get_validation_rules()
    """
    from joker_test.plugins.base import DefaultPlugin, GamePlugin  # noqa: PLC0415

    if plugin_path is None:
        return DefaultPlugin()

    path = Path(plugin_path)
    if not path.exists():
        return DefaultPlugin()

    # 动态加载 .py 文件（用 importlib）
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        return DefaultPlugin()

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001  # 插件加载失败不阻断，降级 DefaultPlugin
        return DefaultPlugin()

    # 找 GamePlugin 协议的实现（结构性子类型）
    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        # 跳过类定义本身和非实例
        if isinstance(obj, GamePlugin) and not isinstance(obj, type):
            return obj

    return DefaultPlugin()


__all__ = ["load_plugin"]
