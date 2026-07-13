"""配置文件加载。

默认文件名 joker-test-config.json，不存在时自动生成。
格式 JSON，key 名自解释。
"""
from __future__ import annotations

import json
from pathlib import Path

_DEFAULT_CONFIG_FILE = "joker-test-config.json"

_DEFAULT_CONFIG: dict = {
    "plugins": ["ocr"],
    "plugin_path": None,
    "explore_strategy": "conversation",
    "max_steps": 20,
    "backend_name": "fake",
    "window_title": "",
}


def load_config(path: str | Path = _DEFAULT_CONFIG_FILE) -> dict:
    """加载 JSON 配置文件。

    不存在时用 _DEFAULT_CONFIG 写入后返回。
    存在时浅合并：顶层 key 缺失用默认值补。

    Args:
        path: 配置文件路径，默认 joker-test-config.json

    Returns:
        配置 dict
    """
    path = Path(path)
    if not path.exists():
        path.write_text(
            json.dumps(_DEFAULT_CONFIG, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"已生成默认配置文件: {path}")
        return _DEFAULT_CONFIG.copy()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    # 浅合并：顶层 key 缺失用默认值补
    return {**_DEFAULT_CONFIG, **loaded}


__all__ = ["load_config", "_DEFAULT_CONFIG", "_DEFAULT_CONFIG_FILE"]
