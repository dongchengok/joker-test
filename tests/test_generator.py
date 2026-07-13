"""用例生成器测试（M3a 核心，MockProvider 驱动）。

验证 SmokeTestGenerator：StateMap → LLM → pytest 代码 + Pydantic spec → 质量兜底通过。
这是 M3a 的完成标志（生成的代码 ruff/ast.parse 通过）。
"""

from __future__ import annotations

import ast

import pytest

from joker_test.explorer.types import Screen, StateMap, UIElement
from joker_test.generator import GeneratedTest, QualityError, SmokeTestGenerator
from joker_test.generator.generator import _pair_into_generated_tests, _parse_code_blocks
from joker_test.llm.base import build_user_message
from joker_test.llm.providers.mock import MockProvider

# ============== fixture ==============

@pytest.fixture
def sample_state_map() -> StateMap:
    """最小 StateMap（1 个界面 + 1 个按钮）。"""
    return StateMap(
        screens=[
            Screen(
                id="root",
                elements=[
                    UIElement(type="button", text="背包", bbox=(0.5, 0.5, 0.2, 0.1)),
                ],
                exits=[],
                entry=None,
                fingerprint="abc123",
            ),
        ],
        root_screen_id="root",
        explored_at="2026-07-06T00:00:00Z",
        backend_info={"type": "FakeBackend"},
    )


@pytest.fixture
def sample_game_meta() -> dict:
    return {
        "game_name": "测试游戏",
        "overview": "一个测试用游戏。",
        "systems": [{"system": "inventory"}],
    }


# ============== 生成器核心 ==============

def test_generate_returns_generated_tests(
    sample_state_map: StateMap, sample_game_meta: dict
) -> None:
    """生成器应返回非空 GeneratedTest 列表。"""
    gen = SmokeTestGenerator(MockProvider())
    tests = gen.generate(sample_state_map, sample_game_meta)
    assert len(tests) >= 1
    assert all(isinstance(t, GeneratedTest) for t in tests)


def test_generated_test_has_code_and_spec(
    sample_state_map: StateMap, sample_game_meta: dict
) -> None:
    """生成的测试应含 test 代码 + spec 代码（两层分离，ADR-008）。"""
    gen = SmokeTestGenerator(MockProvider())
    tests = gen.generate(sample_state_map, sample_game_meta)
    t = tests[0]
    assert t.test_code  # 非空
    assert t.spec_code  # 非空
    assert t.test_filename.startswith("test_")
    assert t.spec_filename.endswith("_spec.py")


def test_generated_code_passes_ast_parse(
    sample_state_map: StateMap, sample_game_meta: dict
) -> None:
    """生成的代码必须语法正确（ast.parse 通过）—— M3a 质量底线。"""
    gen = SmokeTestGenerator(MockProvider())
    tests = gen.generate(sample_state_map, sample_game_meta)
    for t in tests:
        ast.parse(t.test_code)  # 不抛异常即通过
        ast.parse(t.spec_code)


def test_generated_code_contains_test_function(
    sample_state_map: StateMap, sample_game_meta: dict
) -> None:
    """生成的 test 代码应含 test_ 开头的函数。"""
    gen = SmokeTestGenerator(MockProvider())
    tests = gen.generate(sample_state_map, sample_game_meta)
    t = tests[0]
    tree = ast.parse(t.test_code)
    func_names = [
        n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
    ]
    assert any(name.startswith("test_") for name in func_names)


def test_generated_spec_contains_pydantic_model(
    sample_state_map: StateMap, sample_game_meta: dict
) -> None:
    """生成的 spec 代码应含 Pydantic BaseModel 子类。"""
    gen = SmokeTestGenerator(MockProvider())
    tests = gen.generate(sample_state_map, sample_game_meta)
    t = tests[0]
    tree = ast.parse(t.spec_code)
    class_names = [
        n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
    ]
    assert len(class_names) >= 1  # 至少一个 Model 类


# ============== 质量兜底 ==============

def test_quality_checker_rejects_syntax_error() -> None:
    """质量检查应拒绝语法错误的代码。"""
    from joker_test.generator.quality import QualityChecker

    bad = GeneratedTest(
        system="bad",
        test_code="def broken(:\n    pass",  # 语法错误
        spec_code="x = 1\n",
        test_filename="test_bad.py",
        spec_filename="bad_spec.py",
    )
    with pytest.raises(QualityError, match="语法错误"):
        QualityChecker().check(bad)


def test_quality_checker_accepts_valid_code() -> None:
    """质量检查应通过合法代码。"""
    from joker_test.generator.quality import QualityChecker

    good = GeneratedTest(
        system="good",
        test_code="def test_ok():\n    assert True\n",
        spec_code="x = 1\n",
        test_filename="test_good.py",
        spec_filename="good_spec.py",
    )
    QualityChecker().check(good)  # 不抛即通过


# ============== 代码块解析（内部函数）==============

def test_parse_code_blocks_basic() -> None:
    """解析器应提取 ### filename + ```python 代码块。"""
    text = (
        "### test_a.py\n"
        "```python\n"
        "x = 1\n"
        "```\n"
        "### a_spec.py\n"
        "```python\n"
        "y = 2\n"
        "```"
    )
    blocks = _parse_code_blocks(text)
    assert len(blocks) == 2
    assert blocks[0] == ("test_a.py", "x = 1")
    assert blocks[1] == ("a_spec.py", "y = 2")


def test_parse_code_blocks_empty() -> None:
    """无代码块的文本应返回空列表。"""
    assert _parse_code_blocks("普通文本，无代码块") == []


def test_pair_into_generated_tests() -> None:
    """配对器应把 test_*.py 和 *_spec.py 按 system 配对。"""
    blocks = [
        ("test_inventory.py", "def test_x(): pass"),
        ("inventory_spec.py", "class InventorySpec: pass"),
    ]
    tests = _pair_into_generated_tests(blocks)
    assert len(tests) == 1
    t = tests[0]
    assert t.system == "inventory"
    assert "test_x" in t.test_code
    assert "InventorySpec" in t.spec_code
    assert t.test_filename == "test_inventory.py"
    assert t.spec_filename == "inventory_spec.py"


def test_pair_allows_test_without_spec() -> None:
    """无配对 spec 时，spec_code 应为空字符串。"""
    blocks = [("test_lonely.py", "def test_x(): pass")]
    tests = _pair_into_generated_tests(blocks)
    assert len(tests) == 1
    assert tests[0].spec_code == ""


# ============== MockProvider 向后兼容（charter 生成不变）==============

def test_mock_provider_charter_mode_unchanged() -> None:
    """MockProvider 的 charter 生成行为不应被 M3a 改动影响。"""
    provider = MockProvider()
    # charter 场景：prompt 不含"冒烟测试用例"
    msg = provider.create(messages=[build_user_message("生成 Charter 草稿")])
    text = msg["content"][0].get("text", "")
    assert "Mock Architect" in text  # 原 charter 行为


def test_mock_provider_smoke_mode() -> None:
    """MockProvider 在用例生成场景应返回测试代码。"""
    provider = MockProvider()
    msg = provider.create(messages=[build_user_message("请基于界面地图生成冒烟测试用例")])
    text = msg["content"][0].get("text", "")
    assert "### test_" in text  # 用例生成回复
    assert "```python" in text
