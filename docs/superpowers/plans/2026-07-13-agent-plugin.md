# Agent Plugin 系统实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把探索流程中"给 LLM 提供上下文信息"变成插件化，插件通过 4 个注入点（系统提示词/每轮对话/动作建议/校验）向探索流程提供信息，运行时通过 JSON 配置文件选择激活哪些插件。

**Architecture:** AgentPlugin Protocol（4 注入点）+ PluginManager（拼接+异常隔离）+ OCRPlugin（内置，搬现有逻辑）+ JSON 配置文件（自动生成）。策略类的 `_SYSTEM_PROMPT` 拆成 `_BASE_PROMPT` + `plugin_manager.build_system_prompt()`，`_build_step_text` 改调 `plugin_manager.build_step_text()`。

**Tech Stack:** Python 3.12，Pydantic，Protocol + @runtime_checkable（结构性子类型），JSON 配置（标准库零依赖）

**Spec:** `docs/superpowers/specs/2026-07-13-agent-plugin-design.md`

---

### Task 1: AgentPlugin Protocol（base.py 加新接口）

**Files:**
- Modify: `src/joker_test/plugins/base.py`（现有 GamePlugin 后面追加 AgentPlugin）

- [ ] **Step 1: 在 base.py 末尾（DefaultPlugin 之后）追加 AgentPlugin Protocol**

在 `src/joker_test/plugins/base.py` 的 `DefaultPlugin` 类之后、`__all__` 之前追加：

```python
@runtime_checkable
class AgentPlugin(Protocol):
    """测试 Agent 插件。通过注入点向探索流程提供信息。

    4 个注入点，每个返回空值 = 该插件不贡献此注入点的内容。
    实现者只需实现需要的方法，不需要的返回空串/None。
    """

    name: str

    def inject_system_prompt(self) -> str:
        """系统提示词注入点（固定，只拼接一次）。
        告诉 LLM 如何理解本插件提供的信息格式。返回空串 = 不注入。"""
        ...

    def inject_step(self, screenshot: Any, backend: Any, ctx: Any) -> str:
        """每轮对话注入点（动态，每步都调）。
        返回本步要追加到 user message 的文本。返回空串 = 不注入。"""
        ...

    def inject_action_hint(self, screenshot: Any, backend: Any, ctx: Any) -> str:
        """动作建议注入点（动态，每步都调）。
        给 LLM 额外的动作提示。返回空串 = 不注入。"""
        ...

    def validate(self, decision: Any, result: Any) -> str | None:
        """校验注入点（动作执行后调）。
        检查动作结果，返回问题描述（None = 无问题）。
        反馈文本会注入到下一步的 step_text 让 LLM 自我纠错。"""
        ...
```

- [ ] **Step 2: 更新 `__all__`**

```python
__all__ = ["GamePlugin", "DefaultPlugin", "AgentPlugin"]
```

- [ ] **Step 3: 验证导入正常**

Run: `python -c "from joker_test.plugins.base import AgentPlugin, GamePlugin; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/joker_test/plugins/base.py
git commit -m "feat: add AgentPlugin Protocol (4 injection points)"
```

---

### Task 2: PluginManager（拼接 + 异常隔离）

**Files:**
- Create: `src/joker_test/plugins/manager.py`
- Test: `tests/test_plugin_manager.py`

- [ ] **Step 1: 写失败测试 `tests/test_plugin_manager.py`**

```python
"""PluginManager 测试：拼接顺序 + 异常隔离 + validate 反馈。"""
from __future__ import annotations

from unittest.mock import MagicMock

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
    # AAA 在 BBB 前面（列表顺序）
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
    assert "OK" in result  # ok 插件不受 crash 影响


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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_plugin_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'joker_test.plugins.manager'`

- [ ] **Step 3: 实现 `src/joker_test/plugins/manager.py`**

```python
"""PluginManager —— 插件管理器，注入点统一调用入口 + 异常隔离。

职责：
- 拼接：把所有插件的注入内容按顺序拼成完整文本
- 异常隔离：每个插件调用 try/except，崩了跳过（ADR-009 同理）
- validate 反馈收集：收集所有插件的校验结果
"""
from __future__ import annotations

import logging
from typing import Any

from joker_test.plugins.base import AgentPlugin

_LOGGER = logging.getLogger(__name__)


class PluginManager:
    """插件管理器。注入点统一调用入口 + 异常隔离。

    Args:
        plugins: 按顺序排列的激活插件列表
    """

    def __init__(self, plugins: list[AgentPlugin]) -> None:
        self._plugins = plugins

    def build_system_prompt(self, base: str) -> str:
        """base + 所有插件的 system_prompt 片段。

        空片段跳过，异常插件跳过。
        """
        fragments: list[str] = []
        for p in self._plugins:
            try:
                frag = p.inject_system_prompt()
                if frag:
                    fragments.append(frag)
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("插件 %s inject_system_prompt 失败：%s", p.name, e)
        if not fragments:
            return base
        return base + "\n\n" + "\n\n".join(fragments)

    def build_step_text(
        self,
        screenshot: Any,
        backend: Any,
        ctx: Any,
        base: str,
        validate_feedback: str = "",
    ) -> str:
        """base + 所有插件的 step 注入 + action_hint 注入 + validate 反馈。

        每个注入点异常隔离。
        """
        parts: list[str] = [base]
        for p in self._plugins:
            try:
                injected = p.inject_step(screenshot, backend, ctx)
                if injected:
                    parts.append(injected)
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("插件 %s inject_step 失败：%s", p.name, e)
            try:
                hint = p.inject_action_hint(screenshot, backend, ctx)
                if hint:
                    parts.append(hint)
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("插件 %s inject_action_hint 失败：%s", p.name, e)
        if validate_feedback:
            parts.append(validate_feedback)
        return "\n\n".join(parts)

    def validate(self, decision: Any, result: Any) -> str:
        """收集所有插件的校验结果，拼成反馈文本。空串 = 无问题。"""
        issues: list[str] = []
        for p in self._plugins:
            try:
                issue = p.validate(decision, result)
                if issue:
                    issues.append(f"[{p.name}] {issue}")
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("插件 %s validate 失败：%s", p.name, e)
        return "\n".join(issues)


__all__ = ["PluginManager"]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_plugin_manager.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/joker_test/plugins/manager.py tests/test_plugin_manager.py
git commit -m "feat: add PluginManager with injection point concatenation and exception isolation"
```

---

### Task 3: OCRPlugin（内置插件，搬现有逻辑）

**Files:**
- Create: `src/joker_test/plugins/ocr/__init__.py`
- Test: `tests/test_ocr_plugin.py`

- [ ] **Step 1: 写失败测试 `tests/test_ocr_plugin.py`**

```python
"""OCRPlugin 测试：格式正确 + backend 兼容 + 空返回。"""
from __future__ import annotations

from joker_test.executor.backends.fake import FakeBackend, ScreenCfg
from joker_test.executor.base import BBox
from joker_test.plugins.ocr import OCRPlugin


def test_inject_system_prompt_has_format_explanation():
    """系统提示词包含坐标格式说明。"""
    p = OCRPlugin()
    frag = p.inject_system_prompt()
    assert "文字@" in frag
    assert "归一化" in frag or "[0,1]" in frag


def test_inject_step_with_fake_backend():
    """FakeBackend 有 OCR 文字时返回带坐标的界面元素。"""
    backend = FakeBackend(texts_map={
        "设置": BBox(0.4, 0.7, 0.2, 0.1),
        "退出": BBox(0.4, 0.8, 0.2, 0.1),
    })
    backend.connect()
    screenshot = backend.screenshot()

    p = OCRPlugin()
    result = p.inject_step(screenshot, backend, ctx=None)
    assert "设置" in result
    assert "退出" in result
    assert "@(" in result  # 坐标格式
    backend.close()


def test_inject_step_no_texts_returns_empty():
    """backend 无文字时返回空串。"""
    backend = FakeBackend()  # 无 texts_map
    backend.connect()
    screenshot = backend.screenshot()

    p = OCRPlugin()
    result = p.inject_step(screenshot, backend, ctx=None)
    assert result == ""
    backend.close()


def test_inject_action_hint_always_empty():
    """OCR 插件不做动作建议。"""
    assert OCRPlugin().inject_action_hint(None, None, None) == ""


def test_validate_always_none():
    """OCR 插件不做校验。"""
    assert OCRPlugin().validate(None, None) is None


def test_name_is_ocr():
    assert OCRPlugin().name == "ocr"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_ocr_plugin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'joker_test.plugins.ocr'`

- [ ] **Step 3: 实现 `src/joker_test/plugins/ocr/__init__.py`**

```python
"""OCRPlugin —— OCR 文字+坐标识别插件（内置）。

把现有 LLMExplorer._perceive + ConversationStrategy._build_step_text 的 OCR 逻辑
搬成自包含插件。通过 4 个注入点向探索流程提供信息。
"""
from __future__ import annotations

from typing import Any


class OCRPlugin:
    """OCR 文字+坐标识别插件。

    从 backend.state 提取 OCR 文字及其中心坐标，注入每轮对话。
    """

    @property
    def name(self) -> str:
        return "ocr"

    def inject_system_prompt(self) -> str:
        """告诉 LLM 界面元素的坐标格式。"""
        return (
            "界面元素格式：文字@(x,y)，x/y 是归一化坐标[0,1]"
            "（左0右1，上0下1），表示该文字在屏幕上的中心位置。\n"
            "点击有文字的按钮时，直接用它的坐标作为 click 的 x/y，不需要自己估算。"
        )

    def inject_step(self, screenshot: Any, backend: Any, ctx: Any) -> str:
        """从 backend.state 提取 OCR 文字+坐标，返回格式化文本。"""
        try:
            state = backend.state
            texts = list(state.texts)
            if not texts:
                return ""
        except Exception:  # noqa: BLE001
            return ""

        # 提取文字 + 中心坐标（支持 AirtestBackend._ocr_results / FakeBackend.text_elements）
        text_elements: list[dict[str, Any]] = []
        raw = getattr(state, "_ocr_results", None) or getattr(state, "text_elements", None)
        if raw:
            for r in raw:
                bbox = r.get("bbox")
                if bbox:
                    cx = round(bbox.x + bbox.w / 2, 3)
                    cy = round(bbox.y + bbox.h / 2, 3)
                    text_elements.append({"text": r["text"], "x": cx, "y": cy})

        if not text_elements:
            # 降级：只有文字无坐标
            return "OCR: " + ", ".join(texts[:15]) if texts else ""

        lines = [f"  {e['text']}@({e['x']:.2f},{e['y']:.2f})" for e in text_elements[:15]]
        return "界面元素(文字@坐标):\n" + "\n".join(lines)

    def inject_action_hint(self, screenshot: Any, backend: Any, ctx: Any) -> str:
        """OCR 插件不做动作建议。"""
        return ""

    def validate(self, decision: Any, result: Any) -> str | None:
        """OCR 插件不做校验。"""
        return None


__all__ = ["OCRPlugin"]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_ocr_plugin.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/joker_test/plugins/ocr/__init__.py tests/test_ocr_plugin.py
git commit -m "feat: add OCRPlugin (extract text+coords from backend.state)"
```

---

### Task 4: 更新 plugins/__init__.py 导出

**Files:**
- Modify: `src/joker_test/plugins/__init__.py`

- [ ] **Step 1: 重写 `__init__.py`**

```python
"""joker_test.plugins —— 游戏插件系统（DESIGN §4.5，ADR-003）。

两套插件：
- GamePlugin：游戏数据/规则/工具（v0.1 已定义，后续接入）
- AgentPlugin：探索注入点（系统提示词/每步/动作建议/校验）
"""

from joker_test.plugins.base import AgentPlugin, DefaultPlugin, GamePlugin
from joker_test.plugins.manager import PluginManager
from joker_test.plugins.ocr import OCRPlugin
from joker_test.plugins.loader import load_plugin

# 内置 AgentPlugin 注册表（配置文件里的插件名 → 类）
BUILTIN_PLUGINS: dict[str, type[AgentPlugin]] = {
    "ocr": OCRPlugin,
}

__all__ = [
    "GamePlugin",
    "DefaultPlugin",
    "AgentPlugin",
    "PluginManager",
    "OCRPlugin",
    "BUILTIN_PLUGINS",
    "load_plugin",
]
```

- [ ] **Step 2: 验证导入正常**

Run: `python -c "from joker_test.plugins import PluginManager, OCRPlugin, BUILTIN_PLUGINS; print(BUILTIN_PLUGINS)"`
Expected: `{'ocr': <class 'joker_test.plugins.ocr.OCRPlugin'>}`

- [ ] **Step 3: Commit**

```bash
git add src/joker_test/plugins/__init__.py
git commit -m "feat: export AgentPlugin + PluginManager + BUILTIN_PLUGINS"
```

---

### Task 5: ConversationStrategy 接入 PluginManager

**Files:**
- Modify: `src/joker_test/explorer/conversation_strategy.py`

- [ ] **Step 1: 修改 `__init__` 加 plugin_manager 参数**

在 `src/joker_test/explorer/conversation_strategy.py` 的 `ConversationStrategy.__init__`（L59-71）改为：

```python
    def __init__(
        self,
        llm: LLMProvider,
        intent: str,
        max_conversation_tokens: int = 8000,
        plugin_manager: Any = None,
    ) -> None:
        self._llm = llm
        self._intent = intent
        self._max_tokens = max_conversation_tokens
        self._plugin_manager = plugin_manager
        self._messages: list[Message] = []
        self._screens: list[Screen] = []
        self._goal_completed: bool = False
        self._initialized = False
        self._last_validate_feedback: str = ""
```

- [ ] **Step 2: 把 `_SYSTEM_PROMPT` 拆成 `_BASE_PROMPT`**

将 L26-53 的 `_SYSTEM_PROMPT` 改名为 `_BASE_PROMPT`，删除其中 OCR 坐标格式说明（已移到 OCRPlugin），只保留 base 框架：

```python
_BASE_PROMPT = """你在探索一个游戏界面，目标是完成任务。每步你看到截图和界面元素信息，推理后输出动作。

用 <think>推理</think><answer>{json}</answer> 回答。json 只能是以下动作之一：

1. 点击按钮：{"action":"click","target":"按钮文字","x":0.5,"y":0.5,"description":"..."}
2. 按键：{"action":"press_key","target":"escape","description":"..."}
3. 输入文字：{"action":"type_text","target":"要输入的文字","description":"..."}
4. 滑动：{"action":"swipe","target":"left","x":0.5,"y":0.5,"description":"向左拖动滑块"}
5. 翻页：{"action":"scroll","target":"down","description":"向下翻页"}
6. 长按：{"action":"long_press","target":"图标描述","description":"..."}
7. 返回上级：{"action":"back","description":"返回上级界面"}
8. 停止探索：{"action":"stop","description":"目标完成/无法继续"}

规则：
- action 必须是上面 8 个之一，不要发明其他动作名
- click 的 target 填按钮上的文字；有文字的按钮直接用界面元素给的坐标作为 x/y
- 坐标是归一化 [0,1]（左0右1，上0下1），不要输出绝对像素值
- swipe 的 target 填方向 left/right/up/down，用于拖动滑块或切换页面
- 水平滑块（如音量）用 swipe + left/right，不要用 click 点滑块（点不动）
- 不要点击窗口的关闭(×)/最小化/最大化按钮
- 不要重复点击同一个按钮，界面没变化就换操作
- 绝对不要点击全屏模式
- goal_progress 描述当前进度，goal_completed 为 true 时表示目标完成"""
```

- [ ] **Step 3: 修改 `decide` 方法**

在 `decide` 方法中（L73-123），把 system prompt 初始化和 step_text 构建改为走 plugin_manager：

```python
    def decide(
        self,
        screenshot: Any,
        perception: Any,
        ctx: ExploreContext,
    ) -> StepDecision:
        """构建 prompt → LLM（tool_use 结构化输出）→ 解析 tool_use block。"""
        if not self._initialized:
            # 系统提示词：base + 插件注入（无 plugin_manager 时用 _BASE_PROMPT）
            sys_prompt = _BASE_PROMPT
            if self._plugin_manager is not None:
                sys_prompt = self._plugin_manager.build_system_prompt(_BASE_PROMPT)
            self._messages.append(
                {"role": "system", "content": [{"type": "text", "text": sys_prompt}]}
            )
            self._initialized = True

        img_b64 = self._encode_image(screenshot)
        step_text = self._build_step_text(perception, ctx, screenshot)
        images = [img_b64] if img_b64 else None

        from joker_test.llm.base import build_user_message  # noqa: PLC0415

        user_msg = build_user_message(step_text, images)

        try:
            reply = self._llm.create(
                messages=self._messages + [user_msg],
                tools=[EXPLORE_TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": "execute_action"},
            )
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("LLM 决策失败：%s", e)
            return StepDecision(think=f"LLM失败:{e}", action="stop", stop=True)

        # 追加 user 消息到历史（文本，图不保留）
        self._messages.append(
            {"role": "user", "content": [{"type": "text", "text": step_text}]}
        )

        # 从 tool_use block 提取结构化动作
        decision = self._parse_tool_use(reply, screenshot)

        # 追加 assistant 消息到历史
        self._messages.append(
            {"role": "assistant", "content": [{"type": "text", "text": decision.think or decision.action}]}
        )

        if decision.goal_completed:
            self._goal_completed = True

        # 用 perception 的 OCR 文本构建 Screen
        self._maybe_add_screen(perception, ctx)

        return decision
```

- [ ] **Step 4: 修改 `_build_step_text` 方法**

将 `_build_step_text`（L203-215）改为：

```python
    def _build_step_text(self, perception: Any, ctx: ExploreContext, screenshot: Any = None) -> str:
        base = f"目标: {self._intent}\n步数: {ctx.step}/{ctx.max_steps}"
        # 有 plugin_manager → 走插件注入
        if self._plugin_manager is not None:
            validate_prefix = ""
            if self._last_validate_feedback:
                validate_prefix = f"上一步反馈:\n{self._last_validate_feedback}"
            return self._plugin_manager.build_step_text(
                screenshot, ctx.backend, ctx, base, validate_feedback=validate_prefix,
            )
        # 无 plugin_manager → 降级到原有逻辑（向后兼容）
        parts = [base]
        if perception is not None:
            elements = getattr(perception, "text_elements", [])
            if elements:
                lines = [f"  {e['text']}@({e['x']:.2f},{e['y']:.2f})" for e in elements[:15]]
                parts.append("界面元素(文字@坐标):\n" + "\n".join(lines))
            else:
                texts = getattr(perception, "texts", [])
                if texts:
                    parts.append("OCR: " + ", ".join(texts[:15]))
        return "\n".join(parts)
```

- [ ] **Step 5: 运行现有测试确认不回归**

Run: `python -m pytest tests/ -q --ignore=tests/generated_smoke --ignore=tests/real`
Expected: 全部 PASS（无 plugin_manager 时行为不变）

- [ ] **Step 6: Commit**

```bash
git add src/joker_test/explorer/conversation_strategy.py
git commit -m "feat: ConversationStrategy accepts PluginManager for injection points"
```

---

### Task 6: ReactStateStrategy 接入 PluginManager

**Files:**
- Modify: `src/joker_test/explorer/react_strategy.py`

- [ ] **Step 1: 修改 `__init__` 加 plugin_manager 参数**

在 `ReactStateStrategy.__init__`（L72-80）改为：

```python
    def __init__(
        self,
        llm: LLMProvider,
        intent: str,
        max_stale: int = 3,
        plugin_manager: Any = None,
    ) -> None:
        self._llm = llm
        self._max_stale = max_stale
        self._plugin_manager = plugin_manager
        self._state = ExploreState(goal=intent)
```

- [ ] **Step 2: 修改 `_build_prompt` 中的 OCR 部分**

在 `_build_prompt`（L142-187）的 OCR 处理部分，有 plugin_manager 时走插件注入：

```python
    def _build_prompt(
        self, screenshot: Any, perception: Any, ctx: ExploreContext
    ) -> str:
        """构建 ReAct prompt（固定大小状态摘要）。"""
        parts = [
            f"目标: {self._state.goal}",
            f"进度: {self._state.goal_progress or '开始探索'}",
            f"步数: {ctx.step}/{ctx.max_steps}",
        ]

        # 有 plugin_manager → 走插件注入界面元素
        if self._plugin_manager is not None:
            base_step = "\n".join(parts)
            injected = self._plugin_manager.build_step_text(
                screenshot, ctx.backend, ctx, "",
            )
            if injected.strip():
                parts = [base_step, injected]
        elif perception is not None:
            elements = getattr(perception, "text_elements", [])
            if elements:
                lines = [f"  {e['text']}@({e['x']:.2f},{e['y']:.2f})" for e in elements[:15]]
                parts.append("界面元素(文字@坐标):\n" + "\n".join(lines))
            else:
                texts = getattr(perception, "texts", [])
                if texts:
                    parts.append("OCR 文本: " + ", ".join(texts[:15]))

        if self._state.screens:
            screen_summary = "; ".join(
                f"{s.name or s.id}({len(s.elements)}元素)" for s in self._state.screens
            )
            parts.append(f"已探索: {screen_summary}")

        if self._state.path_stack:
            path = "→".join(e.from_screen for e in self._state.path_stack)
            parts.append(f"路径: {path}")

        prompt = (
            "你在探索一个游戏界面。请推理下一步。\n"
            + "\n".join(parts)
            + "\n\n用 <think>推理</think><answer>{json}</answer> 回答。\n"
            "动作只能是以下 8 种之一：\n"
            '1. {"action":"click","target":"按钮文字","description":"..."}\n'
            '2. {"action":"press_key","target":"escape","description":"..."}\n'
            '3. {"action":"type_text","target":"文字","description":"..."}\n'
            '4. {"action":"swipe","target":"up","description":"向上滑动"}\n'
            '5. {"action":"scroll","target":"down","description":"向下翻页"}\n'
            '6. {"action":"long_press","target":"图标描述","description":"..."}\n'
            '7. {"action":"back","description":"返回上级"}\n'
            '8. {"action":"stop","description":"目标完成"}\n'
            "规则：click 的 target 是按钮文字，不要输出坐标，不要点全屏/关闭按钮。"
        )
        return prompt
```

- [ ] **Step 3: 运行现有测试确认不回归**

Run: `python -m pytest tests/ -q --ignore=tests/generated_smoke --ignore=tests/real`
Expected: 全部 PASS

- [ ] **Step 4: Commit**

```bash
git add src/joker_test/explorer/react_strategy.py
git commit -m "feat: ReactStateStrategy accepts PluginManager for injection points"
```

---

### Task 7: LLMExplorer 加 validate 调用 + plugin_manager 持有

**Files:**
- Modify: `src/joker_test/explorer/llm_explorer.py`

- [ ] **Step 1: 修改 `__init__` 加 plugin_manager 参数**

在 `LLMExplorer.__init__`（L34-53）加 `plugin_manager` 参数：

```python
    def __init__(
        self,
        backend: ExecutorBackend,
        llm: LLMProvider,
        strategy: Any,
        max_steps: int = 30,
        screenshot_dir: str | Path | None = None,
        recorder: GlobalRecorder | None = None,
        plugin_manager: Any = None,
    ) -> None:
        self._backend = backend
        self._llm = llm
        self._strategy = strategy
        self._max_steps = max_steps
        self._recorder = recorder
        self._plugin_manager = plugin_manager
        self._step = 0
        self._screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        if self._screenshot_dir:
            self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._last_action_key: str = ""
        self._repeat_count: int = 0
```

- [ ] **Step 2: 在 `_run_step` 中加 validate 调用**

在 `_run_step` 的 `self._strategy.on_action_executed(decision, result)` 之后（L107 附近），加 validate 调用：

```python
        self._strategy.on_action_executed(decision, result)

        # 插件校验：收集反馈注入下一步
        if self._plugin_manager is not None:
            feedback = self._plugin_manager.validate(decision, result)
            if feedback:
                self._trace("plugin_validate", {"feedback": feedback})
            # 传给策略用于下一步（策略自己决定怎么用）
            if hasattr(self._strategy, "_last_validate_feedback"):
                self._strategy._last_validate_feedback = feedback  # noqa: SLF001

        if self._recorder is not None and result.success:
            self._record(decision, result)
```

- [ ] **Step 3: 运行现有测试确认不回归**

Run: `python -m pytest tests/ -q --ignore=tests/generated_smoke --ignore=tests/real`
Expected: 全部 PASS

- [ ] **Step 4: Commit**

```bash
git add src/joker_test/explorer/llm_explorer.py
git commit -m "feat: LLMExplorer holds plugin_manager and calls validate"
```

---

### Task 8: 配置文件（config.py）

**Files:**
- Create: `src/joker_test/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: 写失败测试 `tests/test_config.py`**

```python
"""config.py 测试：自动生成 + 加载 + 默认值合并。"""
from __future__ import annotations

import json
from pathlib import Path

from joker_test.config import load_config, _DEFAULT_CONFIG


def test_auto_generate_when_not_exists(tmp_path):
    """配置文件不存在时自动生成。"""
    cfg_path = tmp_path / "test-config.json"
    result = load_config(cfg_path)
    assert cfg_path.exists()  # 文件已生成
    assert result["plugins"] == ["ocr"]  # 默认值
    # 生成的是合法 JSON
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
    assert result["max_steps"] == 5  # 用户值
    assert result["plugins"] == ["ocr"]  # 默认值（缺失的 key）
    assert result["explore_strategy"] == "conversation"  # 默认值


def test_default_config_has_required_keys():
    """_DEFAULT_CONFIG 包含所有必需 key。"""
    for key in ("plugins", "plugin_path", "explore_strategy", "max_steps", "backend_name", "window_title"):
        assert key in _DEFAULT_CONFIG
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'joker_test.config'`

- [ ] **Step 3: 实现 `src/joker_test/config.py`**

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/joker_test/config.py tests/test_config.py
git commit -m "feat: add config.py with auto-generate and JSON loading"
```

---

### Task 9: ExploreStage 接入 PluginManager + 配置传参

**Files:**
- Modify: `src/joker_test/pipeline/types.py`（ExploreConfig 加字段）
- Modify: `src/joker_test/pipeline/stages/explore.py`（构造 PluginManager + 传给策略）
- Modify: `src/joker_test/pipeline/base.py`（build_orchestrator 传配置）

- [ ] **Step 1: ExploreConfig 加字段**

在 `src/joker_test/pipeline/types.py` 的 `ExploreConfig`（L16-30）加两个字段：

```python
class ExploreConfig(BaseModel):
    """流水线统一配置。"""

    __test__ = False
    intent: str
    mode: Literal["manual", "dfs", "llm"] = "llm"
    reuse: str | None = None
    check_reuse: bool = True
    solidify: bool = True
    execute: bool = True
    game_name: str = ""
    backend_name: str = "fake"
    max_explore_steps: int = 30
    verify_during_solidify: bool = True
    explore_strategy: Literal["react_state", "conversation"] = "conversation"
    plugins: list[str] = Field(default_factory=lambda: ["ocr"])  # 新增
    plugin_path: str | None = None  # 新增
```

- [ ] **Step 2: ExploreStage.__init__ 加 plugin_manager 参数**

在 `src/joker_test/pipeline/stages/explore.py` 的 `ExploreStage.__init__`（L65-75）加参数：

```python
    def __init__(
        self,
        provider: LLMProvider,
        backend: ExecutorBackend,
        gen_dir: str | Path = "tests/generated_smoke",
        flow_dir: str | Path = "flows",
        plugin_manager: Any = None,
    ) -> None:
        self._provider = provider
        self._backend = backend
        self._gen_dir = Path(gen_dir)
        self._flow_dir = Path(flow_dir)
        self._plugin_manager = plugin_manager
```

- [ ] **Step 3: ExploreStage._explore_llm 传 plugin_manager 给策略**

在 `_explore_llm`（L184-219）中，构造策略时传 plugin_manager：

```python
    def _explore_llm(
        self, config: ExploreConfig, log: list[str]
    ) -> tuple[RecordedFlow | None, StateMap]:
        """LLMExplorer agentic loop，产 StateMap + 程序化录制。"""
        recorder = GlobalRecorder(
            output_dir=self._flow_dir, backend=self._backend, pynput_mode=False
        )
        recorder.start()

        from joker_test.explorer.conversation_strategy import ConversationStrategy  # noqa: PLC0415
        from joker_test.explorer.react_strategy import ReactStateStrategy  # noqa: PLC0415

        pm = self._plugin_manager
        if config.explore_strategy == "conversation":
            strategy = ConversationStrategy(
                llm=self._provider, intent=config.intent, plugin_manager=pm,
            )
        else:
            strategy = ReactStateStrategy(
                llm=self._provider, intent=config.intent, plugin_manager=pm,
            )

        explorer = LLMExplorer(
            self._backend,
            self._provider,
            strategy=strategy,
            max_steps=config.max_explore_steps,
            recorder=recorder,
            plugin_manager=pm,
        )
        state_map = explorer.explore()
        flow = recorder.stop()
        if flow.steps:
            recorder.save_flow_yaml(flow)
        log.append(
            f"llm 探索 {len(state_map.screens)} 屏（{config.explore_strategy}）"
        )
        return flow if flow.steps else None, state_map
```

- [ ] **Step 4: build_orchestrator 构造 PluginManager**

在 `src/joker_test/pipeline/base.py` 的 `build_orchestrator`（L135-155）中，根据 config 构造 PluginManager：

```python
def build_orchestrator(
    config: ExploreConfig,
    report_dir: str | Path = "reports",
    gen_dir: str | Path = "tests/generated_smoke",
    flow_dir: str | Path = "flows",
) -> AgenticOrchestrator:
    """根据 config 构造编排器，注入各 Stage 所需依赖。"""
    from joker_test.executor import set_active_backend
    from joker_test.executor.backends.fake import FakeBackend
    from joker_test.llm.providers.mock import MockProvider
    from joker_test.plugins import BUILTIN_PLUGINS, PluginManager
    from joker_test.plugins.loader import load_plugin

    provider = MockProvider()
    backend = FakeBackend()
    set_active_backend(backend)

    # 构建插件管理器
    plugins = []
    for name in config.plugins:
        plugin_cls = BUILTIN_PLUGINS.get(name)
        if plugin_cls:
            plugins.append(plugin_cls())
    if config.plugin_path:
        plugins.append(load_plugin(config.plugin_path))
    plugin_manager = PluginManager(plugins)

    return AgenticOrchestrator(
        explore=ExploreStage(provider, backend, gen_dir=gen_dir, flow_dir=flow_dir, plugin_manager=plugin_manager),
        solidify=SolidifyStage(provider, backend, gen_dir=gen_dir),
        execute=ExecuteStage(),
        report=ReportStage(report_dir=report_dir),
        reflect=ReflectStage(provider),
    )
```

- [ ] **Step 5: 运行现有测试确认不回归**

Run: `python -m pytest tests/ -q --ignore=tests/generated_smoke --ignore=tests/real`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add src/joker_test/pipeline/types.py src/joker_test/pipeline/stages/explore.py src/joker_test/pipeline/base.py
git commit -m "feat: pipeline builds PluginManager from config and injects into strategies"
```

---

### Task 10: CLI 加 --config 参数 + 全量测试

**Files:**
- Modify: `src/joker_test/cli.py`

- [ ] **Step 1: 加 --config 参数到 explore 和 run-all 子命令**

在 `src/joker_test/cli.py` 的 explore 子命令 argparse（L44-69 附近）加：

```python
    parser_explore.add_argument("--config", default="joker-test-config.json",
                                help="配置文件路径（不存在则自动生成）")
```

在 run-all 子命令也加同样的 `--config` 参数。

- [ ] **Step 2: 修改 `_cmd_explore` 加载配置**

在 `_cmd_explore`（L202-226）中，加载配置并传入 ExploreConfig：

```python
def _cmd_explore(args: argparse.Namespace) -> int:
    """智能探索入口：固化命中检查 + 三模式探索 + 可串联固化/执行。"""
    from joker_test.config import load_config  # noqa: PLC0415
    from joker_test.pipeline import ExploreConfig, build_orchestrator  # noqa: PLC0415

    user_cfg = load_config(args.config)
    cfg = ExploreConfig(
        intent=args.intent,
        mode=args.mode,
        reuse=args.reuse,
        check_reuse=args.check_reuse,
        solidify=args.solidify,
        execute=args.execute,
        game_name=args.game,
        backend_name=args.backend,
        max_explore_steps=args.max_steps,
        explore_strategy=args.strategy,
        plugins=user_cfg.get("plugins", ["ocr"]),
        plugin_path=user_cfg.get("plugin_path"),
    )
    orch = build_orchestrator(cfg)
    result = orch.run(cfg)
    print(f"探索完成：{result.report.summary}")
    if result.reflect.risks:
        print(f"风险提示：{len(result.reflect.risks)} 项")
        for risk in result.reflect.risks:
            print(f"  [{risk.severity}] {risk.category}: {risk.description}")
    print(f"可信度：{result.reflect.confidence_score:.1%}")
    return 0
```

- [ ] **Step 3: 运行全量测试**

Run: `python -m pytest tests/ -q --ignore=tests/generated_smoke --ignore=tests/real`
Expected: 全部 PASS

- [ ] **Step 4: lint 检查**

Run: `ruff check src tests`
Expected: All checks passed

- [ ] **Step 5: Commit**

```bash
git add src/joker_test/cli.py
git commit -m "feat: CLI loads config file and passes plugin config to pipeline"
```

---

### Task 11: trace 可观测性（plugin_inject 事件）

**Files:**
- Modify: `src/joker_test/plugins/manager.py`（build_step_text 中加 trace）

- [ ] **Step 1: 在 PluginManager.build_step_text 中加 trace 调用**

在 `src/joker_test/plugins/manager.py` 的 `build_step_text` 方法中，每个插件注入成功时记录 trace：

```python
    def build_step_text(
        self,
        screenshot: Any,
        backend: Any,
        ctx: Any,
        base: str,
        validate_feedback: str = "",
    ) -> str:
        """base + 所有插件的 step 注入 + action_hint 注入 + validate 反馈。"""
        from joker_test.trace import trace_event  # noqa: PLC0415

        parts: list[str] = [base]
        for p in self._plugins:
            try:
                injected = p.inject_step(screenshot, backend, ctx)
                if injected:
                    parts.append(injected)
                    trace_event("plugin_inject", {
                        "plugin": p.name, "inject_point": "step", "length": len(injected),
                    })
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("插件 %s inject_step 失败：%s", p.name, e)
            try:
                hint = p.inject_action_hint(screenshot, backend, ctx)
                if hint:
                    parts.append(hint)
                    trace_event("plugin_inject", {
                        "plugin": p.name, "inject_point": "action_hint", "length": len(hint),
                    })
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("插件 %s inject_action_hint 失败：%s", p.name, e)
        if validate_feedback:
            parts.append(validate_feedback)
        return "\n\n".join(parts)
```

- [ ] **Step 2: 在 trace.py 的 _ICONS 和 _status_light 中加 plugin_inject 类型**

在 `src/joker_test/trace.py` 的 `_ICONS` dict 中加：

```python
        "plugin_inject": "🔌",
```

- [ ] **Step 3: 在 _render_event_card 中加 plugin_inject 的缩略渲染**

在 `_render_event_card` 的 elif 链中加：

```python
        elif etype == "plugin_inject":
            summary_parts.append(
                f"{html.escape(str(data.get('plugin', '')))} "
                f"注入 {html.escape(str(data.get('inject_point', '')))} "
                f"({data.get('length', 0)} 字符)"
            )
```

- [ ] **Step 4: 运行测试确认不回归**

Run: `python -m pytest tests/ -q --ignore=tests/generated_smoke --ignore=tests/real`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/joker_test/plugins/manager.py src/joker_test/trace.py
git commit -m "feat: trace plugin injections with plugin_inject events"
```

---

### Task 12: 更新 .gitignore + 验证自动生成

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: 把 joker-test-config.json 加入 .gitignore**

在 `.gitignore` 中加（如果是用户本地配置不入库）：

```
# 用户运行时配置（自动生成，不入库）
joker-test-config.json
```

- [ ] **Step 2: 端到端验证——确认默认行为不变**

Run: `python -c "from joker_test.config import load_config; c = load_config('joker-test-config.json'); print(c)"`
Expected: 打印配置 dict，文件已自动生成。

Run: `python -m pytest tests/ -q --ignore=tests/generated_smoke --ignore=tests/real`
Expected: 全部 PASS

Run: `ruff check src tests`
Expected: All checks passed

- [ ] **Step 3: 清理自动生成的配置文件（不入库）**

Run: `rm joker-test-config.json`

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore auto-generated joker-test-config.json"
```
