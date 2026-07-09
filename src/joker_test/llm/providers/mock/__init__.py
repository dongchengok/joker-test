"""MockProvider —— 返回固定 Charter JSON，用于离线/CI 验证。

无网络、无 LLM、无 SpecOps-src 依赖。是 M0 解耦后 charter_gen 能在 CI 跑通的关键。

固定返回的 Charter JSON 严格对齐 charter_gen._build_json_schema（约 276-300 行），
**刻意保留 charter_changes_game_state 字段**，用以验证 write_charter 的字段重命名契约
（charter_changes_game_state → env_probing_required，charter_gen.py:322-325）。
"""

from __future__ import annotations

from typing import Any

from joker_test.llm.base import Message

# 固定回复文本（简单文本，让 _log_message 在 --verbose 时有内容可解析）
_ARCHITECT_REPLY: Message = {
    "content": [{"text": "[Mock Architect] 已生成 Charter 草稿（模拟）。"}]
}
_ANALYST_REPLY: Message = {
    "content": [{"text": "[Mock Analyst] 已按 checklist 反思（模拟），无需修订。"}]
}


# 固定 Charter 数组，对齐 _build_json_schema。
# 2 条，覆盖 2 个 persona × 同一 target，模拟 charter_gen 的典型输出。
_MOCK_CHARTERS: list[dict[str, Any]] = [
    {
        "charter_id": 1,
        "target_id": 1,
        "persona": "破坏狂",
        "target_system": "铁匠铺强化（Mock）",
        "target_description": "Mock：武器强化系统边界测试",
        "load_save": "mock_save_01",
        "goal": "Mock：找到铁匠铺强化系统的边界情况和崩溃触发条件",
        "exploration_targets": [
            "把武器强化到+10 后继续尝试强化",
            "在强化动画播放中途按 ESC",
        ],
        "heuristics": [
            "极端值:满级武器(+10)继续强化",
            "时序错乱:强化动画中途 ESC",
        ],
        "expected_behaviors": [
            "+10 满级武器强化时按钮应置灰或弹'已满级'",
            "强化动画中途 ESC 应取消操作、不消耗材料",
        ],
        "coverage_dimensions": {
            "region": ["铁匠铺 NPC", "强化界面"],
            "function": ["强化", "取消"],
            "operation": ["单击", "ESC"],
            "state": ["金币充足", "武器+10"],
        },
        "time_budget_minutes": 30,
        # ⚠️ 刻意用 charter_changes_game_state（LLM 原始输出字段名），
        # 验证 write_charter 的转写契约（→ env_probing_required）。
        "charter_changes_game_state": "yes",
        "severity_threshold": "P0",
    },
    {
        "charter_id": 2,
        "target_id": 1,
        "persona": "贪婪者",
        "target_system": "铁匠铺强化（Mock）",
        "target_description": "Mock：强化经济漏洞测试",
        "load_save": "mock_save_01",
        "goal": "Mock：测试强化系统的经济边界与刷分漏洞",
        "exploration_targets": [
            "金币为 0 时尝试强化",
            "连续强化直到材料耗尽后检查金币是否变负",
        ],
        "heuristics": [
            "经济异常:金币为 0/负数",
            "极端值:材料耗尽后继续操作",
        ],
        "expected_behaviors": [
            "金币不足时强化按钮应置灰",
            "材料为 0 时不应允许强化、金币不应变负",
        ],
        "coverage_dimensions": {
            "region": ["铁匠铺 NPC"],
            "function": ["强化"],
            "operation": ["单击"],
            "state": ["金币为0", "材料为0"],
        },
        "time_budget_minutes": 30,
        "charter_changes_game_state": "no",
        "severity_threshold": "P1",
    },
]


# M3a：用例生成的固定回复（含 test + spec 两个代码块，格式对齐 generator._CODE_BLOCK_PATTERN）
# 代码必须 ruff 干净 + ast.parse 通过（QualityChecker 会检查）
_MOCK_SMOKE_TEST_REPLY: Message = {
    "content": [{"text": (
        "### test_inventory.py\n"
        "```python\n"
        '"""背包系统冒烟测试（Mock 生成）。"""\n'
        "from inventory_spec import InventorySpec\n"
        "\n"
        "\n"
        "def test_inventory_open_close(backend):\n"
        '    """测试：打开再关闭背包。"""\n'
        '    backend.click_text("背包")\n'
        '    backend.wait_until(lambda: "背包" in backend.state.texts, timeout=3.0)\n'
        '    assert "背包" in backend.state.texts\n'
        '    backend.press_key("escape")\n'
        "\n"
        "\n"
        "def test_inventory_spec_loads():\n"
        '    """测试：spec 模型能正常构造。"""\n'
        '    spec = InventorySpec(target_save="chapter1", expected_items=["村长剑"])\n'
        '    assert spec.severity == "P1"\n'
        "```\n"
        "\n"
        "### inventory_spec.py\n"
        "```python\n"
        '"""背包测试数据 spec（Mock 生成）。"""\n'
        "from typing import Literal\n"
        "\n"
        "from pydantic import BaseModel\n"
        "\n"
        "\n"
        "class InventorySpec(BaseModel):\n"
        '    target_save: str = "chapter1"\n'
        "    expected_items: list[str] = []\n"
        '    severity: Literal["P0", "P1", "P2"] = "P1"\n'
        "```\n"
    )}]
}


# M5：误报审查固定回复（review_failure 解析 is_false_positive）
_MOCK_REVIEW_REPLY: Message = {
    "content": [{"text": '{"is_false_positive": true, "reason": "Mock 判定为误报（测试逻辑问题）"}'}]
}


class MockProvider:
    """返回固定 Charter JSON 的 LLMProvider 实现。

    用于离线/CI 验证：无网络、无 LLM、无 SpecOps-src。
    实现 LLMProvider 协议（结构性子类型，无需显式继承）。
    """

    def simple_converse(
        self,
        prompt: str,
        messages: list[Message],
        *,
        reasoning: int = 0,
        images: list[str] | None = None,
    ) -> Message:
        """模拟一次对话。

        根据 prompt 内容 + 对话历史判断场景：
          - prompt 含"冒烟测试用例" → M3a 用例生成场景，返回固定测试代码
          - messages 为空 → charter Architect 生成（第 1 步）
          - messages 有 1 条 → charter Analyst 反思（第 2 步）
        """
        # M3a 用例生成场景（render_smoke_test_prompt 的模板含"冒烟测试用例"）
        if "冒烟测试用例" in prompt:
            return _MOCK_SMOKE_TEST_REPLY
        # M5 误报审查场景（review_failure/review_bug_report 的 prompt 含 "is_false_positive"）
        if "is_false_positive" in prompt:
            return _MOCK_REVIEW_REPLY
        # charter 生成场景（向后兼容 M0）
        if not messages:
            return _ARCHITECT_REPLY
        return _ANALYST_REPLY

    def converse_json(
        self,
        messages: list[Message],
        instruction: str,
    ) -> list[dict[str, Any]]:
        """返回固定 Charter 数组。忽略 messages 和 instruction。"""
        # 深拷贝，避免测试间状态泄漏（调用方会 pop 字段做重命名）
        import copy

        return copy.deepcopy(_MOCK_CHARTERS)


__all__ = ["MockProvider"]
