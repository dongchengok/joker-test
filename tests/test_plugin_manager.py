"""PluginManager 测试：拼接顺序 + 异常隔离 + validate 反馈。"""
from __future__ import annotations

from joker_test.plugins.manager import PluginManager


class _StubPlugin:
    """测试用桩插件。"""

    def __init__(self, name, sys_prompt="", step="", hint="", validate_result=None):
        self.name = name
        self._sys = sys_prompt
        self._step = step
        self._hint = hint
        self._validate = validate_result

    def inject_system_prompt(self) -> str:
        return self._sys

    def inject_step(self, screenshot, backend, ctx) -> str:
        return self._step

    def inject_action_hint(self, screenshot, backend, ctx) -> str:
        return self._hint

    def validate(self, decision, result) -> str | None:
        return self._validate


def test_build_system_prompt_concatenates_fragments():
    """多个插件的 system_prompt 片段按顺序拼接到 base 后面。"""
    pm = PluginManager([
        _StubPlugin("a", sys_prompt="AAA"),
        _StubPlugin("b", sys_prompt="BBB"),
    ])
    result = pm.build_system_prompt("BASE")
    assert "BASE" in result
    assert "AAA" in result
    assert "BBB" in result
    assert result.index("AAA") < result.index("BBB")


def test_build_system_prompt_empty_plugins():
    """无插件时只返回 base。"""
    pm = PluginManager([])
    assert pm.build_system_prompt("BASE") == "BASE"


def test_build_system_prompt_empty_fragment_skipped():
    """插件返回空串时不拼接。"""
    pm = PluginManager([_StubPlugin("a", sys_prompt="")])
    assert pm.build_system_prompt("BASE") == "BASE"


def test_build_step_text_concatenates_step_and_hint():
    """step_text 包含 base + 所有插件的 step 注入 + action_hint 注入。"""
    pm = PluginManager([
        _StubPlugin("a", step="STEP_A", hint="HINT_A"),
    ])
    result = pm.build_step_text(None, None, None, "BASE")
    assert "BASE" in result
    assert "STEP_A" in result
    assert "HINT_A" in result


def test_build_step_text_with_validate_feedback():
    """validate 反馈拼到末尾。"""
    pm = PluginManager([_StubPlugin("a")])
    result = pm.build_step_text(None, None, None, "BASE", validate_feedback="上一步反馈: 问题")
    assert "上一步反馈: 问题" in result


def test_validate_collects_issues():
    """收集所有插件的校验结果。"""
    pm = PluginManager([
        _StubPlugin("a", validate_result="问题A"),
        _StubPlugin("b", validate_result=None),
        _StubPlugin("c", validate_result="问题C"),
    ])
    result = pm.validate(None, None)
    assert "问题A" in result
    assert "问题C" in result
    assert "问题B" not in result


def test_validate_empty_when_all_pass():
    """所有插件校验通过时返回空串。"""
    pm = PluginManager([_StubPlugin("a", validate_result=None)])
    assert pm.validate(None, None) == ""


def test_exception_isolation_in_system_prompt():
    """插件崩了不影响其他插件。"""
    class CrashPlugin:
        name = "crash"
        def inject_system_prompt(self):
            raise RuntimeError("boom")
        def inject_step(self, *a): return ""
        def inject_action_hint(self, *a): return ""
        def validate(self, *a): return None

    pm = PluginManager([CrashPlugin(), _StubPlugin("ok", sys_prompt="OK")])
    result = pm.build_system_prompt("BASE")
    assert "OK" in result


def test_exception_isolation_in_step():
    """step 注入崩溃也不影响其他插件。"""
    class CrashPlugin:
        name = "crash"
        def inject_system_prompt(self): return ""
        def inject_step(self, *a): raise RuntimeError("boom")
        def inject_action_hint(self, *a): return ""
        def validate(self, *a): return None

    pm = PluginManager([CrashPlugin(), _StubPlugin("ok", step="OK_STEP")])
    result = pm.build_step_text(None, None, None, "BASE")
    assert "OK_STEP" in result
