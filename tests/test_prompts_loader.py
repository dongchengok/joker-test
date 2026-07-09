"""prompts/loader 渲染层测试。

验证 prompt 从独立文件加载 + Jinja2 渲染正确（M0 prompt 抽取重构的核心验证）。
"""

from __future__ import annotations

import datetime

import pytest

from joker_test.prompts import (
    load_analyst_checklist,
    load_bug_definition,
    load_charter_schema,
    load_default_heuristics,
    load_default_personas,
    render_analyst_prompt,
    render_architect_prompt,
)

# ============== 常量/数据加载 ==============

def test_load_bug_definition_has_key_phrase() -> None:
    """BUG_DEFINITION 应含关键短语"合理预期"。"""
    text = load_bug_definition()
    assert "合理预期" in text
    assert "视觉异常" in text


def test_load_analyst_checklist_six_items() -> None:
    """ANALYST_CHECKLIST 应有 6 条（对应论文 6 维度）。"""
    items = load_analyst_checklist()
    assert len(items) == 6
    # 第 5 条决定 Phase 4 是否需要内存读取（关键契约）
    assert any("修改游戏环境" in item for item in items)


def test_load_default_personas_five() -> None:
    """DEFAULT_PERSONAS 应有 5 种人格。"""
    personas = load_default_personas()
    assert len(personas) == 5
    names = [p["name"] for p in personas]
    assert "破坏狂" in names and "贪婪者" in names


def test_load_default_heuristics_seven() -> None:
    """DEFAULT_HEURISTICS 应有 7 条。"""
    hs = load_default_heuristics()
    assert len(hs) == 7
    assert any("极端值" in h for h in hs)


# ============== 模板渲染 ==============

@pytest.fixture
def sample_game_meta() -> dict:
    return {
        "game_name": "测试游戏",
        "overview": "这是一个测试用游戏概述。",
        "load_save": "chapter1",
    }


@pytest.fixture
def sample_targets() -> list[dict]:
    return [{"id": 1, "name": "铁匠铺", "description": "武器强化"}]


def test_render_architect_prompt_basic(
    sample_game_meta: dict, sample_targets: list[dict]
) -> None:
    """architect prompt 应注入所有变量，含 XML 标签结构。"""
    personas = load_default_personas()
    heuristics = load_default_heuristics()
    prompt = render_architect_prompt(sample_targets, sample_game_meta, personas, heuristics)

    # 基本变量注入
    assert "测试游戏" in prompt
    assert "铁匠铺" in prompt
    assert "破坏狂" in prompt
    # XML 标签结构（Anthropic 推荐）
    assert "<context>" in prompt and "</context>" in prompt
    assert "<targets>" in prompt and "</targets>" in prompt
    assert "<bug_definition>" in prompt
    # 今日日期注入
    assert datetime.date.today().isoformat() in prompt


def test_render_architect_prompt_json_safe(
    sample_game_meta: dict, sample_targets: list[dict]
) -> None:
    """关键：targets JSON 注入不应破坏（验证 Jinja2 解决了 f-string JSON 冲突）。"""
    prompt = render_architect_prompt(
        sample_targets, sample_game_meta,
        load_default_personas(), load_default_heuristics(),
    )
    # targets 里的 JSON 花括号应原样存在（Jinja2 不冲突）
    assert '"id": 1' in prompt or '"id":1' in prompt


def test_render_analyst_prompt_basic(sample_game_meta: dict) -> None:
    """analyst prompt 应注入编号 checklist。"""
    prompt = render_analyst_prompt(sample_game_meta)
    assert "checklist" in prompt.lower()
    # 6 条编号
    for i in range(1, 7):
        assert f"{i}." in prompt


def test_render_analyst_prompt_with_game_specific() -> None:
    """game_specific 指令应注入到对应标签内。"""
    meta = {"analyst_extra_instructions": "额外要求：关注经济平衡"}
    prompt = render_analyst_prompt(meta)
    assert "额外要求：关注经济平衡" in prompt
    assert "<game_specific_instructions>" in prompt


def test_render_architect_prompt_missing_var_raises() -> None:
    """StrictUndefined：占位符对应的上下文缺失应报错（不静默成空串）。"""
    # 直接调 _render 测试机制（绕过便利函数）
    from joker_test.prompts.loader import _render

    with pytest.raises(Exception):  # noqa: B017 - StrictUndefined 抛 UndefinedError
        _render("architect.md.j2", today="x")  # 缺大量必需变量


def test_load_charter_schema_has_env_field() -> None:
    """charter schema 应含 charter_changes_game_state（字段重命名契约的源字段）。"""
    schema = load_charter_schema()
    assert "charter_changes_game_state" in schema
    assert "env_probing_required" not in schema  # 这是 write_charter 转写后的字段
