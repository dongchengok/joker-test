# joker-test 架构设计文档

> **文档版本**：v0.9
> **创建日期**：2026-06-26
> **状态**：架构权威文档，模块实现细节见代码 docstring
> **关联代码**：`src/joker_test/`（M0-M6 + 探索流水线已实现）、`docs/roadmap/iteration-roadmap.md`（迭代路线）

---

## 文档使用说明

这份文档**刻意模块化**，每个章节独立，便于频繁修改：

- **要改某个模块的设计** → 直接改第 4 节对应子章节
- **要追加一个新决策** → 在第 6 节追加一条 ADR
- **要标记一个未解决问题** → 在第 8 节追加一条
- **每次重要修改** → 在第 10 节加一条修改记录

---

## 目录

- [1. 项目定位](#1-项目定位)
- [2. 核心理念](#2-核心理念)
- [3. 整体架构](#3-整体架构)
- [4. 模块详解](#4-模块详解)
- [5. 数据契约](#5-数据契约)
- [6. 关键决策记录（ADR）](#6-关键决策记录adr)
- [7. 落地路径](#7-落地路径)
- [8. 风险与开放问题](#8-风险与开放问题)
- [9. 术语表](#9-术语表)
- [10. 修改记录](#10-修改记录)
- [11. 命名与目录设计原则](#11-命名与目录设计原则)

---

## 1. 项目定位

### 1.1 这是什么

**joker-test 是一个面向游戏 QA 团队的 AI 驱动测试平台**，名字致敬 SpecOps（Specialist Operations）。核心理念是"**LLM 用得克制**" —— 在生成、决策、验证的高价值环节用 LLM，在执行、回归、批处理环节用脚本。

### 1.2 这不是什么

| 不是 | 为什么 |
|---|---|
| 通用 QA SaaS | 聚焦游戏垂直，不做 Web/移动 |
| UI 自动化框架 | 不替代 Airtest/Poco，而是**调度**它们 |
| 通用 LLM Agent | 不做开放域任务，专注游戏测试 |
| SpecOps 的游戏 fork | 借鉴架构，但加了 Persona/插件/分层测试 |

### 1.3 与 SpecOps 的关系

| 维度 | SpecOps | joker-test |
|---|---|---|
| 测试对象 | GUI-based AI Agent | 游戏（含玩家 Agent） |
| 测试范式 | feature-driven（功能验证） | **charter-driven**（探索式）+ **Python 脚本**（冒烟） |
| LLM 使用 | 全 pipeline 用 | **克制**：生成 + 关键决策处用 |
| 扩展性 | 无 | **插件系统**（数据/规则/工具/Reporter） |
| 集成 | 学术 prototype | **CLI + MCP** |
| 平台 | Linux/X11 强依赖 | **跨平台**（Windows 优先） |

---

## 2. 核心理念

### 2.1 测试分层

```
┌──────────────────────────────────────────────────┐
│  游戏测试完整图景                                  │
├────────────────┬─────────────────────────────────┤
│ 冒烟/回归 (80%)│  探索式 (15-20%)                  │
│                │                                  │
│ Python + pytest│  LLM 探索流水线（pipeline）       │
│ CI 每次构建跑  │  版本前/每周跑                    │
│ 找已知 bug     │  找未知 bug + 可信度评估           │
│ ¥0/次          │  ¥0.3-1/次                       │
└────────────────┴─────────────────────────────────┘
```

**关键洞察**：**冒烟和探索式不是替代关系，是互补**。LLM 跑冒烟是浪费（确定性需求 + 高频次），脚本跑探索做不到（找不到未知 bug）。

### 2.2 LLM 克制使用

| 场景 | 用 LLM? | 原因 |
|---|---|---|
| Charter 生成（探索意图） | ✅ | 需要创意和领域知识 |
| 冒烟 Python 测试代码生成 | ✅ | 把需求转代码 |
| 探索决策（LLM 模式每步） | ✅ | 非确定性判断（理解界面+决策下一步） |
| 固化命中检查 | ✅ | 意图与已有资产比对（1 次，省整轮探索） |
| 反思（可信度+风险） | ✅ | 综合分析 |
| 误报审查 | ✅ | 语义判断（失败测试/Bug 是否真 bug） |
| 冒烟测试执行 | ❌ | 需要确定性 + 高频 |
| 感知（OCR/图像匹配） | ❌ | 算法可解，规则化 |
| 数据合法性校验 | ❌ | 规则化、可枚举 |

### 2.3 插件扩展性

每个游戏可以有专属插件，提供：
- **数据源**（NPC/物品/任务的合法值）
- **校验规则**（数值边界、状态约束）
- **工具**（内存读取、控制台命令）
- **Reporter**（游戏特有可视化，如经济平衡图）

主框架保持通用，**游戏特化逻辑全部走插件**。

### 2.4 可集成性

- **CLI 是核心契约**：人、外部 harness、内部 orchestrator 都面向同一套 CLI（R-ADR-6）
- **MCP 降为可选增强**：仅在需结构化 tool 发现时实现（R-ADR-8 推翻了原"MCP 是包装"定位）
- **主集成方式 = AGENTS.md + CLI**：harness 读指引后调 CLI，最省 token

### 2.5 可视化插件化

Reporter 与 Backend 平行（输入侧/输出侧各一套抽象），详见 §4.7 + ADR-009。

---

## 3. 整体架构

### 3.1 模块全景

分两层：**编排层**（两条测试线 + 统一入口）建立在**基础设施层**（公共能力）之上。

```
┌─ 编排层 ──────────────────────────────────────────────────────────┐
│                                                                   │
│   CLI 入口（统一契约，7 子命令，无参数默认 run-all，R-ADR-6）       │
│                                                                   │
│   ┌─ 冒烟线 ─────────────┐    ┌─ 探索流水线（pipeline）─────────┐ │
│   │ LLM 生成 pytest 代码  │    │ AgenticOrchestrator 薄编排       │ │
│   │ （一次性投资）         │    │                                  │ │
│   │        ↓              │    │  Explore ──► Solidify ──► Execute│ │
│   │ pytest 执行（脱离LLM）│    │     │                         │ │
│   │        ↓              │    │     ▼                         │ │
│   │ TestSession           │    │  Report ──► Reflect            │ │
│   └───────────┬───────────┘    └──────────────┬──────────────────┘│
│               │                               │                   │
│               │ Charter 生成（独立议题，§4.1） │                   │
│               │                               │                   │
└───────────────┼───────────────────────────────┼───────────────────┘
                │                               │
                ▼                               ▼
┌─ 基础设施层 ──────────────────────────────────────────────────────┐
│                                                                   │
│   ExecutorBackend        Reporter          LLMProvider             │
│   (Airtest默认/Fake)     (Json/Html/       (Mock/GLM/MiMo/         │
│        │                 Multi/Explore)     Anthropic/Bedrock)     │
│        │                        ▲                  ▲               │
│        ▼                        │                  │               │
│   PerceptionEngine        flow/（录制+固化）  explorer/（DFS+LLM）  │
│   (OCR+图像匹配+LLM识图)   reflection/（反思） generator/（生成）   │
│                             runner/（pytest执行） plugins/（插件） │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘

        规划态：完整三层异步引擎(L1感知/L2决策/L3战略) + Investigator
                + Meta-CoT Judge + AllureReporter（见 §4.3/§4.4）
```

**实现状态**：编排层两条线 ✅ 已实现；基础设施层 ✅ 已实现；虚线框/规划态标注的三层引擎、Investigator、Meta-CoT Judge、AllureReporter 均 ❌ 规划中。

### 3.2 数据流

两条并行数据流（冒烟线 + 探索流水线），由 CLI 统一入口：

```
【冒烟线】（Python 脱离 LLM，CI 高频）
[游戏元数据] + [插件]
       │
       ▼
[LLM 生成 Python 测试代码] ← 一次性投资
       │
       ▼
[pytest + Backend]──[CI 每次]──→ Json/Html 报告（TestSession）

【探索流水线】（pipeline，LLM 克制参与）
[测试意图] ──► ExploreStage（固化命中检查 + 三模式探索）
                   │
       ┌───────────┼───────────────┐
       ▼           ▼               ▼
   命中复用    探索+固化         探索直出
   Execute     Solidify→Execute    │
       │           │               │
       └─────┬─────┘               │
             ▼                     │
        ReportStage ◄──────────────┘  （汇聚综合报告）
             │
             ▼
        ReflectStage（可信度+风险+误报审查）

完整版目标（规划中）：三层异步引擎 L1(60FPS感知)/L2(决策)/L3(战略)
+ Investigator 独立验证 + Judge(Meta-CoT)。详见 §4.3。
```

---

## 4. 模块详解

> **实现说明**：M0-M6 已实现各模块的核心。本节给出**架构职责 + 关键设计 + 代码指针**；
> 接口签名、数据结构、实现细节一律见代码 docstring（权威源）。

### 4.1 Charter 生成（探索式测试入口）

**职责**：LLM 生成探索意图（不是具体步骤）。

**输入**：游戏元数据 + 被测系统列表 + Persona 库 + Heuristics 库
**输出**：Charter JSON 数组（每个 target × persona 一个独立 JSON）

**关键设计**：
- Persona 驱动（5 种玩家人格：破坏狂/贪婪者/急躁鬼/完美主义/混乱中立）
- Heuristics 库（QA 经验启发式，可被插件覆盖）
- Coverage Map 4 维度（区域/功能/操作/状态）
- 双 specialist 架构（Architect 生成 + Analyst 反思）
- `env_probing_required` 字段决定是否启动 Investigator

**状态**：✅ 已实现。**代码**：`charter_gen.py`、`prompts/`（模板已抽离到独立包）
**开放问题**：Charter 优先级排序、跨 Charter 的 Coverage Map 共享（见 §8 Q1/Q2）

---

### 4.2 冒烟测试（Python + pytest + Backend）

**职责**：LLM 生成 Python 测试代码 + spec 数据，pytest 执行，**执行完全无 LLM**。

**核心设计**：
- 测试**逻辑** = Python 代码（pytest 测试函数），LLM 生成
- 测试**数据** = Pydantic spec（参数化用），LLM 生成
- 测试**执行** = pytest + ExecutorBackend，无 LLM

**生成链路**：`UIExplorer` 探索界面 → `SmokeTestGenerator` 消费 UIMap 调 LLM 生成代码 → `QualityChecker`（ruff + ast.parse）兜底 → `write_tests_to_dir` 落盘 → `runner.py`（pytest inline plugin）执行收集结果。

**Backend 抽象**：

```
Python 测试代码 / 探索器 / 引擎
        ↓
  ExecutorBackend 协议（归一化坐标 [0,1]，基准=screenshot 尺寸）
        ↓
┌─────────────────┬──────────────────┐
│ AirtestBackend  │ FakeBackend      │
│ (默认，图像识别) │ (内存模拟，CI用) │
└─────────────────┴──────────────────┘
```

**状态**：✅ 已实现。**代码**：`generator/`（生成）、`runner.py`（执行）、`executor/`（Backend 抽象）
**关联契约**：测试代码规范见 §5.2，ExecutorBackend 接口见 `executor/base.py`

---

### 4.3 探索流水线（已落地）+ 三层异步引擎（规划态）

**职责**：探索游戏界面、固化操作为冒烟用例、执行、报告、反思。

**探索流水线**（✅ 已实现）：借 Open-AutoGLM 的循环模式（think-act-observe），保留 LLM 克制红线。薄编排 `AgenticOrchestrator` 按分支调用 5 个状态自洽的 Stage：

| Stage | 职责 | 用 LLM? |
|---|---|---|
| Explore | 智能入口：固化命中检查（扫资产+LLM比对）+ 三模式探索（manual录像/dfs确定性/llm agentic loop），统一产 RecordedFlow | 命中检查+llm模式 |
| Solidify | 操作轨迹→LLM生成 pytest test_case（含试跑回喂）。一次固化多次回归，脱离 LLM | 生成阶段 |
| Execute | 跑 pytest，脱离 LLM | 否 |
| Report | 汇聚各阶段产物为综合探索报告（ExploreReporter） | 否 |
| Reflect | 可信度评分 + 风险提示（断言强度/覆盖率/探索充分度）+ 卡死检测 + 误报审查 | 评分+审查 |

设计详见 `docs/superpowers/specs/2026-07-09-explore-pipeline-design.md`。

**完整版目标架构**（❌ 规划中，未实现）—— 三层异步，按延迟分级用不同模型：

| 层 | 频率 | 工具 | 模型 |
|---|---|---|---|
| L1 感知 | 60 FPS | YOLO/GroundingDINO + OCR + 模板匹配 + 物理监控 | 无（纯算法） |
| L2 决策 | 1-3 秒 | 本地 VLM 决策探索方向 | 本地 VLM |
| L3 战略 | 30秒-2分 | session 总结 + Coverage gap 分析 | 云端 LLM |

**状态**：探索流水线 ✅ 已实现（`pipeline/` 包）；完整三层异步引擎 ❌ 规划中。**代码**：`pipeline/`（5 Stage + AgenticOrchestrator）、`reflection.py`（反思复用）

---

### 4.4 反思与误报处理

**职责**：评估测试可信度、提示风险、过滤误报。

**当前实现**（✅ ReflectStage，`pipeline/stages/reflect.py`）：流水线的反思阶段，四项职责：
- **卡死检测**（确定性，复用 `reflection.detect_stuck_loop`）：连续 N 次操作信号相同 → 疑似卡死
- **误报审查**（LLM，复用 `reflection.review_failure`）：对 failed 测试调 LLM 判断"真 bug vs 误报（断言写错/fixture 问题/环境问题）"
- **可信度评分**（LLM）：综合断言强度/覆盖率/探索充分度给 0.0-1.0 分
- **风险提示**（LLM）：未覆盖区域/探索中断/低可信等风险项（含 severity P1/P2）

**降级策略**：LLM 调用失败时降级为低可信度（0.3）+ 确定性卡死检测，不阻断流水线。

**游戏 Bug 标准**（5 类，权威契约，供反思/审查 prompt 引用）：
- 视觉异常：穿模/UI 错位/贴图丢失/动画卡死/坐标越界
- 状态异常：数值越界（金币为负/血量超上限）/状态不同步
- 流程异常：任务无法完成/对话矛盾/流程死锁/软锁存档
- 经济异常：金币物品复制/刷分漏洞/负数套利
- 性能异常：FPS 暴跌/内存泄漏/加载超时
- **防过度报**：bug 必须 prompt+环境可观察下可避免；体验建议不算 bug

**规划态**（❌ 未实现）：Investigator 独立验证（三方案见 ADR-005，默认反向 VLM）+ Meta-CoT Judge 综合判断 + 完整 BugReport Schema（§5.3）。待完整三层引擎落地。

---

### 4.5 插件系统

**职责**：让每个游戏可以有专属扩展，主框架保持通用。

**4 类扩展点**（ADR-003，v0.1 不开放 Persona/Heuristics 扩展，ADR-007）：

| 类型 | 提供什么 | 例子 |
|---|---|---|
| 数据源 | 游戏数据表 schema | NPC/武器/任务的合法值 |
| 校验规则 | 自定义 Bug 检测 | "金币不能为负" |
| 工具 | 自定义操作工具 | 内存读取、控制台命令 |
| Reporter | 自定义可视化 | 经济平衡图 |

**设计要点**：
- `GamePlugin` 是 **Protocol**（结构性子类型，无需显式继承）
- 加载机制 = importlib 动态发现（仿 pytest 插件）

**状态**：✅ 已实现。**代码**：`plugins/base.py`（GamePlugin Protocol + DefaultPlugin）、`plugins/loader.py`

---

### 4.6 CLI + MCP 集成

**职责**：让 joker-test 可被人、外部 AI Harness、内部 orchestrator 调用。

**集成架构**（权威，详见 roadmap §9 / R-ADR-6/7/8）：
- **编排者可替换 + CLI 是统一契约**（R-ADR-6）：人 / 外部 harness / 内部 orchestrator 面向同一套 CLI
- **三种集成方式**（R-ADR-8）：①CLI（人，核心）②**AGENTS.md + CLI**（harness 主方式，最省 token）③MCP Server（可选增强）
- **不抽象 Harness 层**（R-ADR-7）：编排策略可插拔（AgenticOrchestrator 薄编排，见 §4.3）

**已实现的 CLI 子命令**（`cli.py`，7 个，无参数默认等价 `run-all`）：
- `explore`：智能探索入口（固化命中检查 + 三模式探索 + 可串联固化/执行，§4.3）
- `generate-charter`：Charter 生成（§4.1）
- `explore-ui`：界面探索（产出 UIMap，§4.2）
- `run-smoke`：跑冒烟测试 + 报告（§4.2）
- `run-all`：AgenticOrchestrator 编排 探索→固化→执行→报告→反思（§4.3 + §4.7）
- `validate`：校验 charter schema + 测试语法
- `record`：录制操作流程（pynput 监听）→ LLM 起名 → 坐标语义化 + 生成 test_case（`flow/` 包）

`explore` / `explore-ui` / `run-all` / `record` 支持 `--no-trace` 关闭全局 trace（默认开启，见 §11.9）。

**MCP Server**：❌ 未实现（可选增强，R-ADR-8，推迟到按需）

**关联文件**：`cli.py`、`pipeline/`（编排器 + Stage）

---

### 4.7 测试可视化（Reporter 插件化）

**职责**：把"测试做了什么、结果如何、为什么失败"用人类能看懂的方式呈现。

**核心设计**：Reporter 与 Backend 平行（输入侧/输出侧各一套抽象），ADR-009。

**TestReporter 协议**（Protocol + `@runtime_checkable`，5 个 hook）：
- `on_session_start` / `on_test_start` / `on_test_end` / `on_session_end` / `finalize`

**内置实现**：

| Reporter | 状态 | 用途 |
|---|---|---|
| `JsonReporter` | ✅ | 机器可读 JSON（给 CI 消费） |
| `HtmlReporter` | ✅ | 单 HTML 文件（本地开发） |
| `MultiReporter` | ✅ | 广播 + 错误隔离（一个崩了不影响其他） |
| `ExploreReporter` | ✅ | 综合探索报告 JSON（汇聚 pipeline 各阶段产物，`reporters/explore.py`） |
| AllureReporter | ❌ 规划 | 工业标准 Web 报告 |
| CoverageMapReporter | ❌ 规划 | 4 维覆盖度热图（特色） |
| CharterReplayReporter | ❌ 规划 | Charter 轨迹回放 |

**状态**：核心 4 个 ✅ 已实现。**代码**：`reporters/base.py`（协议 + 数据契约）、`reporters/{json,html,multi}/`、`reporters/explore.py`（ExploreReporter + ExploreReport 模型）。**关联契约**：数据结构见 `reporters/base.py`（TestSession/TestCase/TestResult）

---
## 5. 数据契约

### 5.1 Charter JSON Schema（已实现）

```json
{
  "charter_id": int,
  "target_id": int,
  "persona": "破坏狂 | 贪婪者 | 急躁鬼 | 完美主义 | 混乱中立",
  "target_system": string,
  "target_description": string,
  "load_save": string,
  "goal": string,
  "exploration_targets": string[],
  "heuristics": string[],
  "expected_behaviors": string[],
  "coverage_dimensions": {
    "region": string[],
    "function": string[],
    "operation": string[],
    "state": string[]
  },
  "time_budget_minutes": int,
  "env_probing_required": "yes | no",
  "severity_threshold": "P0 | P1 | P2"
}
```

**关联文件**：`joker_test/charter_gen.py`

### 5.2 测试代码规范（Python + Pydantic spec，ADR-008）

测试分两层（详细规范见 §11.10 + `prompts/constants/smoke_quality_rules.md`）：
- **逻辑层**：`test_<system>.py`，pytest 函数 + `backend` fixture，一个函数测一件事
- **数据层**：`<system>_spec.py`，Pydantic BaseModel，`@pytest.mark.parametrize` 注入

**关联文件**：`generator/quality.py`（质量检查）、`prompts/constants/smoke_quality_rules.md`

### 5.3 Bug 报告 Schema（规划态）

> 当前 pipeline 不产出 BugReport。测试结果走 `TestSession`（§5.5），探索反思走 `ReflectResult`（可信度+风险+误报，见 `pipeline/types.py`）。本 Schema 为完整三层引擎落地后的目标，待 Investigator + Meta-CoT Judge 实现时采用。

完整版 Schema（规划中）：

```json
{
  "bug_id": string,
  "charter_id": string,
  "severity": "P0 | P1 | P2 | P3",
  "category": "视觉 | 状态 | 流程 | 经济 | 性能",
  "title": string,
  "description": string,
  "reproduction_steps": string[],
  "evidence": {
    "screenshots": string[],
    "video_clip": string,
    "memory_state": object,
    "vlm_analysis": string
  },
  "investigator_report": string,
  "judge_reasoning": string,
  "first_seen": "ISO 8601",
  "confidence": float
}
```

### 5.4 GameState Schema（完整版目标，规划中）

完整 L1 感知层状态快照（detections/texts/fps/memory/cpu/player_state/inventory/quest_log）。
**当前实现**：`executor/base.py:LazyState` 是最小子集（texts + find_text），完整版待 M4 三层引擎。

### 5.5 Reporter 数据契约

**已实现**，见 `reporters/base.py`：TestSession / TestCase / TestResult / Attachment（pydantic BaseModel）。
TestReporter 协议（5 个 hook）见同文件。

---


## 6. 关键决策记录（ADR）

> ADR (Architecture Decision Records) 风格：每个决策一个独立条目，便于追溯和修改。

### ADR-001：冒烟测试用 Python + pytest + Backend 抽象

- **状态**：已采纳
- **决策**：直接用 Python（pytest + ExecutorBackend 抽象）。
- **理由**：
  1. 零学习成本（QA 已经会 Python）
  2. 零开发成本（不用写 DslEngine）
  3. 完整生态（pytest + IDE + 重构工具 + 类型检查）
  4. LLM 生成 Python 质量稳定（>95%）
  5. 跨框架通过 Backend 抽象一样能实现
- **后果**：
  - ✅ 上手即用，pytest 生态完整
  - ❌ LLM 生成代码不可声明式校验（用 Pydantic 校验 spec 数据部分弥补）

### ADR-002：抽象 ExecutorBackend 接口，Airtest 是默认 backend

- **状态**：已采纳（**R-ADR-5 修订**：默认从 OpenCVBackend 改为 AirtestBackend）
- **决策**：抽象 `ExecutorBackend` 接口，当前 2 个实现：
  - `AirtestBackend`（**默认**，airtest 图像识别，引擎无关；D6/R-ADR-5）
  - `FakeBackend`（内存模拟，CI 用）
- **理由**：引擎无关；图像识别为核心（D7）；Poco 为 Unity 可选增强
- **后果**：✅ 不被框架锁定；❌ 需维护多 backend（OpenCVBackend 从未实现，已从计划移除）

### ADR-003：插件用 Python 类，不用配置文件

- **状态**：已采纳
- **决策**：用 Python 类。
- **理由**：校验规则天然是代码（lambda/函数）；工具函数必须是代码；配置文件表达力不够。
- **后果**：
  - ✅ 表达力强，类型提示友好
  - ❌ 写插件门槛高（需要懂 Python）

### ADR-004：三层架构而非两层

- **状态**：已采纳（**规划态**，当前实现为同步流水线，见 ADR-011）
- **决策**：三层异步架构（L1 感知 60FPS / L2 决策 1-3s / L3 战略 30s-2min）。
- **理由**：游戏实时性要求高；不同延迟层级用不同模型。
- **后果**：
  - ✅ 延迟可控，成本优化（80% 用本地模型）
  - ❌ 架构复杂度增加；当前用同步 pipeline 作为简化先行版

### ADR-005：默认 Investigator 用反向 VLM，不用内存读取

- **状态**：已采纳
- **决策**：默认反向 VLM，内存读取作为可选插件。
- **理由**：反向 VLM 通用（跨游戏）；内存读取侵入性强；反向 VLM 可作为内存读取的兜底。
- **后果**：
  - ✅ 开箱即用，通用性强
  - ❌ 精度不如内存读取

### ADR-006：CLI 是统一契约，MCP 可选增强

- **状态**：已采纳（**R-ADR-8 修订**：MCP 从"包装"降为"可选增强"）
- **决策**：CLI 是核心契约（人/harness/orchestrator 都面向它）；MCP 降为可选，仅在需结构化 tool 发现时实现
- **理由**：CLI 易测试、可单独用；主集成方式 = AGENTS.md + CLI（harness 读指引调 CLI，最省 token）
- **后果**：✅ 测试友好，灵活集成；❌ CLI 状态管理稍复杂

### ADR-007：第一版插件接口不开放 Persona/Heuristics 扩展

- **状态**：已采纳
- **决策**：v0.1 只开放 4 类扩展点（数据/规则/工具/Reporter），Persona/Heuristics 走默认。
- **理由**：接口稳定性优先；Persona/Heuristics 用户改 game_metadata.json 就能覆盖。
- **后果**：
  - ✅ 接口简单，易稳定
  - ❌ 灵活性受限

### ADR-008：测试代码组织 = Python 模块 + Pydantic spec 数据

- **状态**：已采纳
- **决策**：分两层：
  - 测试**逻辑**：Python 代码（pytest 测试函数），由 LLM 生成 + 人工 review
  - 测试**数据**：Pydantic 校验的 spec 文件（JSON/YAML），LLM 生成
- **理由**：测试逻辑复杂多变，Python 表达最自然；测试数据相对稳定，spec 化便于校验和参数化。
- **后果**：
  - ✅ 逻辑层用 Python 全部能力，数据层有 schema 校验
  - ❌ 两层分离需要规范（命名约定、目录结构）

### ADR-009：抽象 TestReporter 接口

- **状态**：已采纳（**修订**：Allure 未实现，当前默认冒烟用 Json+Html via MultiReporter，探索用 ExploreReporter）
- **决策**：抽象 `TestReporter` 接口，内置实现 Json/Html/Multi/Explore（✅ 已实现）+ Allure/CoverageMap/CharterReplay（❌ 规划）。
- **理由**：
  1. 不同场景需要不同 reporter（本地 vs CI vs 团队 server）
  2. 解耦（核心代码不依赖具体 reporter）
  3. 错误隔离（MultiReporter 一个崩了不影响其他）
  4. 可扩展（用户自研 reporter）
- **后果**：
  - ✅ 可选、可组合、可扩展
  - ❌ 接口要稳定（v0.1 接口简单，稳定后扩展）

### ADR-010：插件可以提供 Reporter

- **状态**：已采纳
- **决策**：插件接口加 `get_reporters()` 方法，允许插件提供游戏特化 reporter。
- **理由**：通用 reporter（Allure）+ 游戏特化 reporter（如经济平衡图）可以共存，互不冲突。
- **后果**：
  - ✅ 灵活，游戏可以有自己的可视化
  - ❌ 用户要理解"通用 reporter + 游戏特化 reporter"两个层级

### ADR-011：探索流水线取代战术库 bug 查找

- **状态**：已采纳
- **决策**：删除 `orchestrator.py`（StaticOrchestrator）、`exploratory.py`（战术库+Judge）、`tactics.py`（5 战术），以 `pipeline/` 包的 AgenticOrchestrator 薄编排 5 个 Stage（Explore/Solidify/Execute/Report/Reflect）作为统一探索流水线。借鉴 Open-AutoGLM 的循环模式，保留 §2.2 LLM 克制红线（LLM 仍在命中检查/探索决策/固化生成/可信度评分的高价值点用）。
- **理由**：战术库 bug 查找（M4 简化版）与冒烟/探索式两条线割裂，StaticOrchestrator 只跑半边；pipeline 把分散能力（GlobalRecorder/LLMExplorer/UIExplorer/RecordedFlowGenerator/reflection/reporters）整合为统一四阶段 DAG，支持智能固化命中（已固化资产复用）、三模式探索、全自动编排与独立命令。固化产物是持久化资产（一次固化 N 次回归，脱离 LLM）。
- **后果**：
  - ✅ 统一探索入口，CLI 无参数默认 run-all
  - ✅ Stage 状态自洽、薄编排避免网状依赖（§11 规范）
  - ❌ 战术库的"急躁鬼/贪婪者/破坏狂"Persona 驱动 bug 查找能力暂失，待完整三层引擎落地时以新形式回归

---

## 7. 落地路径

> **已迁移**：落地路径由 `docs/roadmap/iteration-roadmap.md` 维护（垂直切片 M0-M6，滚动更新）。
> 原 §7 的水平阶段设计已被取代。

---

## 8. 风险与开放问题

### 8.1 已识别风险

| # | 风险 | 严重度 | 缓解 | 状态 |
|---|---|---|---|---|
| R1 | 插件接口稳定性（一旦定下难改） | 高 | v0.1 只开放核心扩展点 | 监控中（Protocol 已实现） |
| R2 | LLM 生成 Python 测试代码质量参差 | 高 | QualityChecker(ruff+ast) + pytest + mypy | 已落地（待持续验证） |
| R3 | 内存读取的游戏适配成本 | 中 | 默认 VLM Agent，内存读取可选（ADR-005） | 已决策 |
| R4 | 长时间 charter 状态管理 | 中 | session ID + 中间状态持久化 | 待设计 |
| R5 | Airtest 是 GPL-3.0 | 低 | 内部/SaaS 使用不传染（D5） | ✅ 已解决 |
| R6 | 战术脚本库的覆盖度 | 中 | 战术库（tactics/exploratory）已删除，探索改走 pipeline 三模式 | 已废弃（改走 pipeline） |
| R7 | LLM 成本失控 | 中 | 本地 VLM 为主，关键判断走云端 | 设计中 |
| R8 | Reporter 接口稳定性 | 中 | v0.1 接口简单（5 hook Protocol），稳定后再扩展 | ✅ 已实现 |

### 8.2 开放问题（待讨论）

- [ ] **Q1**：Charter 优先级排序机制？（5 系统 × 5 Persona = 25 个 charter，CI 不能全跑）
- [ ] **Q2**：跨 Charter 的 Coverage Map 共享？（避免重复探索）
- [ ] **Q3**：插件安全性？（任意 Python 代码，是否需要沙箱）
- [ ] **Q4**：插件间冲突解决？（两个插件都注册 `read_memory` 工具）
- [ ] **Q5**：session 状态持久化格式？（JSON / SQLite / 时序数据库）
- [ ] **Q6**：多人协作场景？（多个测试人员同时跑 charter）
- [ ] **Q7**：与现有 QA 工具集成？（Bug 追踪系统、CI 平台）
- [ ] **Q8**：游戏更新后的 charter 迁移？（游戏更新后旧 charter 是否还适用）
- [ ] **Q9**：成本统计与计费？（按 charter / 按 bug / 按时间）
- [ ] **Q10**：游戏特化 Reporter 的标准化？（不同插件 reporter 如何统一展示）

---

## 9. 术语表

| 术语 | 含义 |
|---|---|
| **Charter** | 探索式测试章程（一个 30 分钟的探索目标） |
| **Persona** | 玩家人格（破坏狂/贪婪者/急躁鬼/完美主义/混乱中立） |
| **Heuristics** | QA 经验启发式（如"测试边界值"、"测试时序错乱"） |
| **Coverage Map** | 覆盖度地图（4 维：区域/功能/操作/状态） |
| **Tactic** | 战术脚本（已废弃，探索改走 pipeline 三模式） |
| **Stage** | 流水线阶段（Explore/Solidify/Execute/Report/Reflect，状态自洽） |
| **ExploreStrategy** | 探索策略协议（可替换：ReactStateStrategy 默认 / ConversationStrategy 对比） |
| **ReactStateStrategy** | ReAct 思维链 + 状态机驱动（固定 token，默认策略） |
| **ConversationStrategy** | conversation context 策略（对齐 Open-AutoGLM，对比基准） |
| **ExecutorBackend** | 执行后端抽象（AirtestBackend 默认 / FakeBackend CI 用） |
| **StateMap** | 探索状态地图（Screen 集合 + Exit 边，原 UIMap） |
| **BBox** | 归一化边界框 [0,1]（x,y,w,h，基准=screenshot 尺寸） |
| **PerceptionEngine** | 多层界面识别（OCR + 图像匹配 + LLM 识图，漏斗） |
| **TestReporter** | 报告器抽象（Json/Html/Multi 已实现，Protocol） |
| **MultiReporter** | 多 reporter 组合器（事件广播 + 错误隔离） |
| **LazyState** | 按需解析的游戏状态代理（texts + find_text） |
| **Investigator** | 独立验证者（规划中，完整三层引擎） |
| **Judge** | 综合判断者（规划中=Meta-CoT；当前由 ReflectStage 轻量替代） |
| **GameState** | 游戏状态快照（完整版 L1 感知输出，规划中） |
| **Plugin** | 游戏特化扩展（数据/规则/工具/Reporter，Protocol） |
| **Session** | 一次 charter 执行的会话 |
| **Spec** | Pydantic 校验的测试数据（参数化测试输入） |
| **Allure** | 默认 Reporter 实现之一（工业标准 Web 报告） |

---

## 11. 命名与目录设计原则

> v0.5 新增（2026-07-06）。v0.6 扩展（2026-07-07）：补全 docstring/类型/import/错误处理/日志/测试六节。
> 全仓统一的编码规范，后续所有模块必须遵守。

### 11.1 目录组织

**有抽象的包**（如 `llm/`、`executor/`）按此结构组织：

```
<package>/
├── __init__.py        # 导出对外 API（用 __all__）
├── base.py            # 协议/接口（Protocol 或 ABC）
├── <utils>.py         # 跨实现共享的公共工具（无 _ 前缀）
└── <impl>/            # 每个实现一个子文件夹
    ├── __init__.py
    └── ...
```

- **包根放协议 + 公共工具**：`base.py` 是接口契约，公共工具是所有实现共享的
- **每个实现进自己的子文件夹**：即使当前单文件也用文件夹（为实现的内部扩展留空间，避免后期扁平→嵌套重构）
- 子文件夹内部可有私有模块（`_` 前缀），只给该实现用

**数据驱动的包**（如 `prompts/` 的 constants/data/templates 是内容分类，不是实现）**不套用**此原则。

### 11.2 命名规则

| 对象 | 规则 | 示例 |
|---|---|---|
| 类 | PascalCase | `ExecutorBackend` |
| 协议/接口 | 名词，**无 `I` 前缀**（Python 风格，不学 Java） | `ExecutorBackend`（非 `IExecutorBackend`） |
| 函数/变量/方法 | snake_case | `click_text` |
| 常量 | UPPER_SNAKE_CASE | `DEFAULT_PERSONAS` |
| 模块/文件 | snake_case，**默认无前缀** | `coords.py` |
| 私有成员/私有模块 | `_` 前缀 | `_resolve_default_provider` |

### 11.3 `_` 前缀的边界（判断标准）

**唯一判断标准：包外会不会直接 import？**

- ✅ 会 → 公共（**无前缀**）：跨实现共享的工具、对外 API
- ❌ 不会 → 私有（`_` 前缀）：类的私有方法、某个实现内部的辅助模块、模块内辅助函数

**常见误用**（禁止）：用 `_` 表达"我觉得它不重要"。`_` 是访问控制信号，不是重要性标记。

### 11.4 `__init__.py` 导出规则

- 每个包的 `__init__.py` 用 `__all__` 显式声明对外 API
- `_` 前缀的不导出
- 子包的 `__init__.py` 导出该子包的主类

### 11.5 docstring 风格

**文档化现有惯例**（全仓一致用 Google 风格，本节定为强制标准）。

- **模块级 docstring**：每个 `.py` 必须有，说明职责 + 设计要点。私有模块也要有（说明私有原因）
- **类 / 公共函数 / 公共方法**：必须有 docstring，用 **Google 风格**分段（`Args:` / `Returns:` / `Raises:`）
- **私有成员**（`_` 前缀）：可简化为一句话说明意图，不强制分段
- **语言**：中文（含中文标点），标识符在 docstring 里原样保留英文（如 `"""simple_converse 发一次对话..."""`）
- **移植代码**（`_aircv/`）：保留原 docstring 标注"抄自 ... 1.4.3，去耦版"
- **标杆实例**：`executor/base.py`、`executor/coords.py`、`perception/matching/image_matcher.py`

### 11.6 类型注解

**明确现有非 strict 配置下的分级要求**。

- **公共 API 必须标注**：协议（Protocol）、对外类（含 `__init__` 参数）、对外函数，类型注解不可省
- **私有辅助函数鼓励标注**，但不强制（mypy 非 strict，本地辅助函数可省略）
- **标准头**：每个 `.py` 顶部 `from __future__ import annotations`（已有 ~70% 文件使用，定为标准）
- **重依赖延迟**：numpy/cv2/airtest 等重栈用 `TYPE_CHECKING` 延迟导入 + 字符串别名（如 `NDArray = "numpy.ndarray"`，避免模块加载时拉起重栈）
- **mypy 配置**：`python_version = "3.12"`（对齐 venv 实际版本，numpy 2.x stub 需要）、`strict = false`、`ignore_missing_imports = true`（对 airtest/poco 等无 stub 库放行）

### 11.7 import 规范

**补全 ruff `I` 规则集的细节约定**。

- **分组顺序**（ruff isort 默认强制）：
  1. `from __future__ import annotations`（独占一组）
  2. 标准库（`os` / `sys` / `pathlib` ...）
  3. 第三方（`pydantic` / `cv2` / `numpy` / `pytest` ...）
  4. 本项目（`from joker_test.xxx import ...`）
  5. 同包相对（`from .xxx import ...`）
- 每组之间空一行，组内字母序
- **重依赖懒导入**：`cv2` / `numpy` / `airtest` / `rapidocr` 等在函数内 `import`（`# noqa: PLC0415`），避免模块加载时拉起重栈。顶层 `import cv2` 只在确实需要类型别名的 `TYPE_CHECKING` 块里
- **禁止 `from x import *`**：全仓零使用，定为禁令。所有跨包依赖必须具名 import

### 11.8 错误处理

**填补缺口，统一异常约定**。

- **异常命名**：自定义异常类用 `Error` 后缀（`QualityError`、`TemplateInputError`），继承 `Exception`（不继承 `BaseException`）
- **禁止裸 `except:`**：必须 `except SpecificError`，或至少 `except Exception`（明确不捕 `KeyboardInterrupt`/`SystemExit`）
- **跨层降级**：跨抽象层时用异常隔离 + 降级（`PerceptionEngine` / `ImageMatcher` 已示范：单算法失败降级到下一个，不让一个 provider 的异常炸掉整条链）
- **资源清理**：`connect`/`close`、文件句柄、临时目录等用 `with` 或 `try-finally`（如 `backend` fixture 的 `yield` + `close()`）

### 11.9 日志

**文档化现有惯例，统一 logger 使用**。

- **logger 命名**：每个 `.py` 顶部 `_LOGGER = logging.getLogger(__name__)`（`_` 前缀 = 私有常量）
- **级别约定**：
  - `DEBUG`：详细匹配过程、内部状态变化（如模板匹配的 confidence）
  - `INFO`：关键流程节点（如"开始探索"、"生成 N 个测试"）
  - `WARNING`：可恢复异常、降级（如"模板加载失败，跳过"、"算法异常降级"）
  - `ERROR`：不可恢复错误（如连接失败、关键资源缺失）
- **不在模块顶层配置 `basicConfig`**：日志配置由 CLI / 入口统一负责（避免多模块互相覆盖配置）
- **用户面向的进度**用 `print`，不用 logging（logging 给开发者排查用，print 给终端用户看进度）

**端到端流程跟踪（Tracer）**：logging 是运行时日志，Tracer（`src/joker_test/trace.py`）是**流程跟踪器**，记录 LLM 调用、阶段耗时、事件流，用于诊断问题和持续优化流程。

**全局模式（仿 `logging`，业务代码零参数零感知）**：
- **惰性初始化**：首次 `trace_event(...)` / `trace_stage(...)` 调用时惰性创建默认 `Tracer`，不用 `set_tracer`
- **atexit 自动收尾**：进程退出时自动写 `trace.html` + `events.jsonl` + `summary.json`（`_auto_finalize` 幂等，不抛异常）
- **默认开启**：CLI 入口默认什么都不写；`--no-trace` 才主动 `set_tracer(None)` → `_NoOpTracer` 全空操作
- **模块级函数**：业务代码直接调 `trace_event` / `trace_stage` / `trace_llm` / `trace_error` / `trace_finalize`，不 import `get_tracer`，不传参
- **TracingProvider**：包装真实 LLM provider，自动记录所有 LLM 调用（prompt/reply/耗时）到全局 tracer（见 `llm/providers/tracing.py`）。调用方 `TracingProvider(real, model)`，不传 tracer

调用方对比：
- **默认**：什么都不用写，进程结束自动产 `traces/<时间戳>_run/`
- **关闭**：`set_tracer(None)` 一行
- **想拿 summary print**：可选显式调 `trace_finalize()`（不调也不影响产物）

**产物结构**（一次运行一个目录，时间戳在前可排序）：`traces/<日期>_<时分>_<name>/{trace.html, events.jsonl, summary.json}`
- **trace.html**：单文件自包含，人看。摘要时间线 + LLM 折叠卡片（**完整 prompt/reply 内联**，不再产散文件）。浏览器直接打开，复制一个文件即可分享
- **events.jsonl**：机器读（事件流，每行一个 JSON，`grep`/`jq` 友好）。注意后缀是 `.jsonl`（JSON Lines，逐行处理），非 `.json`（整个文件一个数组）
- **summary.json**：数字摘要（耗时/事件数/LLM 调用数/错误数），CI 判断用
- **自动清理**：`Tracer(keep=20)`（默认）保留最近 20 次，超出删最旧的（只删符合 `<日期>_<时分>_<name>/` 命名格式的目录，防误删非 trace 目录）。`keep=0` 永不清理（归档重要 run）。手动清理：`python -m joker_test.trace clean --keep N`

**打点覆盖**（pipeline/各模块自动插桩，签名不动）：
- `explorer/llm_explorer.py`：`explore()` 每步决策（`explore_step` / `action_result` / `explore_end`）
- `flow/generator.py`：生成全过程（`semanticized` / `code_parsed` / `quality_ok` / `rewritten`）
- `flow/verifier.py`：试跑回喂每轮（`verify_round` / `verify_pass` / `verify_exhausted`）

**漏用检测**：`trace_stage` 是 `@contextmanager`，如果漏了 `with`（生成器被 GC 回收触发 `GeneratorExit`），`stage_end` 事件带 `clean_exit=False` 并打 warning（让漏用从静默变可见）。

### 11.10 测试规范

**填补缺口，对齐 pyproject 配置与现有测试组织**。

- **文件命名**：
  - 根级 CI 测试：`test_<被测模块>.py`（如 `test_backends_fake.py`、`test_image_matcher.py`）
  - 真机测试：`test_<被测模块>_real.py`，放 `tests/real/`（需真游戏/真 LLM，CI 跳过）
- **函数命名**：`test_<被测行为>`（如 `test_match_best_hit`、`test_click_text_miss`），一个函数只测一件事
- **pytest 不收集 `Test*` 类**（`python_classes = []`）：因为 `reporters` 的 `TestCase`/`TestResult` 等是数据模型。数据模型类必须加 `__test__ = False` 显式标记（见 `MatchResult`、`PerceptionResult`）
- **CI 友好原则**：测试不依赖真游戏/真 LLM。用 `FakeBackend` + `MockProvider` 跑通逻辑；真机测试用 `pytestmark = pytest.mark.skipif(...)` 按环境变量（`JOKER_BACKEND`）跳过
- **fixture scope**：`backend` 用 `session` scope（真游戏连接慢，整个会话共享一个实例）；测试间状态隔离靠每个测试 `wait_until` 自包含导航，不靠重新连接
- **`--strict-markers` 已开**：新增 marker 必须在 `conftest.py` 的 `pytest_configure` 注册

---

## 10. 修改记录

| 版本 | 日期 | 修改 | 修改人 |
|---|---|---|---|
| v0.1 | 2026-06-26 | 初稿，覆盖 6 个模块、4 阶段路径、7 条 ADR、10 个开放问题 | — |
| v0.2 | 2026-06-26 | 修订 ADR-002（ExecutorBackend 抽象）；4.2 节加入执行模式说明；追加 ADR-008/009 | — |
| v0.3 | 2026-06-26 | **撤销 DSL 设计**：ADR-001 改为 Python+pytest+Backend；ADR-008 改为 Python 模块 + Pydantic spec；ADR-009 撤销 | — |
| v0.4 | 2026-06-26 | **整体审阅清理**：① 清除所有 DSL 残留（2.1/2.2/3.1/3.2/4.5/4.6）② 新增 4.7 测试可视化（Reporter 插件化）③ 新增 ADR-009（TestReporter 抽象）+ ADR-010（插件提供 Reporter）④ 4.5 插件去掉 Action 扩展，加 Reporter 扩展 ⑤ 新增 5.5 Reporter 数据契约 ⑥ 阶段 2 加入 Reporter 任务 ⑦ 开放问题 Q3 删除（DSL 相关）⑧ 术语表补充 TestReporter/MultiReporter/Allure ⑨ 简化 ADR（去除冗余"撤销说明"） | — |
| v0.5 | 2026-07-06 | **新增 §11 命名与目录设计原则**：固化全仓统一的编码规范（目录组织"包根放协议+公共工具、实现进子文件夹"；命名规则；`_` 前缀边界判断标准"包外是否 import"；`__init__.py` 导出规则）。来源：M1 方案讨论中暴露的 `_coords.py` 误用 + 目录结构不一致。 | M1 执行 |
| v0.6 | 2026-07-07 | **§11 扩展为完整工程规范**：在 §11.1-11.4（命名/目录/私有/导出）基础上新增六节——§11.5 docstring（Google 风格，文档化现有惯例）+ §11.6 类型注解（非 strict 分级，`__future__` annotations 标准）+ §11.7 import（分组顺序 + 重依赖懒导入 + 禁 `import *`）+ §11.8 错误处理（异常 `Error` 后缀 + 跨层降级）+ §11.9 日志（`_LOGGER` 命名 + 级别约定）+ §11.10 测试规范（命名 + CI 友好 + marker）。配套修 4 处违规：`_load_env` 跨包私有导入改公开 `load_env`、`scr` 孤立缩写改 `screen`、`_AirtestState` 从 `__all__` 移除、pyproject 版本号差异加注释。同步 AGENTS.md 加工程规范速查（10 条一句话清单）。 | 工程规范整理 |
| v0.7 | 2026-07-07 | **文档大幅精简（1384→约 760 行，省 45%）**：① §4 模块详解删全部过时伪代码（OpenCVBackend/7-hook Reporter/class GamePlugin 等，654→168 行），降级为"架构说明+代码指针"；② §7 落地路径整节删（已被 roadmap 取代，换成指针）；③ §5 删 §5.5（reporters/base.py 已实现）+ 压 §5.2/5.3/5.4；④ §6 订正 ADR-002（OpenCV→Airtest，R-ADR-5）+ ADR-006（MCP 包装→可选增强，R-ADR-8）；⑤ §3 重画架构图去 OpenCVBackend/三层/Investigator 过时部分；⑥ §8 风险状态更新（R5/R6/R8 已解决）；⑦ §9 术语表补 UIMap/BBox/PerceptionEngine + 去掉不存在的 OpenCVBackend/AllureReporter；⑧ 同步 AGENTS.md/CLAUDE.md 过时引用。 | 文档精简 |
| v0.8 | 2026-07-09 | **Tracer 全局化 + CLI 补 record + click_coord 替代模板匹配**。① §11.9 Tracer 从"手动 TracingProvider 包装"改为"全局惰性模式"（仿 logging：模块级 `trace_event`/`trace_stage` 函数，业务代码零参数零感知，首次打点惰性建 Tracer，atexit 自动收尾，`--no-trace` 主动关闭）；补打点覆盖说明（explorer/generator/verifier 内部插桩）+ 漏用检测（GeneratorExit → clean_exit=False + warning）；补自动清理过滤（`_is_trace_dir` 正则只删 trace 目录防误删）。② §4.6 CLI 子命令补 `record`（6 个），补 `--no-trace` 说明。 | trace 全局化 |
| v0.9 | 2026-07-09 | **探索流水线落地 + 删除战术库 + 全文审查**。① 新增 `pipeline/` 包：AgenticOrchestrator 薄编排 5 Stage（Explore 智能入口含固化命中检查+三模式探索 / Solidify 固化+试跑回喂 / Execute 脱离 LLM / Report 综合报告 / Reflect 可信度+风险），借 Open-AutoGLM 循环模式保留 LLM 克制红线（ADR-011）。② 删除 `orchestrator.py`（StaticOrchestrator）/`exploratory.py`/`tactics.py`，清理 reflection 的 BugReport 依赖。③ §4.6 CLI 新增 `explore` 子命令（7 个）+ `run-all` 改走 AgenticOrchestrator + 无参数默认 run-all。④ **全文系统性审查修正**：§2.1 测试分层图（"LLM+战术脚本"→"LLM 探索流水线"）；§2.2 LLM 克制表（L2/L3/战术脚本执行→探索决策/命中检查/反思/误报审查）；§3.2 数据流重画（旧线性 Charter→Bug → 双线冒烟+pipeline DAG）；§4.4 整节重写（Investigator/Judge/Bug 为主线 → ReflectStage 四项职责为主线，Investigator 降为规划态）；§4.7 Reporter 补 ExploreReporter + ADR-009 修订（Allure 非默认）；§5.3 BugReport Schema 重定位（明确当前不产 BugReport）；ADR-004 标注规划态；§9 术语表 Judge/Tactic/Investigator 更新。 | pipeline 落地 |

<!-- 后续修改请按以下格式追加:
| v0.X | YYYY-MM-DD | <改了什么,为什么> | <谁> |
-->

---

## 附录：参考文档

- SpecOps 论文精读：`D:/AI游戏测试调研/SpecOps论文精读.md`
- SpecOps 代码精读分析：`D:/AI游戏测试调研/SpecOps代码精读分析.md`
- 游戏测试方案调研：`D:/AI游戏测试调研/游戏AI自动测试方案调研.md`
- SpecOps 原始代码：`D:/AI游戏测试调研/SpecOps-src/`
- charter_gen.py 实现：`D:/AI游戏测试调研/joker_test/charter_gen.py`

---

**文档结束** | v0.9 | 2026-07-09
