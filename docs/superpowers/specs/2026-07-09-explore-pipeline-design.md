# 探索式测试流水线设计

> 日期：2026-07-09
> 状态：待实现
> 关联：DESIGN.md §2.1（测试分层）/ §2.2（LLM 克制）/ ADR-006（CLI 统一契约）/ R-ADR-6（编排可插拔）

## 1. 背景与目标

### 1.1 现状

joker-test 已实现零散但完整的探索式能力：`GlobalRecorder`（操作录制）、`LLMExplorer`（LLM 驱动 agentic loop，已在循环内接录制）、`UIExplorer`（确定性 DFS 产 UIMap）、`RecordedFlowGenerator`（录像固化）、`reflection.py`（误报审查）、`reporters/`（Multi 广播）。但这些能力**未被统一编排**：`StaticOrchestrator` 只跑"探索→生成冒烟→报告"半边，`TestCaseVerifier` 试跑闭环未接 CLI，反思未覆盖可信度/风险。

### 1.2 目标

借鉴 Open-AutoGLM 的自主探索循环模式（think-act-observe），**整合**现有能力为统一的四阶段流水线：

1. **探索**：manual 录像 / dfs 确定性 / llm 自主循环，三种方式统一产操作轨迹（RecordedFlow）。入口内置"固化命中检查"，已固化的测试意图直接复用资产，跳过探索+固化。
2. **固化**：操作轨迹 → LLM 生成 pytest test_case（含试跑回喂），一次固化多次回归。脱离 LLM 后可独立执行。
3. **执行**：跑 test_case，纯 pytest，脱离 LLM。可独立调用（复用历史固化资产）。
4. **报告 + 反思**：聚合产物出综合报告；反思覆盖可信度评分 + 风险提示。

### 1.3 设计约束

- **保留 LLM 克制红线**（DESIGN §2.2）：借鉴 Open-AutoGLM 的**循环模式**，但不改为 LLM 全程驱动。LLM 仍在高价值决策点（命中检查/探索决策/固化生成/可信度评分）使用，执行/感知/规则校验一律不用。
- **不新增 ADR**：本设计是对 R-ADR-6（编排可插拔、CLI 统一契约）的落地，不突破现有 ADR。
- **状态自洽、避免网状依赖**：每个 Stage 类自持所需依赖，Stage 间只通过 Result 对象单向传递数据。

## 2. 整体架构

### 2.1 数据流（DAG，非线性）

```
                              ExploreStage（智能入口）
                              ┌──────────────────────────┐
                   所有请求 ─>│ 1. 扫资产 + 判固化命中     │
                              │ 2. 命中 → 短路复用         │
                              │ 3. 未命中 → 探索（3种方式） │
                              └──────┬───────────────────┘
                    ┌───────────────┼──────────────────────────┐
                    ▼               ▼                          ▼
              命中(已固化)    探索 + 固化                探索直出(solidify=False)
              reused_paths    SolidifyStage              （到此为止，进报告）
                    │               │
                    │         ExecuteStage（脱离 LLM）
                    │               │
                    └───────┬───────┘
                            ▼
                      ReportStage（汇聚）
                            │
                            ▼
                     ReflectStage（汇聚）
```

5 个 Stage：Explore（智能入口，必选）/ Solidify（可选）/ Execute（可选）/ Report（必选汇聚）/ Reflect（必选汇聚）。

### 2.2 三种探索方式

ExploreStage 的探索核心支持三种方式，**统一产 RecordedFlow**，下游不区分来源：

| mode | 实现 | 产 UIMap? | 用 LLM? | 适用场景 |
|---|---|---|---|---|
| `manual` | `GlobalRecorder`（pynput 全局监听） | 否 | 否 | 人类演示操作，零 LLM 成本 |
| `dfs` | `UIExplorer`（OCR DFS） | 是 | 否 | 确定性遍历界面，产 UIMap 喂固化/反思 |
| `llm` | `LLMExplorer`（agentic loop） | 是 | 是（每步 1-2 次） | 自主探索，借鉴 Open-AutoGLM 循环模式 |

`dfs` 和 `llm` 额外产 UIMap，挂在 ExploreResult 供反思阶段用（不进固化阶段）。`llm` 模式的循环内已接 `record_action`，探索即录制。

### 2.3 固化命中检查

ExploreStage 入口默认开启命中检查（`config.check_reuse=True`），判定"测试意图是否已固化"：

1. **`config.reuse` 显式非空** → 直接命中，0 LLM，跳过 LLM 命中检查。
2. **`config.reuse` 为空 + `config.check_reuse=True`** → 扫 `tests/generated_smoke/*.py` 提取每个文件的 docstring + 文件名作为资产清单，调 LLM 比对 `config.intent` 与清单 → 命中/未命中（1 LLM）。
3. **`config.reuse` 为空 + `config.check_reuse=False`** → 直接未命中，0 LLM。

命中 → `ExploreResult.skipped=True` + `reused_test_paths`，编排器跳过 Solidify 直接 Execute。资产发现采用**扫目录实时提取**，零额外维护。

## 3. 目录结构

新增 `pipeline/` 包（5 个 Stage 有共同抽象，符合 §11.1"有抽象的包"）。删除 `StaticOrchestrator` / `exploratory.py` / `tactics.py`。保留 `perception/`（录像固化 + executor 公共依赖）和 `explorer/`（两种自动探索方式）。

```
src/joker_test/
├── pipeline/                    # 新增：四阶段流水线
│   ├── __init__.py              # __all__ 导出
│   ├── types.py                 # ExploreConfig + 5个Result + PipelineResult + Risk
│   ├── base.py                  # Stage Protocol + AgenticOrchestrator
│   ├── agentic.py               # AgenticOrchestrator 实现
│   └── stages/
│       ├── __init__.py
│       ├── explore.py           # ExploreStage（智能入口 + 固化命中检查）
│       ├── solidify.py          # SolidifyStage（固化 + 试跑回喂）
│       ├── execute.py           # ExecuteStage（脱离 LLM）
│       ├── report.py            # ReportStage（汇聚综合报告）
│       └── reflect.py           # ReflectStage（可信度 + 风险）
├── explorer/                    # 保留不动：UIExplorer(DFS) + LLMExplorer + types
├── flow/                        # 保留不动：recorder + generator + namer + verifier + semantics
├── perception/                  # 保留不动：ImageMatcher + PerceptionEngine
├── llm/                         # 保留不动：Provider 抽象 + 各实现
├── executor/                    # 保留不动：Backend 抽象 + 各实现
├── reporters/                   # 保留不动：Json/Html/Multi + TestReporter 协议
├── prompts/                     # 保留不动
├── plugins/                     # 保留不动
├── charter_gen.py               # 保留不动（独立议题）
├── reflection.py                # 保留：卡死检测 + 误报审查，ReflectStage 复用
├── runner.py                    # 保留不动：ExecuteStage 复用 run_tests
├── trace.py                     # 保留不动
├── cli.py                       # 改造：新增 explore 命令 + run-all 改走 AgenticOrchestrator
├── orchestrator.py              # 删除（StaticOrchestrator 被 AgenticOrchestrator 取代）
├── exploratory.py               # 删除（M4 简化版战术 bug 查找，本次不整合）
└── tactics.py                   # 删除（战术库，随 exploratory 删除）
```

## 4. 数据契约

所有模型 Pydantic v2，设 `__test__ = False`。字段标注遵循 §11.6。

### 4.1 ExploreConfig

```python
class ExploreConfig(BaseModel):
    __test__ = False
    intent: str                              # 测试意图（命中检查 + 探索方向引导）
    mode: Literal["manual", "dfs", "llm"] = "llm"
    reuse: str | None = None                 # 显式复用资产路径（0 LLM）
    check_reuse: bool = True                 # 是否开启固化命中检查（默认开）
    solidify: bool = True                    # 是否进固化阶段
    execute: bool = True                     # 命中或固化后是否执行
    game_name: str = ""
    backend_name: str = "fake"
    max_explore_steps: int = 30              # dfs/llm 模式步数上限
    verify_during_solidify: bool = True      # 固化阶段是否试跑回喂
```

### 4.2 ExploreResult

```python
class ExploreResult(BaseModel):
    __test__ = False
    flow: RecordedFlow | None = None         # 未命中探索时填，命中时 None
    uimap: UIMap | None = None               # dfs/llm 模式额外产
    skipped: bool = False                    # 命中已固化资产
    reused_test_paths: list[str] = []        # 命中时填
    match_reason: str | None = None          # 命中/未命中理由
    explore_log: list[str] = []              # 探索步骤摘要（供报告/反思）
```

### 4.3 SolidifyResult

```python
class SolidifyResult(BaseModel):
    __test__ = False
    test_paths: list[str]                    # 生成的 test_*.py 路径
    spec_paths: list[str] = []               # 配对的 _spec.py
    verify_ok: bool = False                  # 试跑是否通过
    verify_rounds: int = 0                   # 回喂重写轮数
    warnings: list[str] = []
```

### 4.4 ExecuteResult

```python
class ExecuteResult(BaseModel):
    __test__ = False
    session: TestSession                     # 现有 reporters.base.TestSession（含 results）
    duration_s: float = 0.0
```

`total/passed/failed/skipped` 不重复定义，从 `session.results` 聚合（复用 reporters 既有逻辑）。`TestSession` 来自 `reporters.base`，`run_tests` 返回它。

### 4.5 ReportResult

```python
class ReportResult(BaseModel):
    __test__ = False
    report_paths: list[str]                  # Json/Html 落盘路径
    summary: str                             # 一段文字摘要
    stage_coverage: dict[str, bool]          # 键固定为 explore/solidify/execute，标记哪些阶段参与本次运行
```

### 4.6 ReflectResult

```python
class Risk(BaseModel):
    __test__ = False
    category: str                            # uncovered_area / explore_interrupted / low_confidence / ...
    description: str
    severity: str                            # P1 / P2

class ReflectResult(BaseModel):
    __test__ = False
    confidence_score: float                  # 0.0-1.0 综合可信度
    false_positives: list[dict] = []         # 现有误报审查结果
    stuck_warnings: list[str] = []           # 现有卡死检测
    risks: list[Risk] = []                   # 风险项
    reflect_reasoning: str                   # LLM 推理摘要
```

### 4.7 PipelineResult

```python
class PipelineResult(BaseModel):
    __test__ = False
    explore: ExploreResult
    solidify: SolidifyResult | None = None
    execute: ExecuteResult | None = None
    report: ReportResult
    reflect: ReflectResult
```

## 5. AgenticOrchestrator

薄编排器，只持 5 个 Stage 实例，按分支调用，无业务逻辑。

```python
class AgenticOrchestrator:
    def __init__(self, explore: ExploreStage, solidify: SolidifyStage,
                 execute: ExecuteStage, report: ReportStage,
                 reflect: ReflectStage) -> None: ...

    def run(self, config: ExploreConfig) -> PipelineResult:
        explore_out = self.explore.run(config)

        # 命中已固化资产 → 复用执行
        if explore_out.skipped:
            execute_out = self.execute.run(explore_out.reused_test_paths, config) if config.execute else None
            report_out = self.report.run(explore=explore_out, execute=execute_out, config=config)
            reflect_out = self.reflect.run(explore=explore_out, execute=execute_out,
                                           report=report_out, config=config)
            return PipelineResult(explore=explore_out, execute=execute_out,
                                  report=report_out, reflect=reflect_out)

        # 探索直出（不固化）
        if not config.solidify:
            report_out = self.report.run(explore=explore_out, config=config)
            reflect_out = self.reflect.run(explore=explore_out, report=report_out, config=config)
            return PipelineResult(explore=explore_out, report=report_out, reflect=reflect_out)

        # 探索 + 固化 + 执行
        solidify_out = self.solidify.run(explore_out.flow, config)
        execute_out = self.execute.run(solidify_out.test_paths, config) if config.execute else None
        report_out = self.report.run(explore=explore_out, solidify=solidify_out,
                                     execute=execute_out, config=config)
        reflect_out = self.reflect.run(explore=explore_out, execute=execute_out,
                                       report=report_out, config=config)
        return PipelineResult(explore=explore_out, solidify=solidify_out,
                              execute=execute_out, report=report_out, reflect=reflect_out)
```

工厂函数 `build_orchestrator(config)` 负责依赖注入：构造 provider/backend，new 各 Stage 并注入其依赖。Stage 构造时拿自己的依赖，编排器只持 Stage 引用。

## 6. 各 Stage 职责

### 6.1 ExploreStage — 智能入口

```
run(config) -> ExploreResult
  1. 若 config.reuse 非空 → 命中，填 reused_test_paths
  2. elif config.check_reuse:
       资产清单 = 扫 tests/generated_smoke/*.py 提取 docstring+文件名
       命中判定 = LLM 比对 intent 与资产清单
       命中 → skipped=True，填 reused_test_paths，返回
  3. 未命中 → 按 config.mode 探索：
       manual → GlobalRecorder(pynput_mode=True).start()...stop()
       dfs    → UIExplorer(backend, recorder=GlobalRecorder(程序化)).explore()
       llm    → LLMExplorer(backend, provider, recorder=GlobalRecorder(程序化)).explore()
  4. save_flow_yaml(flow) 落盘
  5. 返回 ExploreResult

依赖：LLMProvider（命中检查 + llm 模式）、ExecutorBackend（dfs/llm）、GlobalRecorder
```

### 6.2 SolidifyStage — 固化（LLM 一次性投资）

```
run(flow, config) -> SolidifyResult
  1. RecordedFlowGenerator.generate(flow, flow_dir, game_meta) → test_case 列表
  2. write_tests_to_dir 落盘到 tests/generated_smoke/
  3. 若 config.verify_during_solidify:
       TestCaseVerifier.verify_and_fix（试跑 + 回喂重写，现有未串联能力）
  4. 返回 SolidifyResult

依赖：LLMProvider、RecordedFlowGenerator、TestCaseVerifier、ExecutorBackend（verifier 试跑）
```

### 6.3 ExecuteStage — 执行（脱离 LLM）

```
run(test_paths, config) -> ExecuteResult
  1. runner.run_tests(test_paths, backend, game)  # 纯 pytest
  2. 聚合 TestResult session 为 ExecuteResult

依赖：ExecutorBackend（无 LLM）
```

### 6.4 ReportStage — 汇聚报告

```
run(explore, solidify=None, execute=None, config) -> ReportResult
  1. 根据 stage_coverage 决定报告丰富度
  2. 有 execute → 含冒烟结果的完整报告
     无 execute → 纯探索报告（轨迹摘要 + UIMap 概况）
  3. 经 MultiReporter 广播 Json/Html

依赖：list[TestReporter]（Json/Html）
```

### 6.5 ReflectStage — 反思

```
run(explore, execute=None, report, config) -> ReflectResult
  1. 现有 reflection.detect_stuck_loop（确定性卡死检测）
  2. 现有 reflection.filter_bug_reports + review_failure（误报审查，LLM）
  3. 新增可信度评分：断言强度 / 覆盖率 / 探索充分度（LLM）
  4. 新增风险提示：未覆盖区域 / 探索中断 / 低可信（LLM）

依赖：LLMProvider、现有 reflection 函数
```

可信度评分维度：
- **断言强度**：test_case 中 assert 的数量与具体性（值断言 > 存在性断言 > 无断言）。
- **覆盖率**：UIMap 中已探索界面/元素占比。
- **探索充分度**：探索是否因步数上限/卡死提前终止。

## 7. CLI 设计

### 7.1 命令清单

| 命令 | 对应 | 说明 |
|---|---|---|
| `joker-test explore`（新增） | ExploreStage | 智能入口。命中检查 + 探索，可串联固化 |
| `joker-test record` | ExploreStage(manual) | 现有保留，等价 `explore --mode manual` 快捷方式 |
| `joker-test explore-ui` | ExploreStage(dfs/llm,无固化) | 现有保留，等价 `explore --no-solidify` 快捷方式 |
| `joker-test run-smoke` | ExecuteStage | 现有保留，跑已有 test_paths，脱离 LLM |
| `joker-test run-all`（改造） | AgenticOrchestrator | 完整四阶段流水线 |
| `joker-test generate-charter` | — | 现有保留，独立议题 |
| `joker-test validate` | — | 现有保留 |

### 7.2 explore 子命令参数

```bash
joker-test explore \
    --intent "进入退出游戏的完整流程" \    # 必填：测试意图
    [--mode manual|dfs|llm] \            # 默认 llm
    [--reuse <test_path>] \              # 显式复用（0 LLM）
    [--check-reuse / --no-check-reuse] \ # 命中检查默认开
    [--solidify / --no-solidify] \       # 固化默认开
    [--execute / --no-execute] \         # 执行默认开
    [--game '<name>'] \
    [--backend airtest|fake] \
    [--max-steps 30] \
    [--no-trace]
```

### 7.3 默认无参数等价 run-all

`joker-test`（无子命令）等价于 `run-all`，使用更简便。默认 config：`mode=llm`、`check_reuse=True`、`solidify=True`、`execute=True`。

### 7.4 命令与 Stage 的复用关系

CLI 命令 = new Stage + 建 config + 调用 + 落盘。编排器 = new AgenticOrchestrator + 按分支跑。**零逻辑重复**：`explore`/`record`/`explore-ui` 都 new ExploreStage 传不同 config；`run-smoke` new ExecuteStage；`run-all` new AgenticOrchestrator。

## 8. 综合探索报告

### 8.1 报告数据模型

ReportStage 聚合各阶段产物为一份综合报告。新增 `ExploreReport` 数据模型（Pydantic）：

```python
class ExploreReport(BaseModel):
    __test__ = False
    intent: str
    mode: str
    stage_coverage: dict[str, bool]
    # 探索段
    explore_summary: str                     # 探索方式 + 步数 + 产出的界面数
    flow_steps_count: int = 0
    uimap_screen_count: int | None = None
    # 固化段（可选）
    solidify_summary: str | None = None
    test_count: int | None = None
    verify_ok: bool | None = None
    # 执行段（可选）
    execute_summary: str | None = None
    passed: int | None = None
    failed: int | None = None
    duration_s: float | None = None
    # 反思段
    confidence_score: float
    risks: list[Risk]
```

### 8.2 报告渲染

复用现有 `MultiReporter` 广播机制。新增 `ExploreReporter`（实现 `TestReporter` 协议）消费 `ExploreReport` 输出 Json/Html。ReportStage 持 `MultiReporter([JsonReporter, HtmlReporter, ExploreReporter])`。

Json 报告含完整数据；Html 报告含可视化（探索轨迹时间线 + 阶段覆盖雷达 + 风险列表）。Html 的可视化部分用现有 HtmlReporter 的模板风格，不过度设计。

## 9. 错误处理

遵循 §11.8（异常 `Error` 后缀，跨层异常隔离降级）。

| 故障点 | 处理 |
|---|---|
| ExploreStage 命中检查 LLM 失败 | 降级为"未命中"，继续正常探索。不阻断流水线。 |
| ExploreStage 探索中某步异常 | 沿用 LLMExplorer 现有单步异常隔离（跳过该步继续） |
| SolidifyStage LLM 生成失败 | 沿用 RecordedFlowGenerator 现有 QualityChecker 兜底 + 降级 |
| SolidifyStage verifier 试跑失败 | 不阻断，verify_ok=False 记入 SolidifyResult.warnings，test_case 仍落盘 |
| ExecuteStage pytest 失败 | 正常收集失败结果入 session，不算流水线故障 |
| ReportStage 某 reporter 失败 | MultiReporter 错误隔离，其他 reporter 继续 |
| ReflectStage LLM 失败 | 降级为确定性卡死检测 + 默认低可信度（confidence=0.3），记录降级原因 |

自定义异常：`ExploreStageError` / `SolidifyStageError` / `ExecuteStageError` / `ReportStageError` / `ReflectStageError`，均继承 `Exception`。各 Stage 内部捕获底层异常转为本 Stage 的异常，编排器不再做额外异常处理（任一 Stage 抛出即终止流水线，已产出的阶段结果在 PipelineResult 的已填充字段中）。

## 10. 测试策略

遵循 §11.10（CI 用 Fake + Mock，真机测试加 `_real` 后缀）。

| 测试文件 | 覆盖 |
|---|---|
| `tests/test_pipeline_types.py` | 数据契约模型往返 + 默认值 |
| `tests/test_pipeline_explore.py` | ExploreStage 三模式（FakeBackend + MockProvider 命中检查） |
| `tests/test_pipeline_solidify.py` | SolidifyStage（MockProvider 生成 + 跳过试跑） |
| `tests/test_pipeline_execute.py` | ExecuteStage（FakeBackend + 极简 test） |
| `tests/test_pipeline_report.py` | ReportStage 各 stage_coverage 组合 |
| `tests/test_pipeline_reflect.py` | ReflectStage 可信度评分 + 降级路径 |
| `tests/test_pipeline_orchestrator.py` | AgenticOrchestrator 三分支（命中/直出/全链路） |

编排器三分支是测试核心：
- **命中分支**：MockProvider 命中检查返回命中 → 验证 skipped=True + 走 Execute。
- **直出分支**：config.solidify=False → 验证不进 Solidify/Execute。
- **全链路分支**：未命中 + solidify=True → 验证五阶段全跑。

## 11. 与现有架构的关系

| 现有 | 处理 | 理由 |
|---|---|---|
| `StaticOrchestrator` | 删除 | AgenticOrchestrator 覆盖且更完整 |
| `exploratory.py` + `tactics.py` | 删除 | M4 简化版战术 bug 查找，本次不整合，保留徒增维护面 |
| `reflection.py` | 保留 | ReflectStage 复用其卡死检测 + 误报审查 |
| `runner.py` | 保留 | ExecuteStage 复用 `run_tests` |
| `flow/` | 保留 | ExploreStage + SolidifyStage 复用 |
| `explorer/` | 保留 | ExploreStage 的 dfs/llm 模式复用 |
| `perception/` | 保留 | 录像固化语义化 + executor 公共依赖 |
| `reporters/` | 保留 + 扩展 | 新增 ExploreReporter，复用 MultiReporter |
| `llm/` `executor/` `prompts/` `plugins/` | 保留不动 | 公共底座 |

## 12. 不做的事（YAGNI）

- 不实现三层异步引擎（L1/L2/L3）。本次是同步流水线，借 Open-AutoGLM 的**循环模式**而非异步架构。
- 不实现 Investigator 独立验证。反思阶段的可信度评分是轻量替代。
- 不实现 Meta-CoT Judge。沿用现有确定性判定 + LLM 误报审查。
- 不整合战术库 bug 查找（已随 tactics.py 删除）。
- 不新增 ADR。本设计是 R-ADR-6 的落地，不突破现有决策。
- 不为资产发现维护索引文件。扫目录实时提取够用。
- 报告 Html 不过度设计可视化。基础时间线 + 风险列表即可。

## 13. 实现顺序（建议）

1. `pipeline/types.py` + `pipeline/base.py`（数据契约 + Stage Protocol + AgenticOrchestrator 骨架）
2. `pipeline/stages/execute.py` + `report.py`（最简单，先打通 Execute→Report）
3. `pipeline/stages/explore.py`（命中检查 + 三模式分发）
4. `pipeline/stages/solidify.py`（接 generator + verifier）
5. `pipeline/stages/reflect.py`（扩展反思）
6. `pipeline/agentic.py`（编排三分支）
7. 删除 `orchestrator.py` / `exploratory.py` / `tactics.py` + 清理引用
8. `cli.py` 改造（新增 explore + run-all 走 AgenticOrchestrator + 无参数默认 run-all）
9. 测试（按第 10 节）
10. 更新 `AGENTS.md` / `DESIGN.md`（目录结构 + CLI 命令 + 状态）

每步可独立验证。第 7 步删除前先确认无残留引用。
