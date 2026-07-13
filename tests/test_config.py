"""config.py 测试：自动生成 + 加载 + 默认值合并。"""
from __future__ import annotations

import json

from joker_test.config import _DEFAULT_CONFIG, load_config


def test_auto_generate_when_not_exists(tmp_path):
    """配置文件不存在时自动生成。"""
    cfg_path = tmp_path / "test-config.json"
    result = load_config(cfg_path)
    assert cfg_path.exists()
    assert result["plugins"] == ["ocr"]
    loaded = json.loads(cfg_path.read_text("utf-8"))
    assert loaded["plugins"] == ["ocr"]


def test_load_existing_config(tmp_path):
    """加载已有配置文件。"""
    cfg_path = tmp_path / "test-config.json"
    cfg_path.write_text(json.dumps({
        "plugins": ["ocr", "slider_hint"],
        "max_steps": 10,
    }), encoding="utf-8")
    result = load_config(cfg_path)
    assert result["plugins"] == ["ocr", "slider_hint"]
    assert result["max_steps"] == 10


def test_default_merge_for_missing_keys(tmp_path):
    """配置文件缺少部分 key 时用默认值补。"""
    cfg_path = tmp_path / "test-config.json"
    cfg_path.write_text(json.dumps({"max_steps": 5}), encoding="utf-8")
    result = load_config(cfg_path)
    assert result["max_steps"] == 5
    assert result["plugins"] == ["ocr"]
    assert result["explore_strategy"] == "conversation"


def test_default_config_has_required_keys():
    """_DEFAULT_CONFIG 包含所有必需 key。"""
    for key in ("plugins", "plugin_path", "explore_strategy", "max_steps", "backend_name", "window_title"):
        assert key in _DEFAULT_CONFIG
