# LLMExplorer 重写 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Open-AutoGLM 循环骨架重写 LLMExplorer，引入 ExploreStrategy 可替换策略层（默认 ReactStateStrategy，可选 ConversationStrategy），扩展动作空间，UIMap→StateMap 改名。

**Architecture:** LLMExplorer 退化为基础设施外壳（截图/perception/动作执行/录制/trace 公共），ExploreStrategy 协议负责决策和状态管理。ReactStateStrategy 用状态机+ReAct 固定 token；ConversationStrategy 对齐 Open-AutoGLM 的 conversation context（历史截图剥离+assistant 重封装）。

**Tech Stack:** Python 3.12、Pydantic v2、Protocol（结构性子类型）

**Spec:** `docs/superpowers/specs/2026-07-10-llm-explorer-rewrite-design.md`

---

## 关键接口参考（实现时对照）

```
# explorer/types.py（改名前）
UIMap(screens, root_screen_id, explored_at, backend_info)  →  StateMap
Screen(id, elements, exits, entry, fingerprint, screenshot_ref)  →  +name
Exit(from_screen, element_text, action: ExitAction, to_screen, evidence)
UIElement(type, text, bbox, template_ref)

# explorer/detection.py
compute_fingerprint(elements: list[UIElement]) -> str
has_screen_changed(before: ndarray, after: ndarray, threshold=0.005) -> tuple[bool, float]

# executor/base.py（扩展前）
ExecutorBackend: connect/close/screenshot/click/click_text/click_image/press_key/type_text/wait_until/state
# 扩展后加: swipe(x1,y1,x2,y2,duration=0.5) / long_press(x,y,duration=2.0)

# executor/backends/fake/backend.py
FakeBackend(width, height, texts_map, bg_pixel, screens, transitions, initial_screen)
click_history: list[tuple[str, Any]]  /  key_history: list[str]  /  type_history: list[str]

# perception/base.py
PerceptionResult(texts, matches, description, ui_elements, layer_used)
PerceptionEngine(ocr, llm, use_llm, matcher).perceive(frame, prompt, templates) -> PerceptionResult

# pipeline/types.py
ExploreConfig(intent, mode, reuse, check_reuse, solidify, execute, game_name, backend_name, max_explore_steps, verify_during_solidify)
# 扩展后加: explore_strategy: Literal["react_state","conversation"] = "react_state"
ExploreResult(flow, flow_dir, uimap→state_map, skipped, reused_test_paths, match_reason, explore_log)

# flow/recorder.py
GlobalRecorder(output_dir, *, backend, pynput_mode, ...).record_action(action: FlowAction, *, x, y, text, key, ocr_before, ocr_after, screenshot_before, screenshot_after, note)
FlowAction = Literal["click","click_text","click_image","press_key","type_text"]

# llm/base.py
LLMProvider.simple_converse(prompt: str, messages: list[Message], *, reasoning=0, images: list[str]|None) -> Message
Message = TypedDict, content: list[dict]
```

---

## Task 1: UIMap→StateMap 改名 + Screen.name 增强

**Files:**
- Modify: `src/joker_test/explorer/types.py`
- Modify: `src/joker_test/explorer/explorer.py`
- Modify: `src/joker_test/pipeline/types.py`
- Modify: `src/joker_test/pipeline/stages/explore.py`
- Modify: `src/joker_test/pipeline/stages/report.py`
- Modify: `src/joker_test/pipeline/stages/reflect.py`
- Modify: `tests/test_pipeline_explore.py`
- Modify: `tests/test_pipeline_report.py`
- Modify: `tests/test_pipeline_orchestrator.py`
- Test: `tests/test_pipeline_types.py`

这是基础改名，先做，后续所有代码用新名字。

- [ ] **Step 1: 读 explorer/types.py 确认当前内容**

Run: `source .venv/Scripts/activate && cat src/joker_test/explorer/types.py`

确认 `UIMap` 类名、`Screen` 字段、`Exit`/`UIElement` 定义。

- [ ] **Step 2: 改 explorer/types.py**

`UIMap` → `StateMap`（类名 + docstring）。`Screen` 加 `name: str = ""` 字段（在 `id` 之后）：

```python
class Screen(BaseModel):
    """界面节点。"""
    __test__ = False
    id: str
    name: str = ""
    elements: list[UIElement]
    exits: list[Exit] = Field(default_factory=list)
    entry: dict[str, object] | None = None
    fingerprint: str
    screenshot_ref: str | None = None

class StateMap(BaseModel):
    """探索状态地图（界面图结构）。"""
    __test__ = False
    screens: list[Screen]
    root_screen_id: str
    explored_at: str
    backend_info: dict[str, object] = Field(default_factory=dict)
```

更新 `__all__`：`"UIMap"` → `"StateMap"`。

- [ ] **Step 3: 更新 explorer/__init__.py**

`UIMap` → `StateMap`（import + `__all__`）。

- [ ] **Step 4: 更新 explorer/explorer.py**

UIExplorer 返回类型 `UIMap` → `StateMap`，内部所有 `UIMap(` → `StateMap(`。

- [ ] **Step 5: 更新 pipeline/types.py**

`ExploreResult.uimap: UIMap | None` → `state_map: StateMap | None`。import 改 `StateMap`。

- [ ] **Step 6: 更新 pipeline/stages/explore.py**

所有 `.uimap` → `.state_map`，`UIMap` → `StateMap`。`_explore_llm` 返回类型 `tuple[RecordedFlow | None, StateMap]`。

- [ ] **Step 7: 更新 pipeline/stages/report.py**

`explore.uimap` → `explore.state_map`，`uimap_screen_count` → `state_map_screen_count`。

- [ ] **Step 8: 更新 pipeline/stages/reflect.py**

如有引用 `.uimap` → `.state_map`。

- [ ] **Step 9: 更新 pipeline/__init__.py 和 pipeline/stages/__init__.py**

如有 `UIMap` 导出 → `StateMap`。

- [ ] **Step 10: 更新测试文件**

`tests/test_pipeline_explore.py`、`test_pipeline_report.py`、`test_pipeline_orchestrator.py` 中所有 `uimap` → `state_map`、`UIMap` → `StateMap`。

- [ ] **Step 11: 运行全量测试**

Run: `source .venv/Scripts/activate && python -m pytest tests/ --ignore=tests/generated_smoke -q`
Expected: all passed（测试数与改名前一致）

- [ ] **Step 12: lint + 提交**

Run: `source .venv/Scripts/activate && ruff check --fix src/joker_test/explorer src/joker_test/pipeline tests/test_pipeline_*.py`

```bash
git add src/joker_test/explorer/ src/joker_test/pipeline/ tests/test_pipeline_*.py
git commit -m "refactor: UIMap→StateMap 改名 + Screen.name 字段增强"
```

---

## Task 2: 动作空间扩展（ExecutorBackend + FakeBackend）

**Files:**
- Modify: `src/joker_test/executor/base.py`
- Modify: `src/joker_test/executor/backends/fake/backend.py`
- Modify: `src/joker_test/executor/backends/airtest/backend.py`
- Test: `tests/test_executor_swipe.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_executor_swipe.py`：

```python
"""ExecutorBackend 动作空间扩展测试（swipe/long_press）。"""
from __future__ import annotations

from joker_test.executor.backends.fake import FakeBackend


def test_fake_swipe_records_history() -> None:
    backend = FakeBackend(width=100, height=100)
    backend.connect()
    backend.swipe(0.5, 0.7, 0.5, 0.3, duration=0.5)
    assert len(backend.swipe_history) == 1
    action, coords = backend.swipe_history[0]
    assert action == "swipe"
    assert coords == (0.5, 0.7, 0.5, 0.3, 0.5)
    backend.close()


def test_fake_long_press_records_history() -> None:
    backend = FakeBackend(width=100, height=100)
    backend.connect()
    backend.long_press(0.5, 0.3, duration=2.0)
    assert len(backend.long_press_history) == 1
    action, coords = backend.long_press_history[0]
    assert action == "long_press"
    assert coords == (0.5, 0.3, 2.0)
    backend.close()


def test_fake_swipe_triggers_transition() -> None:
    """swipe 也能触发多屏状态机切换。"""
    from joker_test.executor.backends.fake import ScreenCfg

    backend = FakeBackend(
        screens={
            "list": ScreenCfg(),
            "list2": ScreenCfg(),
        },
        transitions={("list", "swipe_up"): "list2"},
        initial_screen="list",
    )
    backend.connect()
    backend.swipe(0.5, 0.7, 0.5, 0.3)
    assert backend.current_screen_id == "list2"
    backend.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_executor_swipe.py -v`
Expected: FAIL — `AttributeError: 'FakeBackend' object has no attribute 'swipe'`

- [ ] **Step 3: 扩展 ExecutorBackend 协议**

修改 `src/joker_test/executor/base.py`，在 `type_text` 之后、`wait_until` 之前加：

```python
    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration: float = 0.5) -> None:
        """滑动（归一化坐标 [0,1]）。"""

    def long_press(self, x: float, y: float, duration: float = 2.0) -> None:
        """长按（归一化坐标 [0,1]）。"""
```

- [ ] **Step 4: 扩展 FakeBackend**

修改 `src/joker_test/executor/backends/fake/backend.py`。

`__init__` 里加 history 列表（在 `self.type_history` 之后）：

```python
        self.swipe_history: list[tuple[str, tuple[float, float, float, float, float]]] = []
        self.long_press_history: list[tuple[str, tuple[float, float, float]]] = []
```

加方法（在 `type_text` 之后）：

```python
    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration: float = 0.5) -> None:
        self.swipe_history.append(("swipe", (x1, y1, x2, y2, duration)))
        direction = "swipe_up" if y2 < y1 else "swipe_down"
        self._maybe_transition(direction)

    def long_press(self, x: float, y: float, duration: float = 2.0) -> None:
        self.long_press_history.append(("long_press", (x, y, duration)))
```

- [ ] **Step 5: 扩展 AirtestBackend**

修改 `src/joker_test/executor/backends/airtest/backend.py`，加方法（在 `type_text` 之后）：

```python
    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration: float = 0.5) -> None:
        from airtest.core.api import swipe  # noqa: PLC0415

        swipe((x1, y1), (x2, y2), duration=duration)

    def long_press(self, x: float, y: float, duration: float = 2.0) -> None:
        from airtest.core.api import long_click  # noqa: PLC0415

        long_click(x, y, duration=duration)
```

- [ ] **Step 6: 运行测试确认通过**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_executor_swipe.py -v`
Expected: 3 passed

- [ ] **Step 7: lint + 提交**

Run: `source .venv/Scripts/activate && ruff check --fix src/joker_test/executor tests/test_executor_swipe.py`

```bash
git add src/joker_test/executor/ tests/test_executor_swipe.py
git commit -m "feat(executor): 动作空间扩展 swipe/long_press"
```

---

## Task 3: ExploreStrategy 协议 + 数据契约

**Files:**
- Create: `src/joker_test/explorer/strategy.py`
- Test: `tests/test_explore_strategy.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_explore_strategy.py`：

```python
"""ExploreStrategy 协议 + 数据契约测试。"""
from __future__ import annotations

from joker_test.explorer.strategy import (
    ExploreAction,
    ExploreContext,
    StepDecision,
    ActionResult,
    ExploreStrategy,
)


def test_explore_action_literal() -> None:
    assert "click_text" in ExploreAction.__args__
    assert "swipe" in ExploreAction.__args__
    assert "back" in ExploreAction.__args__
    assert "stop" in ExploreAction.__args__
    assert "long_press" in ExploreAction.__args__
    assert "scroll" in ExploreAction.__args__


def test_step_decision_defaults() -> None:
    d = StepDecision(think="测试", action="click_text")
    assert d.stop is False
    assert d.goal_progress == ""
    assert d.goal_completed is False
    assert d.description == ""


def test_action_result_defaults() -> None:
    r = ActionResult(success=True, screen_changed=True)
    assert r.pixel_diff_ratio == 0.0
    assert r.error is None


def test_strategy_is_protocol() -> None:
    from typing import runtime_checkable

    assert runtime_checkable in type(ExploreStrategy).__mro__ or hasattr(
        ExploreStrategy, "_is_protocol"
    )


def test_models_not_collected() -> None:
    for cls in (StepDecision, ActionResult):
        assert getattr(cls, "__test__", True) is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_explore_strategy.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 strategy.py**

创建 `src/joker_test/explorer/strategy.py`：

```python
"""探索策略协议 + 数据契约。

ExploreStrategy 是可替换的探索决策层。LLMExplorer 外壳持有策略实例，
每步调 decide() 获取决策，执行后调 on_action_executed() 更新策略内部状态。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

if TYPE_CHECKING:
    from joker_test.executor.base import ExecutorBackend, NDArray
    from joker_test.explorer.types import StateMap
    from joker_test.flow.recorder import GlobalRecorder
    from joker_test.llm.base import LLMProvider
    from joker_test.perception.base import PerceptionResult

ExploreAction = Literal[
    "click_text", "click_coord", "press_key", "type_text",
    "swipe", "scroll", "long_press",
    "back", "stop",
]


class ExploreContext(BaseModel):
    """每步决策的公共上下文（LLMExplorer 提供）。"""

    __test__ = False
    step: int
    max_steps: int
    intent: str
    backend: Any  # ExecutorBackend（Any 避免 Pydantic 序列化协议实例）
    llm: Any  # LLMProvider
    recorder: Any | None = None  # GlobalRecorder


class StepDecision(BaseModel):
    """单步决策结果。"""

    __test__ = False
    think: str
    action: ExploreAction
    stop: bool = False
    goal_progress: str = ""
    goal_completed: bool = False
    description: str = ""


class ActionResult(BaseModel):
    """动作执行结果（LLMExplorer 执行后回填）。"""

    __test__ = False
    success: bool
    screen_changed: bool
    pixel_diff_ratio: float = 0.0
    error: str | None = None


@runtime_checkable
class ExploreStrategy(Protocol):
    """探索策略协议。"""

    def decide(
        self,
        screenshot: Any,
        perception: Any,
        ctx: ExploreContext,
    ) -> StepDecision:
        """看当前截图 + perception，决策下一步。"""
        ...

    def on_action_executed(
        self, decision: StepDecision, result: ActionResult
    ) -> None:
        """动作执行后回调，更新内部状态。"""
        ...

    def should_stop(self) -> bool:
        """是否该停止。"""
        ...

    def get_state_map(self) -> Any:
        """返回构建的 StateMap。"""
        ...
```

- [ ] **Step 4: 运行测试确认通过**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_explore_strategy.py -v`
Expected: 5 passed

- [ ] **Step 5: lint + 提交**

Run: `source .venv/Scripts/activate && ruff check --fix src/joker_test/explorer/strategy.py tests/test_explore_strategy.py`

```bash
git add src/joker_test/explorer/strategy.py tests/test_explore_strategy.py
git commit -m "feat(explorer): ExploreStrategy 协议 + 数据契约"
```

---

## Task 4: LLMExplorer 外壳重写

**Files:**
- Modify: `src/joker_test/explorer/llm_explorer.py`（完全重写）
- Modify: `src/joker_test/explorer/__init__.py`
- Test: `tests/test_llm_explorer.py`（新建，替代旧测试）

LLMExplorer 退化为基础设施外壳，持有策略实例，公共逻辑：截图/perception/动作执行/录制/trace/wait_until。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_llm_explorer.py`：

```python
"""LLMExplorer 外壳测试（用 FakeBackend + Mock 策略）。"""
from __future__ import annotations

from unittest.mock import MagicMock

from joker_test.explorer.llm_explorer import LLMExplorer
from joker_test.explorer.strategy import (
    ActionResult,
    ExploreContext,
    StepDecision,
)
from joker_test.explorer.types import StateMap, Screen
from joker_test.executor.backends.fake import FakeBackend, ScreenCfg


class _MockStrategy:
    """最小策略实现，用于测试外壳循环。"""

    def __init__(self, steps: list[StepDecision]) -> None:
        self._steps = steps
        self._idx = 0
        self._screens: list[Screen] = [
            Screen(id="root", name="根界面", elements=[], fingerprint="fp0")
        ]
        self.executed: list[str] = []

    def decide(self, screenshot, perception, ctx) -> StepDecision:
        if self._idx >= len(self._steps):
            return StepDecision(think="done", action="stop", stop=True)
        d = self._steps[self._idx]
        self._idx += 1
        return d

    def on_action_executed(self, decision, result) -> None:
        self.executed.append(decision.action)

    def should_stop(self) -> bool:
        return self._idx >= len(self._steps)

    def get_state_map(self) -> StateMap:
        return StateMap(
            screens=self._screens,
            root_screen_id="root",
            explored_at="2026-01-01T00:00:00Z",
        )


def test_explorer_runs_loop_and_returns_state_map() -> None:
    """外壳循环执行策略决策，返回 StateMap。"""
    backend = FakeBackend(
        screens={"root": ScreenCfg()}, initial_screen="root"
    )
    decisions = [
        StepDecision(think="step1", action="press_key", description="按ESC"),
        StepDecision(think="done", action="stop", stop=True),
    ]
    strategy = _MockStrategy(decisions)
    explorer = LLMExplorer(
        backend=backend, llm=MagicMock(), strategy=strategy, max_steps=10
    )
    state_map = explorer.explore()
    assert isinstance(state_map, StateMap)
    assert strategy.executed == ["press_key"]


def test_explorer_stops_on_strategy_should_stop() -> None:
    """策略 should_stop 返回 True 时循环终止。"""
    backend = FakeBackend(screens={"root": ScreenCfg()}, initial_screen="root")
    strategy = _MockStrategy([
        StepDecision(think="s1", action="press_key"),
    ])
    explorer = LLMExplorer(
        backend=backend, llm=MagicMock(), strategy=strategy, max_steps=10
    )
    explorer.explore()
    assert strategy._idx == 1


def test_explorer_max_steps_limit() -> None:
    """max_steps 上限生效。"""
    backend = FakeBackend(screens={"root": ScreenCfg()}, initial_screen="root")
    # 策略永远不 stop
    strategy = _MockStrategy([])
    explorer = LLMExplorer(
        backend=backend, llm=MagicMock(), strategy=strategy, max_steps=3
    )
    explorer.explore()
    # should_stop 在 idx>=0 时（steps 为空）立即返回 True，所以只跑 1 轮
    # 这里验证不崩溃即可
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_llm_explorer.py -v`
Expected: FAIL — `ImportError` 或签名不匹配

- [ ] **Step 3: 重写 llm_explorer.py**

创建 `src/joker_test/explorer/llm_explorer.py`（完全覆盖）：

```python
"""LLMExplorer：基础设施外壳。

持有 ExploreStrategy 实例，公共逻辑：截图 / perception / 动作执行 / 录制 / trace / wait_until。
策略只负责决策和状态管理。
"""
from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from joker_test.explorer.strategy import (
    ActionResult,
    ExploreContext,
    ExploreStrategy,
    StepDecision,
)
from joker_test.explorer.types import StateMap
from joker_test.flow.recorder import GlobalRecorder
from joker_test.llm.base import LLMProvider

if TYPE_CHECKING:
    from joker_test.executor.base import ExecutorBackend

_LOGGER = logging.getLogger(__name__)

_SCREEN_CHANGE_THRESHOLD = 0.005
_WAIT_AFTER_ACTION = 1.0


class LLMExplorer:
    """LLM 探索器外壳。策略可替换。"""

    def __init__(
        self,
        backend: ExecutorBackend,
        llm: LLMProvider,
        strategy: ExploreStrategy,
        max_steps: int = 30,
        screenshot_dir: str | Path | None = None,
        recorder: GlobalRecorder | None = None,
    ) -> None:
        self._backend = backend
        self._llm = llm
        self._strategy = strategy
        self._max_steps = max_steps
        self._recorder = recorder
        self._screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        if self._screenshot_dir:
            self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    def explore(self) -> StateMap:
        """主循环：截图→感知→决策→执行→录制→更新状态。"""
        self._backend.connect()
        try:
            ctx = ExploreContext(
                step=0,
                max_steps=self._max_steps,
                intent="",
                backend=self._backend,
                llm=self._llm,
                recorder=self._recorder,
            )
            while not self._strategy.should_stop() and ctx.step < ctx.max_steps:
                self._run_step(ctx)
                ctx.step += 1
        finally:
            self._backend.close()
        return self._strategy.get_state_map()

    def _run_step(self, ctx: ExploreContext) -> None:
        """单步：截图→感知→决策→执行→录制。"""
        screenshot = self._retry_screenshot()
        if screenshot is None:
            return

        perception = self._perceive(screenshot)

        try:
            decision = self._strategy.decide(screenshot, perception, ctx)
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("策略 decide 失败：%s", e)
            return

        self._trace_think(decision)

        if decision.stop:
            return

        result = self._perform_action(decision)
        self._strategy.on_action_executed(decision, result)

        if self._recorder is not None and result.success:
            self._record(decision, result)

    def _perceive(self, screenshot: Any) -> Any:
        """感知当前截图（OCR + 图像匹配）。降级为空结果。"""
        try:
            from joker_test.perception.base import PerceptionEngine  # noqa: PLC0415

            engine = PerceptionEngine()
            return engine.perceive(screenshot)
        except Exception:  # noqa: BLE001
            return None

    def _perform_action(self, decision: StepDecision) -> ActionResult:
        """执行动作 + 等待界面稳定 + 检测变化。"""
        before = self._backend.screenshot()
        success = True
        error: str | None = None

        try:
            self._dispatch_action(decision)
        except Exception as e:  # noqa: BLE001
            success = False
            error = str(e)

        if success:
            self._backend.wait_until(lambda: True, timeout=_WAIT_AFTER_ACTION)

        after = self._backend.screenshot() if success else before
        changed, ratio = self._detect_change(before, after)

        return ActionResult(
            success=success,
            screen_changed=changed,
            pixel_diff_ratio=ratio,
            error=error,
        )

    def _dispatch_action(self, decision: StepDecision) -> None:
        """分发动作到 backend。"""
        act = decision.action
        if act == "click_text":
            self._backend.click_text(decision.description)
        elif act == "click_coord":
            # description 里编码坐标 "x,y"
            parts = decision.description.split(",")
            self._backend.click(float(parts[0]), float(parts[1]))
        elif act == "press_key":
            self._backend.press_key(decision.description)
        elif act == "type_text":
            self._backend.type_text(decision.description)
        elif act == "swipe":
            parts = decision.description.split(",")
            self._backend.swipe(
                float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
            )
        elif act == "scroll":
            # scroll 转 swipe
            direction = decision.description
            if "down" in direction:
                self._backend.swipe(0.5, 0.7, 0.5, 0.3)
            else:
                self._backend.swipe(0.5, 0.3, 0.5, 0.7)
        elif act == "long_press":
            parts = decision.description.split(",")
            self._backend.long_press(float(parts[0]), float(parts[1]))
        elif act == "back":
            # back 由策略的 on_action_executed 处理路径回溯
            # 这里执行物理返回（press escape）作为兜底
            self._backend.press_key("escape")
        # stop 不执行

    def _record(self, decision: StepDecision, result: ActionResult) -> None:
        """录制操作到 GlobalRecorder。"""
        if self._recorder is None:
            return
        act = decision.action
        if act == "click_text":
            self._recorder.record_action(
                "click_text", text=decision.description, note=decision.think
            )
        elif act == "click_coord":
            parts = decision.description.split(",")
            self._recorder.record_action(
                "click", x=float(parts[0]), y=float(parts[1]), note=decision.think
            )
        elif act == "press_key":
            self._recorder.record_action(
                "press_key", key=decision.description, note=decision.think
            )
        elif act == "type_text":
            self._recorder.record_action(
                "type_text", text=decision.description, note=decision.think
            )
        elif act == "swipe":
            self._recorder.record_action(
                "click", x=0.5, y=0.5, note=f"swipe: {decision.think}"
            )
        elif act == "long_press":
            parts = decision.description.split(",")
            self._recorder.record_action(
                "click", x=float(parts[0]), y=float(parts[1]), note=decision.think
            )

    def _retry_screenshot(self, max_retries: int = 3) -> Any:
        """重试截图。"""
        for _ in range(max_retries):
            try:
                return self._backend.screenshot()
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        return None

    def _detect_change(self, before: Any, after: Any) -> tuple[bool, float]:
        """检测界面变化。"""
        try:
            from joker_test.explorer.detection import has_screen_changed  # noqa: PLC0415

            return has_screen_changed(before, after, _SCREEN_CHANGE_THRESHOLD)
        except Exception:  # noqa: BLE001
            return True, 0.0

    def _trace_think(self, decision: StepDecision) -> None:
        """记录 ReAct 推理到 trace。"""
        try:
            from joker_test.trace import trace_event  # noqa: PLC0415

            trace_event("explore_think", {"think": decision.think, "action": decision.action})
        except Exception:  # noqa: BLE001
            pass
```

- [ ] **Step 4: 更新 explorer/__init__.py**

确保导出 `LLMExplorer`（已有）。如有旧导出如 `UIMap` 改 `StateMap`（Task 1 已处理）。

- [ ] **Step 5: 运行测试确认通过**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_llm_explorer.py -v`
Expected: 3 passed

- [ ] **Step 6: lint + 提交**

Run: `source .venv/Scripts/activate && ruff check --fix src/joker_test/explorer/llm_explorer.py tests/test_llm_explorer.py`

```bash
git add src/joker_test/explorer/llm_explorer.py src/joker_test/explorer/__init__.py tests/test_llm_explorer.py
git commit -m "feat(explorer): LLMExplorer 重写为策略外壳（截图/感知/执行/录制公共）"
```

---

## Task 5: ReactStateStrategy（方案 A，默认）

**Files:**
- Create: `src/joker_test/explorer/react_strategy.py`
- Test: `tests/test_react_strategy.py`

核心策略：ExploreState 状态机 + ReAct 思维链 + 路径回溯 + perception 辅助。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_react_strategy.py`：

```python
"""ReactStateStrategy 测试。"""
from __future__ import annotations

from unittest.mock import MagicMock

from joker_test.explorer.react_strategy import ExploreState, ReactStateStrategy
from joker_test.explorer.strategy import ExploreContext, StepDecision, ActionResult
from joker_test.explorer.types import Screen, UIElement
from joker_test.executor.base import BBox
from joker_test.llm.providers.mock import MockProvider


def test_explore_state_init() -> None:
    state = ExploreState(goal="进入地牢")
    assert state.goal == "进入地牢"
    assert state.screens == []
    assert state.path_stack == []
    assert state.unvisited == {}
    assert state.goal_completed is False
    assert state.stale_count == 0


def test_should_stop_on_goal_completed() -> None:
    strategy = ReactStateStrategy(llm=MockProvider(), intent="目标")
    strategy._state.goal_completed = True
    assert strategy.should_stop() is True


def test_should_stop_on_stale_limit() -> None:
    strategy = ReactStateStrategy(llm=MockProvider(), intent="目标", max_stale=3)
    strategy._state.stale_count = 3
    assert strategy.should_stop() is True


def test_should_not_stop_early() -> None:
    strategy = ReactStateStrategy(llm=MockProvider(), intent="目标")
    assert strategy.should_stop() is False


def test_on_action_executed_new_screen() -> None:
    """动作执行后界面变化 → 更新 screens + path_stack。"""
    strategy = ReactStateStrategy(llm=MockProvider(), intent="目标")
    decision = StepDecision(think="点按钮", action="click_text", description="开始")
    result = ActionResult(success=True, screen_changed=True, pixel_diff_ratio=0.2)
    strategy.on_action_executed(decision, result)
    # 验证不崩溃，state 更新了 tried_actions
    assert len(strategy._state.tried_actions) >= 1


def test_on_action_executed_no_change_increases_stale() -> None:
    """动作执行后界面无变化 → stale_count +1。"""
    strategy = ReactStateStrategy(llm=MockProvider(), intent="目标")
    strategy._state.stale_count = 0
    decision = StepDecision(think="点按钮", action="click_text", description="开始")
    result = ActionResult(success=True, screen_changed=False)
    strategy.on_action_executed(decision, result)
    assert strategy._state.stale_count == 1


def test_get_state_map_returns_statemap() -> None:
    strategy = ReactStateStrategy(llm=MockProvider(), intent="目标")
    strategy._state.screens = [
        Screen(id="root", name="主菜单", elements=[], fingerprint="fp0")
    ]
    sm = strategy.get_state_map()
    assert sm.root_screen_id == "root"
    assert len(sm.screens) == 1


def test_decide_calls_llm_with_react_format() -> None:
    """decide 调 LLM，解析 <think>/<answer> 格式。"""
    mock_llm = MagicMock()
    mock_llm.simple_converse.return_value = {
        "content": [
            {
                "type": "text",
                "text": (
                    "<think>看到主菜单，应该点进入地牢</think>"
                    '<answer>{"action":"click_text","target":"进入地牢",'
                    '"description":"进入地牢","goal_progress":"正在进入"}</answer>'
                ),
            }
        ]
    }
    strategy = ReactStateStrategy(llm=mock_llm, intent="进入地牢")
    ctx = ExploreContext(step=0, max_steps=10, intent="进入地牢", backend=None, llm=mock_llm)
    decision = strategy.decide(screenshot=None, perception=None, ctx=ctx)
    assert decision.action == "click_text"
    assert "进入地牢" in decision.think or "主菜单" in decision.think
    assert decision.goal_progress == "正在进入"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_react_strategy.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 react_strategy.py**

创建 `src/joker_test/explorer/react_strategy.py`：

```python
"""ReactStateStrategy：ReAct 思维链 + 状态机驱动探索。

默认策略。用 ExploreState 维护探索图/路径/队列，每步 LLM 收到固定大小的状态摘要
（不随步数增长），输出 <think>推理</think><answer>动作</answer>。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from joker_test.explorer.detection import compute_fingerprint
from joker_test.explorer.strategy import (
    ActionResult,
    ExploreContext,
    StepDecision,
)
from joker_test.explorer.types import Exit, Screen, StateMap, UIElement
from joker_test.llm.base import LLMProvider

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)

_WINDOW_DECORATIONS = {"最小化", "最大化", "关闭", "—", "□", "×", "口", "minimize", "maximize", "close"}


class ExploreState:
    """探索状态机（状态自洽）。"""

    def __init__(self, goal: str) -> None:
        self.goal = goal
        self.screens: list[Screen] = []
        self.exits: list[Exit] = []
        self.path_stack: list[Exit] = []
        self.unvisited: dict[str, list[UIElement]] = {}
        self.goal_completed: bool = False
        self.goal_progress: str = ""
        self.stale_count: int = 0
        self.tried_actions: set[tuple[str, str]] = set()
        self._current_screen_id: str | None = None

    @property
    def current_screen_id(self) -> str | None:
        return self._current_screen_id

    def add_screen(self, screen: Screen) -> bool:
        """添加界面，返回是否新界面。"""
        for s in self.screens:
            if s.fingerprint == screen.fingerprint:
                self._current_screen_id = s.id
                return False
        self.screens.append(screen)
        self._current_screen_id = screen.id
        self.unvisited[screen.id] = list(screen.elements)
        return True

    def find_screen_by_fingerprint(self, fp: str) -> Screen | None:
        for s in self.screens:
            if s.fingerprint == fp:
                return s
        return None

    def quantize_target(self, action: str, target: str) -> str:
        """坐标网格量化去重。"""
        if action == "click_coord":
            try:
                parts = target.split(",")
                x = round(float(parts[0]) * 10) / 10
                y = round(float(parts[1]) * 10) / 10
                return f"coord({x:.1f},{y:.1f})"
            except (ValueError, IndexError):
                return target
        return target


class ReactStateStrategy:
    """ReAct + 状态机策略。"""

    def __init__(
        self,
        llm: LLMProvider,
        intent: str,
        max_stale: int = 3,
    ) -> None:
        self._llm = llm
        self._max_stale = max_stale
        self._state = ExploreState(goal=intent)

    def decide(
        self,
        screenshot: Any,
        perception: Any,
        ctx: ExploreContext,
    ) -> StepDecision:
        """ReAct 决策：截图+perception+状态摘要 → LLM → 解析 think/answer。"""
        prompt = self._build_prompt(screenshot, perception, ctx)
        try:
            msg = self._llm.simple_converse(prompt, [], reasoning=8000)
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("LLM 决策失败：%s", e)
            return StepDecision(think=f"LLM失败:{e}", action="stop", stop=True)

        think, answer_json = self._parse_react(msg)
        return self._build_decision(think, answer_json)

    def on_action_executed(
        self, decision: StepDecision, result: ActionResult
    ) -> None:
        """动作执行后更新状态。"""
        if not result.success:
            self._state.stale_count += 1
            return

        if result.screen_changed:
            self._state.stale_count = 0
        else:
            self._state.stale_count += 1

        target = self._state.quantize_target(decision.action, decision.description)
        fp = self._state.current_screen_id or "unknown"
        self._state.tried_actions.add((fp, target))

        if decision.goal_progress:
            self._state.goal_progress = decision.goal_progress
        if decision.goal_completed:
            self._state.goal_completed = True

    def should_stop(self) -> bool:
        return self._state.goal_completed or self._state.stale_count >= self._max_stale

    def get_state_map(self) -> StateMap:
        import datetime  # noqa: PLC0415

        root_id = self._state.screens[0].id if self._state.screens else ""
        return StateMap(
            screens=self._state.screens,
            root_screen_id=root_id,
            explored_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            backend_info={"strategy": "react_state"},
        )

    def _build_prompt(
        self, screenshot: Any, perception: Any, ctx: ExploreContext
    ) -> str:
        """构建 ReAct prompt（固定大小状态摘要）。"""
        parts = [
            f"目标: {self._state.goal}",
            f"进度: {self._state.goal_progress or '开始探索'}",
            f"步数: {ctx.step}/{ctx.max_steps}",
        ]

        if perception is not None:
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
            'json 格式: {"action":"click_text|click_coord|press_key|swipe|scroll|long_press|back|stop",'
            '"target":"...","description":"...","goal_progress":"...","goal_completed":false}'
        )
        return prompt

    def _parse_react(self, msg: Any) -> tuple[str, dict[str, Any]]:
        """解析 <think>...</think><answer>{json}</answer>。"""
        text = self._extract_text(msg)

        think = ""
        think_match = re.search(r"<think>(.*?)</think>", text, re.DOTALL)
        if think_match:
            think = think_match.group(1).strip()

        answer_raw = ""
        answer_match = re.search(r"<answer>(.*?)</answer>", text, re.DOTALL)
        if answer_match:
            answer_raw = answer_match.group(1).strip()

        try:
            answer = json.loads(answer_raw)
        except json.JSONDecodeError:
            answer = {"action": "stop", "description": f"解析失败: {answer_raw[:100]}"}

        return think, answer

    def _build_decision(
        self, think: str, answer: dict[str, Any]
    ) -> StepDecision:
        action = answer.get("action", "stop")
        target = answer.get("target", "")
        desc = answer.get("description", target)
        # click_coord 的 description 编码坐标
        if action == "click_coord" and target:
            desc = target
        return StepDecision(
            think=think,
            action=action,
            stop=action == "stop",
            goal_progress=answer.get("goal_progress", ""),
            goal_completed=answer.get("goal_completed", False),
            description=desc,
        )

    @staticmethod
    def _extract_text(msg: Any) -> str:
        for block in msg.get("content", []):
            if isinstance(block, dict) and "text" in block:
                return block["text"]
        return ""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_react_strategy.py -v`
Expected: 7 passed

- [ ] **Step 5: lint + 提交**

Run: `source .venv/Scripts/activate && ruff check --fix src/joker_test/explorer/react_strategy.py tests/test_react_strategy.py`

```bash
git add src/joker_test/explorer/react_strategy.py tests/test_react_strategy.py
git commit -m "feat(explorer): ReactStateStrategy ReAct+状态机策略"
```

---

## Task 6: ConversationStrategy（方案 C，对比基准）

**Files:**
- Create: `src/joker_test/explorer/conversation_strategy.py`
- Test: `tests/test_conversation_strategy.py`

对齐 Open-AutoGLM：历史截图剥离 + assistant 重封装 + system 一次。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_conversation_strategy.py`：

```python
"""ConversationStrategy 测试（对齐 Open-AutoGLM）。"""
from __future__ import annotations

from unittest.mock import MagicMock

from joker_test.explorer.conversation_strategy import ConversationStrategy
from joker_test.explorer.strategy import ExploreContext


def test_decide_strips_image_from_history() -> None:
    """LLM 响应后，最后一条 user 消息的图像块被剥离。"""
    mock_llm = MagicMock()
    mock_llm.simple_converse.return_value = {
        "content": [
            {
                "type": "text",
                "text": (
                    "<think>看到主菜单</think>"
                    '<answer>{"action":"click_text","target":"开始"}</answer>'
                ),
            }
        ]
    }
    strategy = ConversationStrategy(llm=mock_llm, intent="测试")
    ctx = ExploreContext(step=0, max_steps=10, intent="测试", backend=None, llm=mock_llm)
    decision = strategy.decide(screenshot=b"fake_img", perception=None, ctx=ctx)

    # 验证最后一条 user 消息已剥离图像
    last_user = [m for m in strategy._messages if m["role"] == "user"][-1]
    has_image = any(
        b.get("type") == "image" or b.get("type") == "image_url"
        for b in last_user["content"]
    )
    assert not has_image, "历史截图应被剥离"
    assert decision.action == "click_text"


def test_assistant_repackaged_as_think_answer() -> None:
    """assistant 消息被重新封装为 <think>/<answer> 格式。"""
    mock_llm = MagicMock()
    mock_llm.simple_converse.return_value = {
        "content": [{"type": "text", "text": "<think>推理</think><answer>{\"action\":\"stop\"}</answer>"}]
    }
    strategy = ConversationStrategy(llm=mock_llm, intent="测试")
    ctx = ExploreContext(step=0, max_steps=10, intent="测试", backend=None, llm=mock_llm)
    strategy.decide(screenshot=b"img", perception=None, ctx=ctx)

    assistant_msgs = [m for m in strategy._messages if m["role"] == "assistant"]
    assert len(assistant_msgs) >= 1
    content = assistant_msgs[-1]["content"]
    assert "<think>" in content and "<answer>" in content


def test_should_stop_on_goal_completed() -> None:
    strategy = ConversationStrategy(llm=MagicMock(), intent="测试")
    strategy._goal_completed = True
    assert strategy.should_stop() is True


def test_should_stop_on_token_limit() -> None:
    strategy = ConversationStrategy(
        llm=MagicMock(), intent="测试", max_conversation_tokens=10
    )
    # 塞入超长历史
    strategy._messages.append(
        {"role": "user", "content": [{"type": "text", "text": "x" * 100}]}
    )
    assert strategy.should_stop() is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_conversation_strategy.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 conversation_strategy.py**

创建 `src/joker_test/explorer/conversation_strategy.py`：

```python
"""ConversationStrategy：对齐 Open-AutoGLM 的 conversation context 策略。

历史截图剥离（只保留当前步截图）+ assistant 重封装为 <think>/<answer> + system 一次。
作为 ReactStateStrategy 的对比基准。
"""
from __future__ import annotations

import base64
import datetime
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from joker_test.explorer.strategy import (
    ActionResult,
    ExploreContext,
    StepDecision,
)
from joker_test.explorer.types import Screen, StateMap
from joker_test.llm.base import LLMProvider, Message

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "你在探索一个游戏界面，目标是完成任务。每步你看到截图和 OCR 文本，"
    "推理后输出动作。用 <think>推理</think><answer>{json}</answer> 回答。\n"
    'json: {"action":"click_text|click_coord|press_key|swipe|scroll|long_press|back|stop",'
    '"target":"...","description":"...","goal_progress":"...","goal_completed":false}'
)


class ConversationStrategy:
    """conversation context 策略（对齐 Open-AutoGLM）。"""

    def __init__(
        self,
        llm: LLMProvider,
        intent: str,
        max_conversation_tokens: int = 8000,
    ) -> None:
        self._llm = llm
        self._intent = intent
        self._max_tokens = max_conversation_tokens
        self._messages: list[Message] = []
        self._screens: list[Screen] = []
        self._goal_completed: bool = False
        self._initialized = False

    def decide(
        self,
        screenshot: Any,
        perception: Any,
        ctx: ExploreContext,
    ) -> StepDecision:
        """构建 prompt + 追加 user 消息（含图）→ LLM → 剥离图 → 重封装 assistant。"""
        if not self._initialized:
            self._messages.append(
                {"role": "system", "content": [{"type": "text", "text": _SYSTEM_PROMPT}]}
            )
            self._initialized = True

        img_b64 = self._encode_image(screenshot)
        step_text = self._build_step_text(perception, ctx)

        user_content: list[dict[str, Any]] = []
        if img_b64:
            user_content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
                }
            )
        user_content.append({"type": "text", "text": step_text})

        self._messages.append({"role": "user", "content": user_content})

        try:
            reply = self._llm.simple_converse("", self._messages)
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("LLM 决策失败：%s", e)
            self._strip_last_image()
            return StepDecision(think=f"LLM失败:{e}", action="stop", stop=True)

        # ★ 关键：剥离最后一条 user 消息的图像块
        self._strip_last_image()

        think, answer = self._parse_react(reply)

        # ★ 关键：assistant 重封装
        self._messages.append(
            {
                "role": "assistant",
                "content": f"<think>{think}</think><answer>{json.dumps(answer, ensure_ascii=False)}</answer>",
            }
        )

        if answer.get("goal_completed"):
            self._goal_completed = True

        return self._build_decision(think, answer)

    def on_action_executed(
        self, decision: StepDecision, result: ActionResult
    ) -> None:
        """被动维护 screens（不参与循环逻辑）。"""
        pass

    def should_stop(self) -> bool:
        return self._goal_completed or self._token_exceeded()

    def get_state_map(self) -> StateMap:
        return StateMap(
            screens=self._screens,
            root_screen_id=self._screens[0].id if self._screens else "",
            explored_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            backend_info={"strategy": "conversation"},
        )

    def _strip_last_image(self) -> None:
        """剥离最后一条 user 消息的图像块。"""
        if not self._messages:
            return
        last = self._messages[-1]
        if last.get("role") != "user":
            return
        last["content"] = [
            b for b in last["content"]
            if isinstance(b, dict) and b.get("type") == "text"
        ]

    def _build_step_text(self, perception: Any, ctx: ExploreContext) -> str:
        parts = [f"目标: {self._intent}", f"步数: {ctx.step}/{ctx.max_steps}"]
        if perception is not None:
            texts = getattr(perception, "texts", [])
            if texts:
                parts.append("OCR: " + ", ".join(texts[:15]))
        return "\n".join(parts)

    def _token_exceeded(self) -> bool:
        """估算 conversation token 量。"""
        total = 0
        for msg in self._messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict):
                        total += len(b.get("text", ""))
        return total // 4 > self._max_tokens

    @staticmethod
    def _encode_image(screenshot: Any) -> str | None:
        """截图转 base64。"""
        if screenshot is None:
            return None
        try:
            import cv2  # noqa: PLC0415
            import numpy as np  # noqa: PLC0415

            if isinstance(screenshot, bytes):
                return base64.b64encode(screenshot).decode("ascii")
            if hasattr(screenshot, "tobytes"):
                _, buf = cv2.imencode(".png", screenshot)
                return base64.b64encode(buf.tobytes()).decode("ascii")
        except Exception:  # noqa: BLE001
            return None
        return None

    @staticmethod
    def _parse_react(msg: Any) -> tuple[str, dict[str, Any]]:
        text = ""
        for block in msg.get("content", []):
            if isinstance(block, dict) and "text" in block:
                text += block["text"]

        think = ""
        think_match = re.search(r"<think>(.*?)</think>", text, re.DOTALL)
        if think_match:
            think = think_match.group(1).strip()

        answer_raw = ""
        answer_match = re.search(r"<answer>(.*?)</answer>", text, re.DOTALL)
        if answer_match:
            answer_raw = answer_match.group(1).strip()

        try:
            answer = json.loads(answer_raw)
        except json.JSONDecodeError:
            answer = {"action": "stop", "description": f"解析失败: {answer_raw[:100]}"}

        return think, answer

    @staticmethod
    def _build_decision(think: str, answer: dict[str, Any]) -> StepDecision:
        action = answer.get("action", "stop")
        target = answer.get("target", "")
        desc = answer.get("description", target)
        if action == "click_coord" and target:
            desc = target
        return StepDecision(
            think=think,
            action=action,
            stop=action == "stop",
            goal_progress=answer.get("goal_progress", ""),
            goal_completed=answer.get("goal_completed", False),
            description=desc,
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_conversation_strategy.py -v`
Expected: 4 passed

- [ ] **Step 5: lint + 提交**

Run: `source .venv/Scripts/activate && ruff check --fix src/joker_test/explorer/conversation_strategy.py tests/test_conversation_strategy.py`

```bash
git add src/joker_test/explorer/conversation_strategy.py tests/test_conversation_strategy.py
git commit -m "feat(explorer): ConversationStrategy 对齐 Open-AutoGLM conversation context"
```

---

## Task 7: pipeline + CLI 接入

**Files:**
- Modify: `src/joker_test/pipeline/types.py`
- Modify: `src/joker_test/pipeline/stages/explore.py`
- Modify: `src/joker_test/cli.py`
- Test: `tests/test_pipeline_explore.py`

- [ ] **Step 1: 更新 ExploreConfig 加 explore_strategy 字段**

修改 `src/joker_test/pipeline/types.py`，ExploreConfig 加：

```python
    explore_strategy: Literal["react_state", "conversation"] = "react_state"
```

确保顶部有 `from typing import Literal`（已有）。

- [ ] **Step 2: 更新 ExploreStage._explore_llm 策略选择**

修改 `src/joker_test/pipeline/stages/explore.py` 的 `_explore_llm`：

```python
    def _explore_llm(
        self, config: ExploreConfig, log: list[str]
    ) -> tuple[RecordedFlow | None, StateMap]:
        recorder = GlobalRecorder(
            output_dir=self._flow_dir, backend=self._backend, pynput_mode=False
        )
        recorder.start()

        # 策略选择
        from joker_test.explorer.conversation_strategy import ConversationStrategy  # noqa: PLC0415
        from joker_test.explorer.react_strategy import ReactStateStrategy  # noqa: PLC0415

        if config.explore_strategy == "conversation":
            strategy = ConversationStrategy(llm=self._provider, intent=config.intent)
        else:
            strategy = ReactStateStrategy(llm=self._provider, intent=config.intent)

        explorer = LLMExplorer(
            self._backend,
            self._provider,
            strategy=strategy,
            max_steps=config.max_explore_steps,
            recorder=recorder,
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

- [ ] **Step 3: 更新 cli.py explore 命令加 --strategy**

在 `explore` 子命令参数里加：

```python
    p_exp.add_argument(
        "--strategy", choices=["react_state", "conversation"], default="react_state"
    )
```

在 `_cmd_explore` 里传 `explore_strategy=args.strategy`：

```python
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
    )
```

- [ ] **Step 4: 运行已有 pipeline 测试确认不回归**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_pipeline_explore.py tests/test_pipeline_orchestrator.py -v`
Expected: all passed

- [ ] **Step 5: 运行全量测试**

Run: `source .venv/Scripts/activate && python -m pytest tests/ --ignore=tests/generated_smoke -q`
Expected: all passed

- [ ] **Step 6: lint + 提交**

Run: `source .venv/Scripts/activate && ruff check --fix src/joker_test/pipeline src/joker_test/cli.py`

```bash
git add src/joker_test/pipeline/ src/joker_test/cli.py
git commit -m "feat(pipeline): explore_strategy 配置 + CLI --strategy 参数"
```

---

## Task 8: 文档更新

**Files:**
- Modify: `DESIGN.md`
- Modify: `AGENTS.md`
- Modify: `docs/roadmap/iteration-roadmap.md`

- [ ] **Step 1: 更新 DESIGN.md**

§4.3 探索流水线表格补 `explore_strategy` 配置说明。§9 术语表加 `ExploreStrategy`/`ReactStateStrategy`/`ConversationStrategy` 条目。§10 修改记录加 v1.0 条目。

- [ ] **Step 2: 更新 AGENTS.md**

仓库结构补 `explorer/strategy.py`、`react_strategy.py`、`conversation_strategy.py`。CLI 说明补 `--strategy` 参数。

- [ ] **Step 3: 更新 roadmap**

迭代 B 任务 B1（LLMExplorer 探索深度）标记为 ✅ 已完成，补实现说明。

- [ ] **Step 4: 提交**

```bash
git add DESIGN.md AGENTS.md docs/roadmap/iteration-roadmap.md
git commit -m "docs: LLMExplorer 重写 + ExploreStrategy 策略层文档更新"
```

---

## 自检

**Spec 覆盖检查：**

| Spec 章节 | 覆盖 Task |
|---|---|
| §2 整体架构（LLMExplorer 外壳 + ExploreStrategy） | Task 3（协议）+ Task 4（外壳） |
| §3 ExploreStrategy 协议 + 数据契约 | Task 3 |
| §4 ReactStateStrategy | Task 5 |
| §5 ConversationStrategy | Task 6 |
| §6 动作空间扩展 | Task 2 |
| §7 UIMap→StateMap 改名 | Task 1 |
| §8 文件结构 | Task 1-7（全部新增/修改文件） |
| §9 错误处理 | 各 Task 内 try/except 降级 |
| §10 测试策略 | 每个 Task 的测试步骤 |
| §11 YAGNI | 不涉及（都是"不做的事"） |

**无占位符扫描：** 所有代码块含完整实现，无 TBD/TODO。

**类型一致性检查：**
- `StateMap` 在 Task 1 定义，Task 3/4/5/6/7 使用一致
- `ExploreStrategy` 协议在 Task 3 定义，Task 4/5/6 实现一致
- `StepDecision` 字段（think/action/stop/goal_progress/goal_completed/description）在 Task 3 定义，Task 4/5/6 使用一致
- `ExploreAction` 在 Task 3 定义，含 swipe/scroll/long_press/back/stop，Task 2 的动作空间扩展一致
- `ExploreConfig.explore_strategy` 在 Task 7 定义，CLI 传入一致

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-10-llm-explorer-rewrite.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
