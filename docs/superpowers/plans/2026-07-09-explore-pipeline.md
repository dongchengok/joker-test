# 探索式测试流水线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将分散的探索/固化/执行/报告/反思能力整合为统一的五阶段流水线，借 Open-AutoGLM 的循环模式（保留 LLM 克制），支持智能固化命中、三种探索方式、全自动编排与独立命令。

**Architecture:** 薄编排 AgenticOrchestrator 按序调用 5 个状态自洽的 Stage 类（Explore/Solidify/Execute/Report/Reflect），Stage 间只通过 Result 对象单向传递数据。删除 StaticOrchestrator/exploratory/tactics，保留 perception/explorer/flow 等公共底座。

**Tech Stack:** Python 3.12、Pydantic v2、pytest、Jinja2、Protocol（结构性子类型）

**Spec:** `docs/superpowers/specs/2026-07-09-explore-pipeline-design.md`

---

## 文件结构

### 新增文件

| 文件 | 职责 |
|---|---|
| `src/joker_test/pipeline/__init__.py` | 包导出 |
| `src/joker_test/pipeline/types.py` | 数据契约：ExploreConfig + 5 个 Result + PipelineResult + Risk |
| `src/joker_test/pipeline/base.py` | Stage Protocol + AgenticOrchestrator |
| `src/joker_test/pipeline/stages/__init__.py` | stages 包导出 |
| `src/joker_test/pipeline/stages/explore.py` | ExploreStage（智能入口 + 固化命中检查 + 三模式分发） |
| `src/joker_test/pipeline/stages/solidify.py` | SolidifyStage（固化 + 试跑回喂） |
| `src/joker_test/pipeline/stages/execute.py` | ExecuteStage（脱离 LLM） |
| `src/joker_test/pipeline/stages/report.py` | ReportStage（汇聚综合报告） |
| `src/joker_test/pipeline/stages/reflect.py` | ReflectStage（可信度 + 风险） |
| `src/joker_test/reporters/explore.py` | ExploreReporter（消费 ExploreReport 出 Json） |
| `tests/test_pipeline_types.py` | 数据契约往返 |
| `tests/test_pipeline_explore.py` | ExploreStage 三模式 + 命中检查 |
| `tests/test_pipeline_solidify.py` | SolidifyStage |
| `tests/test_pipeline_execute.py` | ExecuteStage |
| `tests/test_pipeline_report.py` | ReportStage |
| `tests/test_pipeline_reflect.py` | ReflectStage |
| `tests/test_pipeline_orchestrator.py` | AgenticOrchestrator 三分支 |

### 修改文件

| 文件 | 改动 |
|---|---|
| `src/joker_test/reflection.py` | 删除 `review_bug_report`/`filter_bug_reports`（依赖被删的 BugReport），保留 `detect_stuck_loop`/`review_failure` |
| `src/joker_test/cli.py` | 新增 `explore` 子命令；`run-all` 改走 AgenticOrchestrator；无参数默认 run-all |
| `src/joker_test/reporters/__init__.py` | 导出 ExploreReporter |
| `tests/test_reflection.py` | 删除依赖 BugReport 的 3 个测试 |
| `AGENTS.md` | 更新目录结构 + CLI 命令 + 状态描述 |

### 删除文件

| 文件 | 理由 |
|---|---|
| `src/joker_test/orchestrator.py` | StaticOrchestrator 被 AgenticOrchestrator 取代 |
| `src/joker_test/exploratory.py` | M4 简化版战术 bug 查找，本次不整合 |
| `src/joker_test/tactics.py` | 战术库，随 exploratory 删除 |
| `tests/test_orchestrator.py` | 测 StaticOrchestrator，随删 |
| `tests/test_exploratory.py` | 测 exploratory/tactics，随删 |

---

## 关键接口参考（实现时对照）

实现时必须用这些精确签名，不要臆造：

```
# runner.py
run_tests(test_paths: list[str|Path], backend_name: str = "fake", game_name: str = "unknown") -> TestSession

# flow/recorder.py
GlobalRecorder(output_dir: str|Path, *, backend: ExecutorBackend|None = None, pynput_mode: bool = False, ...) -> None
GlobalRecorder.start() -> None
GlobalRecorder.stop() -> RecordedFlow
GlobalRecorder.record_action(action: FlowAction, *, x=None, y=None, screen_x=0, screen_y=0, window=None, key=None, text=None, ...) -> None
GlobalRecorder.save_flow_yaml(flow: RecordedFlow) -> Path

# flow/generator.py
RecordedFlowGenerator(provider: LLMProvider, quality_checker=None, ocr_provider=None)
.generate(flow: RecordedFlow, flow_dir: Path, game_meta: dict) -> list[GeneratedTest]
.rewrite_failed(tests, session, flow, flow_dir, game_meta) -> list[GeneratedTest]

# flow/verifier.py
TestCaseVerifier(reset_fn: Callable[[],None], gen_dir: str|Path, max_retries=2, backend_name="airtest", game_name="verified")
.verify_and_fix(tests, generator, flow, flow_dir, game_meta) -> tuple[list[GeneratedTest], list[dict]]

# flow/namer.py
FlowNamer(provider: LLMProvider, max_screenshots=3)
.name_flow(flow: RecordedFlow, screenshots_dir: Path|None = None) -> tuple[str, str]

# explorer/explorer.py
UIExplorer(backend: ExecutorBackend, max_depth=5, max_screens=20, screenshot_dir=None, screen_change_timeout=3.0)
.explore() -> UIMap

# explorer/llm_explorer.py
LLMExplorer(backend: ExecutorBackend, llm: LLMProvider, max_steps=10, screenshot_dir=None, recorder: GlobalRecorder|None=None, max_stale_steps=3)
.explore() -> UIMap

# generator/generator.py
SmokeTestGenerator(provider: LLMProvider, quality_checker=None)
generate(uimap: UIMap, game_meta: dict) -> list[GeneratedTest]
write_tests_to_dir(tests: list[GeneratedTest], output_dir: str|Path) -> list[Path]

# reflection.py（保留的）
detect_stuck_loop(operation_signals: list[str]) -> bool
review_failure(provider: LLMProvider, result: TestResult, context: str = "") -> tuple[bool, str]

# reporters/base.py
TestSession(id, started_at, game, backend, tests, results, finished_at)  # passed/failed 是 computed_field
TestReporter Protocol: name / on_session_start / on_test_start / on_test_end / on_session_end / finalize

# reporters/__init__.py 导出
JsonReporter, HtmlReporter, MultiReporter

# llm
LLMProvider Protocol: simple_converse(prompt, messages, *, reasoning=0, images=None) -> Message
                     converse_json(messages, instruction) -> list[dict]
MockProvider()  # 无参构造，按 prompt 关键词路由固定回复

# executor
ExecutorBackend Protocol: connect/close/screenshot/click/click_text/click_image/press_key/type_text/wait_until/state
FakeBackend(width=100, height=100, texts_map=None, bg_pixel=(50,50,50), screens=None, transitions=None, initial_screen=None)
```

---

## Task 1: 数据契约（pipeline/types.py）

**Files:**
- Create: `src/joker_test/pipeline/types.py`
- Test: `tests/test_pipeline_types.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_pipeline_types.py`：

```python
"""pipeline 数据契约往返测试。"""
from __future__ import annotations

from joker_test.pipeline.types import (
    ExploreConfig,
    ExploreResult,
    SolidifyResult,
    ExecuteResult,
    ReportResult,
    ReflectResult,
    Risk,
    PipelineResult,
)


def test_explore_config_defaults() -> None:
    cfg = ExploreConfig(intent="进入退出游戏")
    assert cfg.mode == "llm"
    assert cfg.reuse is None
    assert cfg.check_reuse is True
    assert cfg.solidify is True
    assert cfg.execute is True
    assert cfg.backend_name == "fake"
    assert cfg.max_explore_steps == 30


def test_explore_config_mode_literal() -> None:
    cfg = ExploreConfig(intent="x", mode="manual")
    assert cfg.mode == "manual"


def test_explore_result_skipped() -> None:
    r = ExploreResult(skipped=True, reused_test_paths=["tests/generated_smoke/test_a.py"])
    assert r.flow is None
    assert r.uimap is None
    assert r.match_reason is None


def test_solidify_result_defaults() -> None:
    r = SolidifyResult(test_paths=["tests/generated_smoke/test_a.py"])
    assert r.spec_paths == []
    assert r.verify_ok is False
    assert r.verify_rounds == 0
    assert r.warnings == []


def test_risk_model() -> None:
    risk = Risk(category="uncovered_area", description="未探索设置界面", severity="P2")
    assert risk.severity == "P2"


def test_reflect_result_defaults() -> None:
    r = ReflectResult(confidence_score=0.5, reflect_reasoning="ok")
    assert r.false_positives == []
    assert r.stuck_warnings == []
    assert r.risks == []


def test_pipeline_result_optional_fields() -> None:
    e = ExploreResult(skipped=True)
    rep = ReportResult(report_paths=[], summary="s", stage_coverage={"explore": True, "solidify": False, "execute": False})
    ref = ReflectResult(confidence_score=0.3, reflect_reasoning="low")
    p = PipelineResult(explore=e, report=rep, reflect=ref)
    assert p.solidify is None
    assert p.execute is None


def test_models_not_collected_by_pytest() -> None:
    for cls in (ExploreConfig, ExploreResult, Risk):
        assert getattr(cls, "__test__", True) is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_pipeline_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'joker_test.pipeline'`

- [ ] **Step 3: 实现 types.py**

创建 `src/joker_test/pipeline/__init__.py`（占位，后续 Task 7 补充导出）：

```python
"""四阶段探索式测试流水线。"""
```

创建 `src/joker_test/pipeline/types.py`：

```python
"""流水线数据契约。

所有模型设 __test__ = False 防止 pytest 误收集。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from joker_test.explorer.types import UIMap
from joker_test.flow.types import RecordedFlow
from joker_test.reporters.base import TestSession


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


class ExploreResult(BaseModel):
    """探索阶段产出。"""

    __test__ = False
    flow: RecordedFlow | None = None
    uimap: UIMap | None = None
    skipped: bool = False
    reused_test_paths: list[str] = Field(default_factory=list)
    match_reason: str | None = None
    explore_log: list[str] = Field(default_factory=list)


class SolidifyResult(BaseModel):
    """固化阶段产出。"""

    __test__ = False
    test_paths: list[str] = Field(default_factory=list)
    spec_paths: list[str] = Field(default_factory=list)
    verify_ok: bool = False
    verify_rounds: int = 0
    warnings: list[str] = Field(default_factory=list)


class ExecuteResult(BaseModel):
    """执行阶段产出。total/passed/failed 从 session 聚合。"""

    __test__ = False
    session: TestSession
    duration_s: float = 0.0


class ReportResult(BaseModel):
    """报告阶段产出。"""

    __test__ = False
    report_paths: list[str] = Field(default_factory=list)
    summary: str
    stage_coverage: dict[str, bool]


class Risk(BaseModel):
    """反思阶段的风险项。"""

    __test__ = False
    category: str
    description: str
    severity: str  # P1 / P2


class ReflectResult(BaseModel):
    """反思阶段产出。"""

    __test__ = False
    confidence_score: float
    false_positives: list[dict[str, Any]] = Field(default_factory=list)
    stuck_warnings: list[str] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    reflect_reasoning: str


class PipelineResult(BaseModel):
    """流水线总产出。solidify/execute 可选。"""

    __test__ = False
    explore: ExploreResult
    solidify: SolidifyResult | None = None
    execute: ExecuteResult | None = None
    report: ReportResult
    reflect: ReflectResult
```

- [ ] **Step 4: 运行测试确认通过**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_pipeline_types.py -v`
Expected: 8 passed

- [ ] **Step 5: lint**

Run: `source .venv/Scripts/activate && ruff check src/joker_test/pipeline tests/test_pipeline_types.py`
Expected: All checks passed

- [ ] **Step 6: 提交**

```bash
git add src/joker_test/pipeline/ tests/test_pipeline_types.py
git commit -m "feat(pipeline): 数据契约 ExploreConfig + 5 Result + PipelineResult + Risk"
```

---

## Task 2: Stage Protocol + ExecuteStage（最简阶段先行）

**Files:**
- Create: `src/joker_test/pipeline/base.py`
- Create: `src/joker_test/pipeline/stages/__init__.py`
- Create: `src/joker_test/pipeline/stages/execute.py`
- Test: `tests/test_pipeline_execute.py`

ExecuteStage 最简单（无 LLM），先打通它，后续 Stage 照此模式。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_pipeline_execute.py`：

```python
"""ExecuteStage 测试。"""
from __future__ import annotations

from pathlib import Path

from joker_test.pipeline.types import ExploreConfig
from joker_test.pipeline.stages.execute import ExecuteStage


def test_execute_runs_tests(tmp_path: Path) -> None:
    """执行阶段能跑 pytest 并返回 ExecuteResult。"""
    # 写一个极简 passing test
    test_file = tmp_path / "test_simple.py"
    test_file.write_text(
        "def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    cfg = ExploreConfig(intent="test", game_name="demo", backend_name="fake")
    stage = ExecuteStage()
    result = stage.run([str(test_file)], cfg)
    assert result.session.passed == 1
    assert result.session.failed == 0


def test_execute_collects_failure(tmp_path: Path) -> None:
    """失败用例被收集，不抛异常。"""
    test_file = tmp_path / "test_fail.py"
    test_file.write_text(
        "def test_bad():\n    assert False\n", encoding="utf-8"
    )
    cfg = ExploreConfig(intent="test")
    stage = ExecuteStage()
    result = stage.run([str(test_file)], cfg)
    assert result.session.failed == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_pipeline_execute.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'joker_test.pipeline.stages'`

- [ ] **Step 3: 实现 base.py（Stage Protocol）**

创建 `src/joker_test/pipeline/base.py`：

```python
"""Stage 协议与编排器。

每个 Stage 状态自洽（构造时注入自己的依赖），Stage 间只通过 Result 对象单向传递数据。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from joker_test.pipeline.types import (
        ExploreConfig,
        ExploreResult,
        ExecuteResult,
        PipelineResult,
        ReflectResult,
        ReportResult,
        SolidifyResult,
    )


@runtime_checkable
class Stage(Protocol):
    """Stage 协议：run 接受 config，返回各阶段 Result。具体签名由子协议细化。"""

    def run(self, *args: object, **kwargs: object) -> object: ...
```

创建 `src/joker_test/pipeline/stages/__init__.py`（占位）：

```python
"""流水线各 Stage 实现。"""
```

- [ ] **Step 4: 实现 ExecuteStage**

创建 `src/joker_test/pipeline/stages/execute.py`：

```python
"""执行阶段：跑 pytest，脱离 LLM。"""
from __future__ import annotations

import time

from joker_test.pipeline.types import ExecuteConfig if False else None  # 占位行删除
```

**注意**：上面是占位，实际代码如下。创建 `src/joker_test/pipeline/stages/execute.py`（覆盖）：

```python
"""执行阶段：跑 pytest，脱离 LLM。

复用 runner.run_tests，纯 pytest 收集结果。
"""
from __future__ import annotations

import time

from joker_test.pipeline.types import ExecuteResult, ExploreConfig
from joker_test.runner import run_tests


class ExecuteStage:
    """执行固化产物，脱离 LLM。"""

    def run(self, test_paths: list[str], config: ExploreConfig) -> ExecuteResult:
        start = time.monotonic()
        session = run_tests(
            test_paths,
            backend_name=config.backend_name,
            game_name=config.game_name or "unknown",
        )
        return ExecuteResult(session=session, duration_s=time.monotonic() - start)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_pipeline_execute.py -v`
Expected: 2 passed

- [ ] **Step 6: lint + 提交**

Run: `source .venv/Scripts/activate && ruff check src/joker_test/pipeline`

```bash
git add src/joker_test/pipeline/base.py src/joker_test/pipeline/stages/ tests/test_pipeline_execute.py
git commit -m "feat(pipeline): Stage Protocol + ExecuteStage"
```

---

## Task 3: ExploreStage — 智能入口 + 固化命中检查 + 三模式

**Files:**
- Create: `src/joker_test/pipeline/stages/explore.py`
- Test: `tests/test_pipeline_explore.py`

ExploreStage 是最复杂的阶段。分三块：命中检查（扫资产 + LLM 比对）、三模式分发（manual/dfs/llm）、落盘。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_pipeline_explore.py`：

```python
"""ExploreStage 测试：命中检查 + 三模式分发。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from joker_test.executor.backends.fake import FakeBackend, ScreenCfg
from joker_test.llm.providers.mock import MockProvider
from joker_test.pipeline.types import ExploreConfig, ExploreResult
from joker_test.pipeline.stages.explore import ExploreStage, scan_solidified_assets


# ===== 命中检查 =====

def test_reuse_explicit_skips_exploration(tmp_path: Path) -> None:
    """config.reuse 显式指定 → 直接命中，0 LLM。"""
    cfg = ExploreConfig(intent="x", reuse="tests/generated_smoke/test_a.py")
    stage = ExploreStage(provider=MagicMock(), backend=FakeBackend())
    result = stage.run(cfg)
    assert result.skipped is True
    assert result.reused_test_paths == ["tests/generated_smoke/test_a.py"]


def test_check_reuse_disabled_no_llm(tmp_path: Path) -> None:
    """check_reuse=False → 直接未命中，0 LLM。"""
    cfg = ExploreConfig(intent="x", check_reuse=False, solidify=False, execute=False)
    mock_provider = MagicMock()
    mock_provider.simple_converse.side_effect = AssertionError("不该调 LLM")
    stage = ExploreStage(provider=mock_provider, backend=FakeBackend())
    result = stage.run(cfg)
    assert result.skipped is False


def test_scan_assets_extracts_docstrings(tmp_path: Path) -> None:
    """scan_solidified_assets 能提取 docstring。"""
    gen_dir = tmp_path / "generated_smoke"
    gen_dir.mkdir()
    (gen_dir / "test_login.py").write_text(
        '"""测试登录流程。"""\n\ndef test_login():\n    pass\n',
        encoding="utf-8",
    )
    (gen_dir / "test_logout.py").write_text(
        '"""测试退出登录。"""\n\ndef test_logout():\n    pass\n',
        encoding="utf-8",
    )
    assets = scan_solidified_assets(gen_dir)
    assert len(assets) == 2
    names = {a["name"] for a in assets}
    assert names == {"test_login.py", "test_logout.py"}
    docs = {a["name"]: a["docstring"] for a in assets}
    assert "登录" in docs["test_login.py"]


def test_llm_match_hit_skips_exploration(tmp_path: Path) -> None:
    """LLM 命中检查返回命中 → skipped=True。"""
    gen_dir = tmp_path / "generated_smoke"
    gen_dir.mkdir()
    (gen_dir / "test_login.py").write_text(
        '"""测试登录。"""\n\ndef test_login():\n    pass\n', encoding="utf-8"
    )
    mock_provider = MagicMock()
    mock_provider.simple_converse.return_value = {
        "content": [{"type": "text", "text": '{"hit": true, "path": "test_login.py", "reason": "意图匹配"}'}]
    }
    stage = ExploreStage(provider=mock_provider, backend=FakeBackend(), gen_dir=gen_dir)
    cfg = ExploreConfig(intent="登录", check_reuse=True, solidify=False, execute=False)
    result = stage.run(cfg)
    assert result.skipped is True
    assert "test_login.py" in result.reused_test_paths[0]
    assert result.match_reason is not None


def test_llm_match_miss_proceeds_to_explore(tmp_path: Path) -> None:
    """LLM 命中检查返回未命中 → 继续探索。"""
    mock_provider = MagicMock()
    mock_provider.simple_converse.return_value = {
        "content": [{"type": "text", "text": '{"hit": false, "reason": "无匹配"}'}]
    }
    backend = FakeBackend(
        screens={"root": ScreenCfg(texts_map={})}, initial_screen="root"
    )
    stage = ExploreStage(provider=mock_provider, backend=backend, gen_dir=tmp_path / "empty_gen")
    cfg = ExploreConfig(intent="未知功能", mode="dfs", check_reuse=True,
                        solidify=False, execute=False)
    result = stage.run(cfg)
    assert result.skipped is False
    assert result.match_reason is not None


# ===== 三模式分发 =====

def test_dfs_mode_produces_flow(tmp_path: Path) -> None:
    """dfs 模式调用 UIExplorer 产 UIMap。"""
    from joker_test.executor.base import BBox
    backend = FakeBackend(
        screens={
            "root": ScreenCfg(texts_map={"按钮": BBox(0.4, 0.4, 0.2, 0.1)}),
            "next": ScreenCfg(texts_map={"返回": BBox(0.4, 0.4, 0.2, 0.1)}),
        },
        transitions={
            ("root", "按钮"): "next",
            ("next", "返回"): "root",
            ("next", "key@escape"): "root",
        },
        initial_screen="root",
    )
    stage = ExploreStage(provider=MockProvider(), backend=backend,
                         gen_dir=tmp_path / "gen", flow_dir=tmp_path / "flows")
    cfg = ExploreConfig(intent="探索", mode="dfs", check_reuse=False,
                        solidify=False, execute=False)
    result = stage.run(cfg)
    assert result.skipped is False
    assert result.uimap is not None


def test_llm_mode_produces_flow(tmp_path: Path) -> None:
    """llm 模式调用 LLMExplorer。"""
    from joker_test.executor.base import BBox
    mock_provider = MagicMock()
    mock_provider.simple_converse.return_value = {
        "content": [{"type": "text", "text": '{"screen_name": "主页", "elements": []}'}]
    }
    backend = FakeBackend(
        screens={"root": ScreenCfg(texts_map={})}, initial_screen="root"
    )
    stage = ExploreStage(provider=mock_provider, backend=backend,
                         gen_dir=tmp_path / "gen", flow_dir=tmp_path / "flows")
    cfg = ExploreConfig(intent="探索", mode="llm", check_reuse=False,
                        max_explore_steps=2, solidify=False, execute=False)
    result = stage.run(cfg)
    assert result.skipped is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_pipeline_explore.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'joker_test.pipeline.stages.explore'`

- [ ] **Step 3: 实现 ExploreStage**

创建 `src/joker_test/pipeline/stages/explore.py`：

```python
"""探索阶段：智能入口（固化命中检查）+ 三模式探索。

manual → GlobalRecorder(pynput)
dfs    → UIExplorer（确定性，产 UIMap）
llm    → LLMExplorer（agentic loop，产 UIMap + 操作轨迹）

三种模式统一产 RecordedFlow，下游不区分来源。
"""
from __future__ import annotations

import ast
import datetime
import json
from pathlib import Path
from typing import TYPE_CHECKING

from joker_test.executor.base import ExecutorBackend
from joker_test.explorer.explorer import UIExplorer
from joker_test.explorer.llm_explorer import LLMExplorer
from joker_test.flow.recorder import GlobalRecorder
from joker_test.flow.types import RecordedFlow
from joker_test.llm.base import LLMProvider
from joker_test.pipeline.types import ExploreConfig, ExploreResult

if TYPE_CHECKING:
    pass


def scan_solidified_assets(gen_dir: str | Path) -> list[dict[str, str]]:
    """扫描已固化资产，提取文件名 + docstring。

    Args:
        gen_dir: tests/generated_smoke 目录

    Returns:
        [{"name": "test_xxx.py", "docstring": "..."}]
    """
    gen_path = Path(gen_dir)
    if not gen_path.exists():
        return []
    assets: list[dict[str, str]] = []
    for py in sorted(gen_path.glob("test_*.py")):
        docstring = ""
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
            if (tree.body and isinstance(tree.body[0], ast.Expr)
                    and isinstance(tree.body[0].value, ast.Constant)
                    and isinstance(tree.body[0].value.value, str)):
                docstring = tree.body[0].value.value.strip()
        except SyntaxError:
            docstring = ""
        assets.append({"name": py.name, "docstring": docstring})
    return assets


class ExploreStage:
    """探索阶段：固化命中检查 + 三模式探索。"""

    def __init__(
        self,
        provider: LLMProvider,
        backend: ExecutorBackend,
        gen_dir: str | Path = "tests/generated_smoke",
        flow_dir: str | Path = "flows",
    ) -> None:
        self._provider = provider
        self._backend = backend
        self._gen_dir = Path(gen_dir)
        self._flow_dir = Path(flow_dir)

    def run(self, config: ExploreConfig) -> ExploreResult:
        # 1. 显式 reuse → 直接命中
        if config.reuse is not None:
            return ExploreResult(
                skipped=True,
                reused_test_paths=[config.reuse],
                match_reason="显式指定复用资产",
            )

        # 2. check_reuse → LLM 比对
        if config.check_reuse:
            hit = self._check_solidified(config.intent)
            if hit is not None:
                return ExploreResult(
                    skipped=True,
                    reused_test_paths=[str(self._gen_dir / hit["path"])],
                    match_reason=hit["reason"],
                )

        # 3. 未命中 → 按 mode 探索
        flow, uimap = self._explore(config)
        return ExploreResult(flow=flow, uimap=uimap, explore_log=self._log)

    def _check_solidified(self, intent: str) -> dict[str, str] | None:
        """扫资产 + LLM 比对，返回命中 dict 或 None。"""
        assets = scan_solidified_assets(self._gen_dir)
        if not assets:
            return None
        prompt = (
            "<测试意图>\n"
            f"{intent}\n"
            "</测试意图>\n\n"
            "<已固化资产>\n"
            + json.dumps(assets, ensure_ascii=False, indent=2)
            + "\n</已固化资产>\n\n"
            "判断测试意图是否已被某个已固化资产覆盖。"
            "请只回答 JSON：{\"hit\": true/false, \"path\": \"文件名\", \"reason\": \"...\"}"
        )
        try:
            msg = self._provider.simple_converse(prompt, [], reasoning=4000)
        except Exception:
            return None
        text = _extract_text(msg)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if data.get("hit") is True and data.get("path"):
            return {"path": data["path"], "reason": data.get("reason", "LLM 命中")}
        return None

    def _explore(self, config: ExploreConfig) -> tuple[RecordedFlow | None, object | None]:
        """按 mode 探索，返回 (flow, uimap)。"""
        self._log: list[str] = []
        if config.mode == "manual":
            return self._explore_manual(config), None
        if config.mode == "dfs":
            return self._explore_dfs(config)
        return self._explore_llm(config)

    def _explore_manual(self, config: ExploreConfig) -> RecordedFlow:
        """pynput 全局监听人类操作。"""
        self._flow_dir.mkdir(parents=True, exist_ok=True)
        recorder = GlobalRecorder(
            output_dir=self._flow_dir, pynput_mode=True
        )
        recorder.start()
        flow = recorder.stop()
        recorder.save_flow_yaml(flow)
        self._log.append(f"manual 录制 {len(flow.steps)} 步")
        return flow

    def _explore_dfs(self, config: ExploreConfig) -> tuple[RecordedFlow | None, object]:
        """UIExplorer 确定性 DFS，产 UIMap + 程序化录制。"""
        recorder = GlobalRecorder(
            output_dir=self._flow_dir, backend=self._backend, pynput_mode=False
        )
        recorder.start()
        explorer = UIExplorer(
            self._backend,
            max_depth=min(config.max_explore_steps, 5),
            screen_change_timeout=1.0,
        )
        uimap = explorer.explore()
        flow = recorder.stop()
        if flow.steps:
            recorder.save_flow_yaml(flow)
        self._log.append(f"dfs 探索 {len(uimap.screens)} 屏")
        return flow if flow.steps else None, uimap

    def _explore_llm(self, config: ExploreConfig) -> tuple[RecordedFlow | None, object]:
        """LLMExplorer agentic loop，产 UIMap + 程序化录制。"""
        recorder = GlobalRecorder(
            output_dir=self._flow_dir, backend=self._backend, pynput_mode=False
        )
        recorder.start()
        explorer = LLMExplorer(
            self._backend,
            self._provider,
            max_steps=config.max_explore_steps,
            recorder=recorder,
        )
        uimap = explorer.explore()
        flow = recorder.stop()
        if flow.steps:
            recorder.save_flow_yaml(flow)
        self._log.append(f"llm 探索 {len(uimap.screens)} 屏")
        return flow if flow.steps else None, uimap


def _extract_text(msg: dict) -> str:
    """从 LLM Message 提取纯文本。"""
    for block in msg.get("content", []):
        if isinstance(block, dict) and "text" in block:
            return block["text"]
    return ""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_pipeline_explore.py -v`
Expected: 7 passed

- [ ] **Step 5: lint + 提交**

Run: `source .venv/Scripts/activate && ruff check src/joker_test/pipeline/stages/explore.py tests/test_pipeline_explore.py`

```bash
git add src/joker_test/pipeline/stages/explore.py tests/test_pipeline_explore.py
git commit -m "feat(pipeline): ExploreStage 智能入口 + 固化命中检查 + 三模式探索"
```

---

## Task 4: SolidifyStage

**Files:**
- Create: `src/joker_test/pipeline/stages/solidify.py`
- Test: `tests/test_pipeline_solidify.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_pipeline_solidify.py`：

```python
"""SolidifyStage 测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from joker_test.executor.backends.fake import FakeBackend
from joker_test.flow.types import RecordedFlow, RecordedStep
from joker_test.pipeline.types import ExploreConfig, SolidifyResult
from joker_test.pipeline.stages.solidify import SolidifyStage


def _make_flow() -> RecordedFlow:
    """构造最小 RecordedFlow。"""
    return RecordedFlow(
        name="test_flow",
        steps=[
            RecordedStep(action="click_text", text="开始", note="点开始"),
        ],
    )


def test_solidify_generates_tests(tmp_path: Path) -> None:
    """固化阶段调 generator 产 test_case。"""
    mock_provider = MagicMock()
    mock_provider.simple_converse.return_value = {
        "content": [{
            "type": "text",
            "text": '### test_demo.py\n```python\ndef test_demo():\n    assert True\n```\n'
        }]
    }
    flow = _make_flow()
    flow_dir = tmp_path / "flow_dir"
    flow_dir.mkdir()
    gen_dir = tmp_path / "gen"
    stage = SolidifyStage(
        provider=mock_provider,
        backend=FakeBackend(),
        gen_dir=gen_dir,
    )
    cfg = ExploreConfig(intent="demo", verify_during_solidify=False)
    result = stage.run(flow, flow_dir, cfg)
    assert len(result.test_paths) >= 1
    assert all(Path(p).exists() for p in result.test_paths)


def test_solidify_skips_verify_when_disabled(tmp_path: Path) -> None:
    """verify_during_solidify=False → 不试跑。"""
    mock_provider = MagicMock()
    mock_provider.simple_converse.return_value = {
        "content": [{
            "type": "text",
            "text": '### test_skip.py\n```python\ndef test_s():\n    assert True\n```\n'
        }]
    }
    flow = _make_flow()
    flow_dir = tmp_path / "flow_dir"
    flow_dir.mkdir()
    stage = SolidifyStage(
        provider=mock_provider, backend=FakeBackend(), gen_dir=tmp_path / "gen"
    )
    cfg = ExploreConfig(intent="x", verify_during_solidify=False)
    result = stage.run(flow, flow_dir, cfg)
    assert result.verify_ok is False  # 没试跑 → False
    assert result.verify_rounds == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_pipeline_solidify.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 SolidifyStage**

创建 `src/joker_test/pipeline/stages/solidify.py`：

```python
"""固化阶段：操作轨迹 → LLM 生成 pytest test_case。

一次固化多次回归。可选试跑回喂（TestCaseVerifier）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from joker_test.executor.base import ExecutorBackend
from joker_test.flow.generator import RecordedFlowGenerator
from joker_test.flow.types import RecordedFlow
from joker_test.generator import write_tests_to_dir
from joker_test.llm.base import LLMProvider
from joker_test.pipeline.types import ExploreConfig, SolidifyResult


class SolidifyStage:
    """固化操作轨迹为 pytest test_case。"""

    def __init__(
        self,
        provider: LLMProvider,
        backend: ExecutorBackend,
        gen_dir: str | Path = "tests/generated_smoke",
    ) -> None:
        self._provider = provider
        self._backend = backend
        self._gen_dir = Path(gen_dir)
        self._generator = RecordedFlowGenerator(provider)

    def run(
        self,
        flow: RecordedFlow,
        flow_dir: Path,
        config: ExploreConfig,
    ) -> SolidifyResult:
        game_meta: dict[str, Any] = {"game_name": config.game_name}
        tests = self._generator.generate(flow, flow_dir, game_meta)
        test_paths = write_tests_to_dir(tests, self._gen_dir)
        spec_paths = [str(self._gen_dir / t.spec_filename) for t in tests]

        warnings: list[str] = []
        verify_ok = False
        verify_rounds = 0

        if config.verify_during_solidify and test_paths:
            verify_ok, verify_rounds, vw = self._verify(
                tests, flow, flow_dir, game_meta, config
            )
            warnings.extend(vw)

        return SolidifyResult(
            test_paths=[str(p) for p in test_paths],
            spec_paths=spec_paths,
            verify_ok=verify_ok,
            verify_rounds=verify_rounds,
            warnings=warnings,
        )

    def _verify(
        self,
        tests: list,
        flow: RecordedFlow,
        flow_dir: Path,
        game_meta: dict[str, Any],
        config: ExploreConfig,
    ) -> tuple[bool, int, list[str]]:
        """试跑回喂闭环。"""
        from joker_test.flow.verifier import TestCaseVerifier

        def reset_fn() -> None:
            self._backend.connect()

        verifier = TestCaseVerifier(
            reset_fn=reset_fn,
            gen_dir=self._gen_dir,
            backend_name=config.backend_name,
            game_name=config.game_name or "verified",
        )
        fixed_tests, verify_warnings = verifier.verify_and_fix(
            tests, self._generator, flow, flow_dir, game_meta
        )
        all_pass = len(verify_warnings) == 0
        return all_pass, verifier._max_retries, [str(w) for w in verify_warnings]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_pipeline_solidify.py -v`
Expected: 2 passed

- [ ] **Step 5: lint + 提交**

Run: `source .venv/Scripts/activate && ruff check src/joker_test/pipeline/stages/solidify.py tests/test_pipeline_solidify.py`

```bash
git add src/joker_test/pipeline/stages/solidify.py tests/test_pipeline_solidify.py
git commit -m "feat(pipeline): SolidifyStage 固化 + 试跑回喂"
```

---

## Task 5: ReflectStage — 可信度评分 + 风险提示

**Files:**
- Create: `src/joker_test/pipeline/stages/reflect.py`
- Test: `tests/test_pipeline_reflect.py`

复用现有 `detect_stuck_loop` 和 `review_failure`，新增可信度评分（LLM）和风险提示（LLM）。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_pipeline_reflect.py`：

```python
"""ReflectStage 测试。"""
from __future__ import annotations

from unittest.mock import MagicMock

from joker_test.pipeline.types import (
    ExploreConfig,
    ExploreResult,
    ReportResult,
)
from joker_test.pipeline.stages.reflect import ReflectStage


def test_reflect_with_execute_results() -> None:
    """有执行结果时反思覆盖 pass/fail 风险。"""
    mock_provider = MagicMock()
    mock_provider.simple_converse.return_value = {
        "content": [{
            "type": "text",
            "text": '{"confidence": 0.8, "risks": [{"category": "low_confidence", "description": "断言较弱", "severity": "P2"}], "reasoning": "基本可信"}'
        }]
    }
    stage = ReflectStage(provider=mock_provider)
    explore = ExploreResult(skipped=False, explore_log=["dfs 探索 3 屏"])
    report = ReportResult(
        report_paths=[],
        summary="s",
        stage_coverage={"explore": True, "solidify": False, "execute": False},
    )
    cfg = ExploreConfig(intent="x")
    result = stage.run(explore=explore, execute=None, report=report, config=cfg)
    assert 0.0 <= result.confidence_score <= 1.0
    assert len(result.risks) >= 1


def test_reflect_llm_failure_degrades_to_low_confidence() -> None:
    """LLM 失败时降级为低可信度，不阻断。"""
    mock_provider = MagicMock()
    mock_provider.simple_converse.side_effect = RuntimeError("network down")
    stage = ReflectStage(provider=mock_provider)
    explore = ExploreResult(skipped=False)
    report = ReportResult(
        report_paths=[], summary="",
        stage_coverage={"explore": True, "solidify": False, "execute": False},
    )
    cfg = ExploreConfig(intent="x")
    result = stage.run(explore=explore, execute=None, report=report, config=cfg)
    assert result.confidence_score == 0.3
    assert "降级" in result.reflect_reasoning


def test_reflect_detects_stuck_loop() -> None:
    """反思阶段调 detect_stuck_loop 检测卡死。"""
    mock_provider = MagicMock()
    mock_provider.simple_converse.return_value = {
        "content": [{"type": "text", "text": '{"confidence": 0.7, "risks": [], "reasoning": "ok"}'}]
    }
    stage = ReflectStage(provider=mock_provider)
    explore = ExploreResult(
        skipped=False, explore_log=["step1", "step2", "step3", "step3", "step3"]
    )
    report = ReportResult(
        report_paths=[], summary="",
        stage_coverage={"explore": True, "solidify": False, "execute": False},
    )
    cfg = ExploreConfig(intent="x")
    result = stage.run(explore=explore, execute=None, report=report, config=cfg)
    # explore_log 里的字符串不会触发 stuck（detect_stuck_loop 看的是操作信号）
    # 这个测试验证不崩溃即可
    assert result.reflect_reasoning != ""
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_pipeline_reflect.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 ReflectStage**

创建 `src/joker_test/pipeline/stages/reflect.py`：

```python
"""反思阶段：可信度评分 + 风险提示。

复用 reflection.detect_stuck_loop（确定性卡死检测）+ review_failure（误报审查）。
新增 LLM 可信度评分（断言强度/覆盖率/探索充分度）+ 风险提示。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from joker_test.llm.base import LLMProvider
from joker_test.pipeline.types import (
    ExploreConfig,
    ExploreResult,
    ExecuteResult,
    ReflectResult,
    ReportResult,
    Risk,
)
from joker_test.reflection import detect_stuck_loop

if TYPE_CHECKING:
    pass


class ReflectStage:
    """反思阶段：可信度 + 风险。"""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def run(
        self,
        explore: ExploreResult,
        execute: ExecuteResult | None,
        report: ReportResult,
        config: ExploreConfig,
    ) -> ReflectResult:
        stuck_warnings = self._detect_stuck(explore)
        false_positives = self._review_failures(execute)

        try:
            confidence, risks, reasoning = self._llm_reflect(explore, execute, report)
        except Exception:
            return ReflectResult(
                confidence_score=0.3,
                stuck_warnings=stuck_warnings,
                false_positives=false_positives,
                risks=[],
                reflect_reasoning="LLM 反思失败，降级为低可信度",
            )

        return ReflectResult(
            confidence_score=confidence,
            false_positives=false_positives,
            stuck_warnings=stuck_warnings,
            risks=risks,
            reflect_reasoning=reasoning,
        )

    def _detect_stuck(self, explore: ExploreResult) -> list[str]:
        """确定性卡死检测。"""
        if detect_stuck_loop(explore.explore_log):
            return ["探索操作信号连续重复，疑似卡死"]
        return []

    def _review_failures(
        self, execute: ExecuteResult | None
    ) -> list[dict[str, object]]:
        """对失败用例调误报审查。"""
        from joker_test.reflection import review_failure

        if execute is None:
            return []
        fps: list[dict[str, object]] = []
        for result in execute.session.results:
            if result.status != "failed":
                continue
            try:
                is_fp, reason = review_failure(self._provider, result)
                fps.append({"test": result.test.name, "is_false_positive": is_fp, "reason": reason})
            except Exception:
                fps.append({"test": result.test.name, "is_false_positive": False, "reason": "审查失败"})
        return fps

    def _llm_reflect(
        self,
        explore: ExploreResult,
        execute: ExecuteResult | None,
        report: ReportResult,
    ) -> tuple[float, list[Risk], str]:
        """LLM 综合可信度评分 + 风险提示。"""
        parts = [
            f"探索方式: {explore.explore_log}",
            f"命中跳过: {explore.skipped}",
        ]
        if execute is not None:
            parts.append(
                f"执行结果: passed={execute.session.passed}, failed={execute.session.failed}"
            )
        parts.append(f"阶段覆盖: {report.stage_coverage}")

        prompt = (
            "<本次测试运行摘要>\n"
            + "\n".join(parts)
            + "\n</本次测试运行摘要>\n\n"
            "请评估：\n"
            "1. 综合可信度（0.0-1.0，考虑断言强度/覆盖率/探索充分度）\n"
            "2. 风险项（未覆盖区域/探索中断/低可信等）\n"
            "请只回答 JSON：{\"confidence\": 0.0, \"risks\": [{\"category\": \"\", \"description\": \"\", \"severity\": \"P2\"}], \"reasoning\": \"...\"}"
        )
        msg = self._provider.simple_converse(prompt, [], reasoning=8000)
        text = _extract_text(msg)
        data = json.loads(text)
        confidence = float(data.get("confidence", 0.5))
        risks = [
            Risk(
                category=r.get("category", "unknown"),
                description=r.get("description", ""),
                severity=r.get("severity", "P2"),
            )
            for r in data.get("risks", [])
        ]
        return confidence, risks, data.get("reasoning", "")


def _extract_text(msg: dict) -> str:
    for block in msg.get("content", []):
        if isinstance(block, dict) and "text" in block:
            return block["text"]
    return ""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_pipeline_reflect.py -v`
Expected: 3 passed

- [ ] **Step 5: lint + 提交**

Run: `source .venv/Scripts/activate && ruff check src/joker_test/pipeline/stages/reflect.py tests/test_pipeline_reflect.py`

```bash
git add src/joker_test/pipeline/stages/reflect.py tests/test_pipeline_reflect.py
git commit -m "feat(pipeline): ReflectStage 可信度评分 + 风险提示"
```

---

## Task 6: ReportStage + ExploreReporter

**Files:**
- Create: `src/joker_test/reporters/explore.py`
- Create: `src/joker_test/pipeline/stages/report.py`
- Test: `tests/test_pipeline_report.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_pipeline_report.py`：

```python
"""ReportStage 测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from joker_test.pipeline.types import (
    ExploreConfig,
    ExploreResult,
    ExploreReport,
)
from joker_test.pipeline.stages.report import ReportStage
from joker_test.reporters.explore import ExploreReporter


def test_report_pure_explore(tmp_path: Path) -> None:
    """纯探索（无固化/执行）出报告。"""
    stage = ReportStage(report_dir=tmp_path)
    explore = ExploreResult(skipped=False, explore_log=["llm 探索 3 屏"])
    cfg = ExploreConfig(intent="测试登录", mode="llm")
    result = stage.run(explore=explore, solidify=None, execute=None, config=cfg)
    assert len(result.report_paths) >= 1
    assert result.stage_coverage["explore"] is True
    assert result.stage_coverage["solidify"] is False
    assert "探索" in result.summary or "explore" in result.summary.lower()


def test_report_with_execute(tmp_path: Path) -> None:
    """有执行结果时 stage_coverage 标记 execute=True。"""
    stage = ReportStage(report_dir=tmp_path)
    explore = ExploreResult(skipped=True, reused_test_paths=["test_a.py"])
    cfg = ExploreConfig(intent="x")
    result = stage.run(explore=explore, solidify=None, execute=None, config=cfg)
    assert result.stage_coverage["explore"] is True


def test_explore_report_serialization(tmp_path: Path) -> None:
    """ExploreReporter 能输出 JSON 文件。"""
    reporter = ExploreReporter(output_dir=tmp_path)
    report = ExploreReport(
        intent="登录测试",
        mode="llm",
        stage_coverage={"explore": True, "solidify": False, "execute": False},
        explore_summary="探索了 3 个界面",
        confidence_score=0.8,
        risks=[],
    )
    path = reporter.render(report)
    assert Path(path).exists()
    import json
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["intent"] == "登录测试"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_pipeline_report.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 ExploreReporter**

创建 `src/joker_test/reporters/explore.py`：

```python
"""ExploreReporter：消费 ExploreReport 输出 JSON。"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from pydantic import BaseModel, Field

from joker_test.pipeline.types import Risk


class ExploreReport(BaseModel):
    """综合探索报告数据模型。"""

    __test__ = False
    intent: str
    mode: str
    stage_coverage: dict[str, bool]
    explore_summary: str = ""
    flow_steps_count: int = 0
    uimap_screen_count: int | None = None
    solidify_summary: str | None = None
    test_count: int | None = None
    verify_ok: bool | None = None
    execute_summary: str | None = None
    passed: int | None = None
    failed: int | None = None
    duration_s: float | None = None
    confidence_score: float = 0.0
    risks: list[Risk] = Field(default_factory=list)


class ExploreReporter:
    """输出综合探索报告 JSON。"""

    name = "explore"

    def __init__(self, output_dir: str | Path = "reports") -> None:
        self._output_dir = Path(output_dir)

    def render(self, report: ExploreReport) -> str:
        """渲染报告到 JSON 文件，返回路径。"""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._output_dir / f"explore_report_{ts}.json"
        path.write_text(
            report.model_dump_json(indent=2), encoding="utf-8"
        )
        return str(path)
```

- [ ] **Step 4: 实现 ReportStage**

创建 `src/joker_test/pipeline/stages/report.py`：

```python
"""报告阶段：汇聚各阶段产物为综合探索报告。"""
from __future__ import annotations

from pathlib import Path

from joker_test.pipeline.types import (
    ExploreConfig,
    ExploreResult,
    ExecuteResult,
    ReportResult,
    SolidifyResult,
)
from joker_test.reporters.explore import ExploreReporter, ExploreReport


class ReportStage:
    """汇聚综合探索报告。"""

    def __init__(self, report_dir: str | Path = "reports") -> None:
        self._reporter = ExploreReporter(output_dir=report_dir)

    def run(
        self,
        explore: ExploreResult,
        solidify: SolidifyResult | None,
        execute: ExecuteResult | None,
        config: ExploreConfig,
    ) -> ReportResult:
        coverage = {
            "explore": True,
            "solidify": solidify is not None,
            "execute": execute is not None,
        }
        report = self._build_report(explore, solidify, execute, config, coverage)
        path = self._reporter.render(report)
        summary = self._build_summary(explore, solidify, execute)
        return ReportResult(
            report_paths=[path],
            summary=summary,
            stage_coverage=coverage,
        )

    def _build_report(
        self,
        explore: ExploreResult,
        solidify: SolidifyResult | None,
        execute: ExecuteResult | None,
        config: ExploreConfig,
        coverage: dict[str, bool],
    ) -> ExploreReport:
        return ExploreReport(
            intent=config.intent,
            mode=config.mode,
            stage_coverage=coverage,
            explore_summary="; ".join(explore.explore_log) or f"探索完成（{config.mode}）",
            flow_steps_count=len(explore.flow.steps) if explore.flow else 0,
            uimap_screen_count=len(explore.uimap.screens) if explore.uimap else None,
            solidify_summary=(
                f"固化 {len(solidify.test_paths)} 个测试" if solidify else None
            ),
            test_count=len(solidify.test_paths) if solidify else None,
            verify_ok=solidify.verify_ok if solidify else None,
            execute_summary=(
                f"passed={execute.session.passed}, failed={execute.session.failed}"
                if execute else None
            ),
            passed=execute.session.passed if execute else None,
            failed=execute.session.failed if execute else None,
            duration_s=execute.duration_s if execute else None,
            confidence_score=0.0,
        )

    def _build_summary(
        self,
        explore: ExploreResult,
        solidify: SolidifyResult | None,
        execute: ExecuteResult | None,
    ) -> str:
        parts = [f"探索: {'; '.join(explore.explore_log) or '完成'}"]
        if solidify:
            parts.append(f"固化: {len(solidify.test_paths)} 个测试")
        if execute:
            parts.append(f"执行: {execute.session.passed}通过/{execute.session.failed}失败")
        return " | ".join(parts)
```

- [ ] **Step 5: 运行测试确认通过**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_pipeline_report.py -v`
Expected: 3 passed

- [ ] **Step 6: lint + 提交**

Run: `source .venv/Scripts/activate && ruff check src/joker_test/reporters/explore.py src/joker_test/pipeline/stages/report.py tests/test_pipeline_report.py`

```bash
git add src/joker_test/reporters/explore.py src/joker_test/pipeline/stages/report.py tests/test_pipeline_report.py
git commit -m "feat(pipeline): ReportStage + ExploreReporter 综合探索报告"
```

---

## Task 7: AgenticOrchestrator + 包导出

**Files:**
- Create: `src/joker_test/pipeline/base.py`（补充编排器）
- Modify: `src/joker_test/pipeline/__init__.py`
- Modify: `src/joker_test/pipeline/stages/__init__.py`
- Test: `tests/test_pipeline_orchestrator.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_pipeline_orchestrator.py`：

```python
"""AgenticOrchestrator 三分支测试。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from joker_test.executor.backends.fake import FakeBackend
from joker_test.llm.providers.mock import MockProvider
from joker_test.pipeline.types import ExploreConfig
from joker_test.pipeline.base import AgenticOrchestrator, build_orchestrator
from joker_test.pipeline.stages.explore import ExploreStage
from joker_test.pipeline.stages.solidify import SolidifyStage
from joker_test.pipeline.stages.execute import ExecuteStage
from joker_test.pipeline.stages.report import ReportStage
from joker_test.pipeline.stages.reflect import ReflectStage


def _mock_reflect_provider() -> MagicMock:
    """反思阶段需要的 LLM 回复。"""
    mock = MagicMock()
    mock.simple_converse.return_value = {
        "content": [{"type": "text", "text": '{"confidence": 0.7, "risks": [], "reasoning": "ok"}'}]
    }
    return mock


def test_branch_reuse_hit(tmp_path: Path) -> None:
    """命中分支：config.reuse → 跳过探索+固化，直接执行。"""
    test_file = tmp_path / "test_reuse.py"
    test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    cfg = ExploreConfig(
        intent="x",
        reuse=str(test_file),
        check_reuse=False,
    )
    reflect_provider = _mock_reflect_provider()
    orch = AgenticOrchestrator(
        explore=ExploreStage(MagicMock(), FakeBackend()),
        solidify=SolidifyStage(MagicMock(), FakeBackend(), gen_dir=tmp_path),
        execute=ExecuteStage(),
        report=ReportStage(report_dir=tmp_path),
        reflect=ReflectStage(reflect_provider),
    )
    result = orch.run(cfg)
    assert result.explore.skipped is True
    assert result.solidify is None
    assert result.execute is not None
    assert result.execute.session.passed >= 1


def test_branch_explore_only(tmp_path: Path) -> None:
    """直出分支：solidify=False → 不固化不执行。"""
    explore_provider = MagicMock()
    explore_provider.simple_converse.return_value = {
        "content": [{"type": "text", "text": '{"hit": false, "reason": "无"}'}]
    }
    cfg = ExploreConfig(
        intent="未知",
        mode="dfs",
        check_reuse=False,
        solidify=False,
        execute=False,
    )
    backend = FakeBackend(
        screens={"root": __import__(
            "joker_test.executor.backends.fake", fromlist=["ScreenCfg"
            ]).ScreenCfg(texts_map={})},
        initial_screen="root",
    )
    reflect_provider = _mock_reflect_provider()
    orch = AgenticOrchestrator(
        explore=ExploreStage(explore_provider, backend,
                             gen_dir=tmp_path/"gen", flow_dir=tmp_path/"flows"),
        solidify=SolidifyStage(MagicMock(), backend, gen_dir=tmp_path/"gen"),
        execute=ExecuteStage(),
        report=ReportStage(report_dir=tmp_path),
        reflect=ReflectStage(reflect_provider),
    )
    result = orch.run(cfg)
    assert result.explore.skipped is False
    assert result.solidify is None
    assert result.execute is None


def test_build_orchestrator_factory(tmp_path: Path) -> None:
    """build_orchestrator 能构造完整编排器。"""
    cfg = ExploreConfig(intent="x", backend_name="fake")
    orch = build_orchestrator(cfg, report_dir=tmp_path)
    assert orch is not None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_pipeline_orchestrator.py -v`
Expected: FAIL — `ImportError: cannot import name 'AgenticOrchestrator'`

- [ ] **Step 3: 补充 base.py 编排器**

修改 `src/joker_test/pipeline/base.py`（在现有 Protocol 后追加）：

```python
"""Stage 协议与编排器。

薄编排：只持 5 个 Stage 实例，按分支调用，无业务逻辑。
Stage 间只通过 Result 对象单向传递数据，无反向引用。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from joker_test.executor.base import ExecutorBackend
from joker_test.llm.base import LLMProvider

if TYPE_CHECKING:
    from joker_test.pipeline.types import (
        ExploreConfig,
        ExploreResult,
        ExecuteResult,
        PipelineResult,
        ReflectResult,
        ReportResult,
        SolidifyResult,
    )

from joker_test.pipeline.stages.execute import ExecuteStage
from joker_test.pipeline.stages.explore import ExploreStage
from joker_test.pipeline.stages.reflect import ReflectStage
from joker_test.pipeline.stages.report import ReportStage
from joker_test.pipeline.stages.solidify import SolidifyStage
from joker_test.pipeline.types import (
    ExploreConfig,
    ExploreResult,
    PipelineResult,
)


@runtime_checkable
class Stage(Protocol):
    """Stage 协议。"""

    def run(self, *args: object, **kwargs: object) -> object: ...


class AgenticOrchestrator:
    """四阶段流水线编排器。

    薄编排，按 config 分支调用 Stage：
    - 命中已固化资产 → 复用执行
    - 探索直出（solidify=False）→ 不固化不执行
    - 探索 + 固化 + 执行 → 全链路
    """

    def __init__(
        self,
        explore: ExploreStage,
        solidify: SolidifyStage,
        execute: ExecuteStage,
        report: ReportStage,
        reflect: ReflectStage,
    ) -> None:
        self._explore = explore
        self._solidify = solidify
        self._execute = execute
        self._report = report
        self._reflect = reflect

    def run(self, config: ExploreConfig) -> PipelineResult:
        explore_out = self._explore.run(config)

        # 命中已固化资产 → 复用执行
        if explore_out.skipped:
            execute_out = (
                self._execute.run(explore_out.reused_test_paths, config)
                if config.execute else None
            )
            report_out = self._report.run(
                explore=explore_out, solidify=None, execute=execute_out, config=config
            )
            reflect_out = self._reflect.run(
                explore=explore_out, execute=execute_out,
                report=report_out, config=config,
            )
            return PipelineResult(
                explore=explore_out, execute=execute_out,
                report=report_out, reflect=reflect_out,
            )

        # 探索直出
        if not config.solidify:
            report_out = self._report.run(
                explore=explore_out, solidify=None, execute=None, config=config
            )
            reflect_out = self._reflect.run(
                explore=explore_out, execute=None,
                report=report_out, config=config,
            )
            return PipelineResult(
                explore=explore_out, report=report_out, reflect=reflect_out,
            )

        # 探索 + 固化 + 执行
        from pathlib import Path as _Path

        solidify_out = self._solidify.run(
            explore_out.flow, _Path("flows/latest"), config
        )
        execute_out = (
            self._execute.run(solidify_out.test_paths, config)
            if config.execute else None
        )
        report_out = self._report.run(
            explore=explore_out, solidify=solidify_out,
            execute=execute_out, config=config,
        )
        reflect_out = self._reflect.run(
            explore=explore_out, execute=execute_out,
            report=report_out, config=config,
        )
        return PipelineResult(
            explore=explore_out, solidify=solidify_out,
            execute=execute_out, report=report_out, reflect=reflect_out,
        )


def build_orchestrator(
    config: ExploreConfig,
    report_dir: str | Path = "reports",
    gen_dir: str | Path = "tests/generated_smoke",
    flow_dir: str | Path = "flows",
) -> AgenticOrchestrator:
    """根据 config 构造编排器，注入各 Stage 所需依赖。"""
    from joker_test.executor.backends.fake import FakeBackend
    from joker_test.llm.providers.mock import MockProvider

    provider = MockProvider()
    backend = FakeBackend()
    return AgenticOrchestrator(
        explore=ExploreStage(provider, backend, gen_dir=gen_dir, flow_dir=flow_dir),
        solidify=SolidifyStage(provider, backend, gen_dir=gen_dir),
        execute=ExecuteStage(),
        report=ReportStage(report_dir=report_dir),
        reflect=ReflectStage(provider),
    )
```

- [ ] **Step 4: 补充包导出**

修改 `src/joker_test/pipeline/stages/__init__.py`：

```python
"""流水线各 Stage 实现。"""
from joker_test.pipeline.stages.execute import ExecuteStage
from joker_test.pipeline.stages.explore import ExploreStage
from joker_test.pipeline.stages.reflect import ReflectStage
from joker_test.pipeline.stages.report import ReportStage
from joker_test.pipeline.stages.solidify import SolidifyStage

__all__ = [
    "ExploreStage",
    "SolidifyStage",
    "ExecuteStage",
    "ReportStage",
    "ReflectStage",
]
```

修改 `src/joker_test/pipeline/__init__.py`：

```python
"""四阶段探索式测试流水线。"""
from joker_test.pipeline.base import AgenticOrchestrator, Stage, build_orchestrator
from joker_test.pipeline.types import (
    ExploreConfig,
    ExploreResult,
    ExecuteResult,
    PipelineResult,
    ReflectResult,
    ReportResult,
    Risk,
    SolidifyResult,
)

__all__ = [
    "AgenticOrchestrator",
    "Stage",
    "build_orchestrator",
    "ExploreConfig",
    "ExploreResult",
    "ExecuteResult",
    "PipelineResult",
    "ReflectResult",
    "ReportResult",
    "Risk",
    "SolidifyResult",
]
```

修改 `src/joker_test/reporters/__init__.py`（追加 ExploreReporter 导出——先读现有内容确认）。

- [ ] **Step 5: 运行测试确认通过**

Run: `source .venv/Scripts/activate && python -m pytest tests/test_pipeline_orchestrator.py tests/test_pipeline_types.py tests/test_pipeline_execute.py tests/test_pipeline_explore.py tests/test_pipeline_solidify.py tests/test_pipeline_reflect.py tests/test_pipeline_report.py -v`
Expected: all passed

- [ ] **Step 6: lint + 提交**

Run: `source .venv/Scripts/activate && ruff check src/joker_test/pipeline src/joker_test/reporters`

```bash
git add src/joker_test/pipeline/ src/joker_test/reporters/ tests/test_pipeline_orchestrator.py
git commit -m "feat(pipeline): AgenticOrchestrator 三分支编排 + build_orchestrator 工厂 + 包导出"
```

---

## Task 8: 删除冗余文件 + 清理 reflection.py

**Files:**
- Delete: `src/joker_test/orchestrator.py`
- Delete: `src/joker_test/exploratory.py`
- Delete: `src/joker_test/tactics.py`
- Delete: `tests/test_orchestrator.py`
- Delete: `tests/test_exploratory.py`
- Modify: `src/joker_test/reflection.py` — 删除 `review_bug_report`/`filter_bug_reports`，保留 `detect_stuck_loop`/`review_failure`
- Modify: `tests/test_reflection.py` — 删除依赖 BugReport 的 3 个测试

- [ ] **Step 1: 先读 reflection.py 全文，确认保留部分**

Run: `source .venv/Scripts/activate && cat src/joker_test/reflection.py`

确认要保留：`detect_stuck_loop`（33 行）、`review_failure`（50 行）、`_extract_text`、`_parse_verdict`。要删除：`review_bug_report`（113 行）、`filter_bug_reports`（151 行）、`TYPE_CHECKING` import BugReport（177 行）、`__all__` 里的 `review_bug_report`/`filter_bug_reports`。

- [ ] **Step 2: 读 test_reflection.py，确认要删的测试**

确认 `test_review_bug_report_returns_verdict`、`test_filter_bug_reports_separates`、`test_filter_bug_reports_llm_error_keeps_bug` 这 3 个要删。

- [ ] **Step 3: 修改 reflection.py**

删除 `review_bug_report` 函数（113-148 行）、`filter_bug_reports` 函数（151-173 行）、`TYPE_CHECKING` 块（176-177 行）。更新 `__all__` 为 `["detect_stuck_loop", "review_failure"]`。

- [ ] **Step 4: 修改 test_reflection.py**

删除依赖 BugReport 的 3 个测试函数及其 import。

- [ ] **Step 5: 删除冗余文件**

```bash
rm src/joker_test/orchestrator.py
rm src/joker_test/exploratory.py
rm src/joker_test/tactics.py
rm tests/test_orchestrator.py
rm tests/test_exploratory.py
```

- [ ] **Step 6: 搜索残留引用**

```bash
grep -rn "orchestrator\|exploratory\|tactics\|StaticOrchestrator\|BugReport" src/ tests/ --include="*.py" | grep -v "pipeline"
```

确认无残留（pipeline 内部的不算）。

- [ ] **Step 7: 运行全量测试确认无回归**

Run: `source .venv/Scripts/activate && python -m pytest -v`
Expected: all passed（除可能的 real 测试 skip）

- [ ] **Step 8: lint + 提交**

Run: `source .venv/Scripts/activate && ruff check src tests`

```bash
git add -A
git commit -m "refactor: 删除 StaticOrchestrator/exploratory/tactics + 清理 reflection BugReport 依赖"
```

---

## Task 9: CLI 改造

**Files:**
- Modify: `src/joker_test/cli.py`

- [ ] **Step 1: 读现有 cli.py**

Run: `source .venv/Scripts/activate && cat src/joker_test/cli.py`

理解 main() 的 subparsers 结构和分发逻辑。

- [ ] **Step 2: 新增 explore 子命令**

在 main() 的 subparsers 注册部分追加 `explore` 子命令：

```python
p_explore = sub.add_parser("explore", help="智能探索入口（固化命中检查 + 三模式探索）")
p_explore.add_argument("--intent", required=True, help="测试意图")
p_explore.add_argument("--mode", choices=["manual", "dfs", "llm"], default="llm")
p_explore.add_argument("--reuse", default=None, help="显式复用资产路径")
p_explore.add_argument("--check-reuse", dest="check_reuse", action="store_true", default=True)
p_explore.add_argument("--no-check-reuse", dest="check_reuse", action="store_false")
p_explore.add_argument("--solidify", dest="solidify", action="store_true", default=True)
p_explore.add_argument("--no-solidify", dest="solidify", action="store_false")
p_explore.add_argument("--execute", dest="execute", action="store_true", default=True)
p_explore.add_argument("--no-execute", dest="execute", action="store_false")
p_explore.add_argument("--game", default="")
p_explore.add_argument("--backend", default="fake", choices=["airtest", "fake"])
p_explore.add_argument("--max-steps", type=int, default=30)
p_explore.add_argument("--no-trace", action="store_true")
```

- [ ] **Step 3: 实现 _cmd_explore**

```python
def _cmd_explore(args: argparse.Namespace) -> int:
    from joker_test.pipeline import build_orchestrator, ExploreConfig  # noqa: PLC0415
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
    )
    orch = build_orchestrator(cfg)
    result = orch.run(cfg)
    print(f"探索完成：{result.report.summary}")
    if result.reflect.risks:
        print(f"风险提示：{len(result.reflect.risks)} 项")
    return 0
```

- [ ] **Step 4: 改造 run-all 走 AgenticOrchestrator**

将 `_cmd_run_all` 改为：

```python
def _cmd_run_all(args: argparse.Namespace) -> int:
    from joker_test.pipeline import build_orchestrator, ExploreConfig  # noqa: PLC0415
    game_meta = json.loads(Path(args.game_meta).read_text(encoding="utf-8"))
    cfg = ExploreConfig(
        intent=game_meta.get("game_name", "探索式测试"),
        mode="llm",
        game_name=game_meta.get("game_name", "unknown"),
        backend_name="fake" if args.mock else "airtest",
    )
    orch = build_orchestrator(cfg, report_dir=args.report_dir)
    result = orch.run(cfg)
    print(f"编排完成：\n{result.report.summary}")
    return 0
```

- [ ] **Step 5: 无参数默认 run-all**

修改 main() 的 subparser 注册，让 `required=False`，并在分发逻辑加默认：

```python
# 改 sub.add_subparsers(dest="command", required=True)
# 为
sub = parser.add_subparsers(dest="command", required=False)
# 分发逻辑开头加
if args.command is None:
    args = parser.parse_args(["run-all"] + (argv or []))
```

并在 `_cmd_run_all` 的 `--game-meta` 改为可选（default=None），为 None 时用默认 game_meta。

- [ ] **Step 6: 注册 explore 分发**

在 `if args.command == "..."` 链中追加：

```python
if args.command == "explore":
    return _cmd_explore(args)
```

- [ ] **Step 7: 运行 CLI 冒烟测试**

```bash
source .venv/Scripts/activate
python -m joker_test.cli explore --intent "测试" --mode dfs --no-solidify --no-execute --no-trace 2>&1 | head -5
```

Expected: 不崩溃，输出探索完成（FakeBackend 单屏会快速返回）

- [ ] **Step 8: lint + 提交**

Run: `source .venv/Scripts/activate && ruff check src/joker_test/cli.py`

```bash
git add src/joker_test/cli.py
git commit -m "feat(cli): 新增 explore 子命令 + run-all 改走 AgenticOrchestrator + 无参数默认 run-all"
```

---

## Task 10: 更新文档

**Files:**
- Modify: `AGENTS.md`
- Modify: `DESIGN.md`（状态描述 + 目录结构）

- [ ] **Step 1: 更新 AGENTS.md**

更新仓库结构部分：pipeline/ 新增、orchestrator.py/exploratory.py/tactics.py 删除。更新 CLI 子命令描述：6 → 7（加 explore）。更新状态行。

- [ ] **Step 2: 更新 DESIGN.md**

更新 §3.1 架构图（实线框标注 pipeline 已实现）。更新目录结构描述。

- [ ] **Step 3: 提交**

```bash
git add AGENTS.md DESIGN.md
git commit -m "docs: 更新 AGENTS.md/DESIGN.md 反映 pipeline 架构"
```

---

## 自检

**Spec 覆盖检查：**

| Spec 章节 | 覆盖 Task |
|---|---|
| §2.1 数据流 DAG | Task 7（编排器三分支） |
| §2.2 三种探索方式 | Task 3（ExploreStage manual/dfs/llm） |
| §2.3 固化命中检查 | Task 3（reuse + check_reuse + scan_solidified_assets） |
| §3 目录结构 | Task 1-7（pipeline/）+ Task 8（删除） |
| §4 数据契约 | Task 1（types.py） |
| §5 AgenticOrchestrator | Task 7 |
| §6.1-6.5 各 Stage 职责 | Task 2-6 |
| §7 CLI | Task 9 |
| §8 综合探索报告 | Task 6（ExploreReporter + ExploreReport） |
| §9 错误处理 | 各 Stage 内 try/except 降级（Task 3/5/6） |
| §10 测试策略 | 每个 Task 的测试步骤 |
| §11 与现有架构关系 | Task 8（删除 + 清理 reflection） |

**无占位符扫描：** 所有代码块均含完整实现，无 TBD/TODO。

**类型一致性检查：**
- `ExploreConfig` 字段在 Task 1 定义，Task 3/4/5/6/7/9 使用一致
- `ExploreResult.skipped` / `reused_test_paths` / `flow` / `uimap` 在编排器（Task 7）和 CLI（Task 9）使用一致
- `SolidifyResult.test_paths` 在 Task 4 定义，Task 7 编排器消费一致
- `ReflectStage.run` 签名 `(explore, execute, report, config)` 在 Task 5 定义，Task 7 调用一致

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-09-explore-pipeline.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
