"""charter_gen + MockProvider 端到端测试。

这是 M0 解耦（R-ADR-3）的核心验证：证明 charter_gen.py 不再硬依赖 SpecOps-src，
用 MockProvider 即可离线跑通完整生成流程。无需网络、无需 LLM、无需 SpecOps-src、无需游戏。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from joker_test.charter_gen import generate_charters, write_charter
from joker_test.llm import MockProvider
from joker_test.llm.base import build_user_message

# ============== generate_charters 端到端 ==============

def test_generate_charters_with_mock_produces_files(
    tmp_path: Path, targets_file: Path, game_meta_file: Path
) -> None:
    """MockProvider 注入 generate_charters，应产出固定数量的 Charter JSON 文件。"""
    output_dir = tmp_path / "charters"
    generate_charters(
        str(targets_file),
        str(game_meta_file),
        output_dir=str(output_dir),
        target_ids=[1],              # MockProvider 固定返回 target_id=1 的 charter
        persona_filter=["破坏狂", "贪婪者"],  # Mock 覆盖这两个 persona
        provider=MockProvider(),
    )

    # MockProvider 固定返回 2 个 charter
    produced = list(output_dir.glob("*.json"))
    assert len(produced) == 2, f"应产出 2 个 charter 文件，实际 {len(produced)}: {produced}"


def test_generate_charters_no_provider_without_specops_raises(
    tmp_path: Path, targets_file: Path, game_meta_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """无 SpecOps-src 且未传 provider 时，应抛 RuntimeError（而非原 ImportError）。"""
    # 强制 DEFAULT_PROVIDER 为 None（模拟 SpecOps-src 不存在的环境）
    import joker_test.charter_gen as cg

    monkeypatch.setattr(cg, "DEFAULT_PROVIDER", None)

    with pytest.raises(RuntimeError, match="无可用 LLM provider"):
        generate_charters(
            str(targets_file),
            str(game_meta_file),
            output_dir=str(tmp_path / "out"),
            target_ids=[1],
        )


# ============== 字段重命名契约（charter_gen.py:322-325）==============

def test_write_charter_renames_field_contract(tmp_path: Path) -> None:
    """write_charter 必须把 charter_changes_game_state 转写为 env_probing_required。

    这是 Phase 1 → Phase 4 的契约（AGENTS.md footgun #3），不可破坏。
    """
    charter = {
        "charter_id": 1,
        "target_id": 1,
        "persona": "破坏狂",
        "target_system": "测试系统",
        "target_description": "测试描述",
        "load_save": "save",
        "goal": "目标",
        "exploration_targets": ["a"],
        "heuristics": ["b"],
        "expected_behaviors": ["c"],
        "coverage_dimensions": {"region": [], "function": [], "operation": [], "state": []},
        "time_budget_minutes": 30,
        "charter_changes_game_state": "yes",
        "severity_threshold": "P0",
    }

    write_charter(str(tmp_path), charter)

    produced = list(tmp_path.glob("*.json"))
    assert len(produced) == 1
    with open(produced[0], encoding="utf-8") as f:
        data = json.load(f)

    # 关键契约：字段已被转写
    assert "env_probing_required" in data, "env_probing_required 字段缺失（契约破坏）"
    assert "charter_changes_game_state" not in data, "原字段名残留（应已被 pop 掉）"
    assert data["env_probing_required"] == "yes"


def test_write_charter_renames_field_no_case(tmp_path: Path) -> None:
    """charter_changes_game_state='no' 应转写为 env_probing_required='no'。"""
    charter = {
        "charter_id": 2,
        "persona": "贪婪者",
        "target_system": "测",
        "charter_changes_game_state": "no",
    }
    write_charter(str(tmp_path), charter)
    data = json.load(next(tmp_path.glob("*.json")).open(encoding="utf-8"))
    assert data["env_probing_required"] == "no"


# ============== 文件名清洗 ==============

def test_write_charter_cleans_windows_filename(tmp_path: Path) -> None:
    """target_system 含 '/' 应被清洗（/ → '或'），文件名格式 T{id}_C{id}_{persona}_{system}。"""
    charter = {
        "charter_id": 3,
        "target_id": 5,
        "persona": "混乱中立",
        "target_system": "A/B 系统",
        "charter_changes_game_state": "no",
    }
    write_charter(str(tmp_path), charter)
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    # 文件名应含 T05_C03，且 / 被替换
    assert files[0].name.startswith("T05_C03_")
    assert "/" not in files[0].name


# ============== LLMProvider 协议结构化校验 ==============

def test_mock_provider_satisfies_protocol() -> None:
    """MockProvider 应满足 LLMProvider 协议（runtime_checkable）。"""
    from joker_test.llm.base import LLMProvider

    assert isinstance(MockProvider(), LLMProvider)


def test_mock_provider_create_returns_message() -> None:
    """create 应返回 Message dict（含 content 列表）。"""
    from joker_test.llm.providers.anthropic import extract_text, parse_json_array

    reply = MockProvider().create(messages=[build_user_message("test")])
    assert "content" in reply
    text = extract_text(reply)
    assert text  # 非空
    # MockProvider 的 architect reply 含 charter JSON
    charters = parse_json_array(text)
    assert isinstance(charters, list)
    assert all(isinstance(item, dict) for item in charters)
