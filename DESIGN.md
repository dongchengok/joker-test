# joker-test 架构设计文档

> **文档版本**：v1.0
> **创建日期**：2026-06-26
> **状态**：架构权威文档，模块实现细节见代码 docstring
> **关联代码**：`src/joker_test/`（5 Stage 探索流水线 + AgentPlugin 插件系统已实现）

---

## 文档使用说明

这份文档**以探索流水线 5 个 Stage + AgentPlugin 注入系统为骨架**组织：

- **要理解整体架构** → 读 §3 架构全景 + §4 探索流水线
- **要改某个 Stage** → 直接改 §4 对应子章节
- **要看支撑模块（感知/Backend/LLM）** → 读 §5 支撑子系统
- **要追加决策** → 在 §8 追加一条 ADR
- **要看编码规范** → 读 §11 工程规范

---

## 目录

1. [项目定位](#1-项目定位)
2. [核心理念](#2-核心理念)
3. [架构全景](#3-架构全景)
4. [探索流水线 + AgentPlugin](#4-探索流水线--agentplugin)
5. [支撑子系统](#5-支撑子系统)
6. [数据契约](#6-数据契约)
7. [规划态](#7-规划态)
8. [关键决策记录（ADR）](#8-关键决策记录adr)
9. [风险与开放问题](#9-风险与开放问题)
10. [术语表](#10-术语表)
11. [工程规范](#11-工程规范)
12. [修改记录](#12-修改记录)

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
| 扩展性 | 无 | **AgentPlugin 插件系统**（4 注入点 + 配置文件） |
| 集成 | 学术 prototype | **CLI 是核心契约**，MCP 可选 |

---

## 2. 核心理念

### 2.1 测试分层

```
┌──────────────────────────────────────────────────┐
│  游戏测试完整图景                                  │
├────────────────┬─────────────────────────────────┤
│ 冒烟/回归 (80%)│  探索式 (15-20%)                  │
│                │                                  │
│ Python + pytest│  LLM 探索流水线（5 Stage）        │
│ CI 每次构建跑  │  版本前/每周跑                    │
│ 找已知 bug     │  找未知 bug + 可信度评估           │
│ ¥0/次          │  ¥0.3-1/次                       │
└────────────────┴─────────────────────────────────┘
```

**关键洞察**：冒烟和探索式不是替代关系，是互补。LLM 跑冒烟是浪费（确定性需求 + 高频次），脚本跑探索做不到（找不到未知 bug）。

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

统一一套插件 Protocol——**AgentPlugin**（4 注入点），游戏特化的数据/规则/工具也通过注入点提供。详见 §4.2。

主框架保持通用，**游戏特化逻辑全部走插件**。

---

## 3. 架构全景

### 3.1 模块全景

分两层：**探索流水线**（5 Stage + AgentPlugin 横切）建立在**支撑子系统**（公共能力）之上。

```
┌─ 探索流水线 ────────────────────────────────────────────────────────┐
│                                                                      │
│   CLI 入口（统一契约，7 子命令，无参数默认 run-all）                    │
│                                                                      │
│   AgenticOrchestrator（薄编排，按分支调用 5 个 Stage）                  │
│                                                                      │
│   Explore ──► Solidify ──► Execute ──► Report ──► Reflect             │
│      │            │          │                                      │
│      │     命中复用 │    探索+固化                                    │
│      └────────────┘                                                  │
│                                                                      │
│   ═══════════ AgentPlugin 注入层（横切所有 Stage）═════════════════    │
│   系统提示词 / 每轮对话 / 动作建议 / 校验                                │
│   PluginManager（拼接 + 异常隔离）+ OCRPlugin + DefaultAgentPlugin        │
│                                                                      │
└──────────────────────────┬───────────────────────────────────────────┘
                           │
                   ┌───────┴──────────────────────────────┐
                   ▼                                      ▼
┌─ 支撑子系统 ────────────────────────┐  ┌─ 独立议题 ─────────────────┐
│                                      │  │                             │
│  ExecutorBackend    LLMProvider      │  │  Charter 生成（§5.5）        │
│  (Airtest/Fake)     (Mock/Anthropic) │  │  冒烟线（§5.6）             │
│  全局注册             SDK 对齐+trace  │  │  操作录制 flow/（§5.4）     │
│                                      │  │                             │
│  PerceptionEngine   Reporter         │  └─────────────────────────────┘
│  (OCR+图像匹配+LLM)  (Json/Html/     │
│                       Multi/Explore) │
│                                      │
│  config.py（joker-test-config.json） │
│  trace.py（状态灯+统一时间线）         │
└──────────────────────────────────────┘
```

### 3.2 数据流

两条并行数据流（冒烟线 + 探索流水线），由 CLI 统一入口：

```
【探索流水线】（pipeline，LLM 克制参与）
[测试意图] ──► Explore Stage（固化命中检查 + 三模式探索）
                   │
       ┌───────────┼───────────────┐
       ▼           ▼               ▼
   命中复用    探索+固化         探索直出
   Execute     Solidify→Execute    │
       │           │               │
       └─────┬─────┘               │
             ▼                     │
        Report Stage ◄──────────────┘  （汇聚综合报告）
             │
             ▼
        Reflect Stage（可信度+风险+误报审查）

【冒烟线】（Python 脱离 LLM，CI 高频）
[游戏元数据] → [LLM 生成 Python 测试代码] → [pytest + Backend] → Json/Html 报告
```

---

## 4. 探索流水线 + AgentPlugin

### 4.0 设计理念

探索流水线借 Open-AutoGLM 的循环模式（think-act-observe），保留 LLM 克制红线。核心设计：

1. **5 个状态自洽的 Stage**（Explore/Solidify/Execute/Report/Reflect），薄编排 `AgenticOrchestrator` 按分支调用，不形成网状依赖
2. **AgentPlugin 横切注入**（4 注入点），向探索流程提供感知/提示词/校验信息，插件可配置可扩展
3. **固化资产是持久化的**（一次固化 N 次回归，脱离 LLM）

设计详见 `docs/superpowers/specs/2026-07-09-explore-pipeline-design.md`。

### 4.1 AgenticOrchestrator（薄编排）

**职责**：按配置调用 5 个 Stage，支持智能固化命中（已固化资产复用）、三模式探索、全自动编排与独立命令。

| Stage | 职责 | 用 LLM? |
|---|---|---|
| **Explore** | 智能入口：固化命中检查 + 三模式探索，统一产 RecordedFlow | 命中检查 + llm 模式 |
| **Solidify** | 操作轨迹 → LLM 生成 pytest test_case（含试跑回喂） | 生成阶段 |
| **Execute** | 跑 pytest，脱离 LLM | 否 |
| **Report** | 汇聚各阶段产物为综合探索报告 | 否 |
| **Reflect** | 可信度评分 + 风险提示 + 卡死检测 + 误报审查 | 评分 + 审查 |

**代码**：`pipeline/base.py`（Stage Protocol + AgenticOrchestrator + build_orchestrator）、`pipeline/stages/`（5 个 Stage 实现）

### 4.2 AgentPlugin 注入系统

**核心设计**：插件定义**注入点内容**，不限定产出格式。AgentPlugin 横切所有 Stage 的探索流程。

#### 4.2.1 4 注入点 Protocol

```python
class AgentPlugin(Protocol):
    name: str

    def inject_system_prompt(self) -> str: ...        # 系统提示词（固定，1次）
    def inject_step(self, screenshot, backend, ctx) -> str: ...  # 每轮对话（动态，每步）
    def inject_action_hint(self, screenshot, backend, ctx) -> str: ...  # 动作建议（每步）
    def validate(self, decision, result) -> str | None: ...  # 校验（每步动作后）
```

| 注入点 | 调用时机 | 用途 |
|--------|---------|------|
| `inject_system_prompt` | 探索开始 1 次 | 告诉 LLM 信息格式（如 OCR 坐标格式说明） |
| `inject_step` | 每步 | 追加动态信息到 user message（如当前 OCR 文字+坐标） |
| `inject_action_hint` | 每步 | 额外操作提示（如"检测到滑块，建议用 swipe"） |
| `validate` | 每步动作后 | 检查结果，反馈注入下一步让 LLM 自我纠错 |

#### 4.2.2 PluginManager（拼接 + 异常隔离）

```python
class PluginManager:
    def build_system_prompt(self, base: str) -> str: ...
    def build_step_text(self, screenshot, backend, ctx, base, validate_feedback="") -> str: ...
    def validate(self, decision, result) -> str: ...
```

- **拼接**：所有插件的注入内容按配置顺序拼成完整文本
- **异常隔离**：每个插件调用 try/except，崩了跳过（ADR-009 同理）
- **validate 反馈**：收集所有插件的校验结果，拼到下一步 step_text

#### 4.2.3 内置插件 + 配置文件

| 名称 | 类 | 职责 |
|------|-----|------|
| `ocr` | `OCRPlugin` | 从 backend.state 提取 OCR 文字+坐标，注入每轮对话 |

配置文件 `joker-test-config.json`（自动生成，`config.py:load_config`）：首次运行自动生成，含 `plugins`/`plugin_path`/`explore_strategy`/`max_steps`/`backend_name`/`window_title`。默认 `plugins=["ocr"]`。

**代码**：`plugins/base.py`（AgentPlugin Protocol + DefaultAgentPlugin）、`plugins/manager.py`（PluginManager）、`plugins/ocr/`（OCRPlugin）、`plugins/__init__.py`（BUILTIN_PLUGINS 注册表）

### 4.3 Explore Stage

**职责**：智能入口，固化命中检查 + 三模式探索，统一产 RecordedFlow。

#### 4.3.1 固化命中检查（智能入口）

扫描已固化资产 → LLM 比对测试意图 → 命中则直接复用（跳过探索+固化），省整轮探索。显式 `--reuse` 可跳过 LLM 比对。

#### 4.3.2 三模式探索

| 模式 | 机制 | 用途 |
|------|------|------|
| `manual` | pynput 全局监听人类操作 | 人工探索录制 |
| `dfs` | UIExplorer 确定性 DFS | 界面拓扑自动遍历 |
| `llm` | LLMExplorer agentic loop | LLM 驱动探索（默认） |

#### 4.3.3 LLMExplorer + ExploreStrategy 双策略

`LLMExplorer` 是基础设施外壳（截图/感知/动作执行/录制/trace），持有可替换的 `ExploreStrategy`：

| 策略 | 设计 | 特点 |
|------|------|------|
| `ConversationStrategy`（默认） | conversation context（对齐 Open-AutoGLM） | 历史截图剥离+assistant 重封装+system 一次 |
| `ReactStateStrategy` | ReAct 思维链 + 状态机（UIMap 图+去重+回退） | 固定 token，对比基准 |

**AgentPlugin 在此接入**：策略的 `_build_step_text` / `_build_prompt` 改调 `plugin_manager.build_step_text()`，系统提示词改调 `plugin_manager.build_system_prompt()`。LLMExplorer 每步动作后调 `plugin_manager.validate()`，反馈注入下一步。

**代码**：`explorer/llm_explorer.py`（外壳）、`explorer/conversation_strategy.py`、`explorer/react_strategy.py`、`explorer/strategy.py`（Protocol + 数据契约 + EXPLORE_TOOL_SCHEMA + parse_coords）

### 4.4 Solidify Stage

**职责**：操作轨迹 → LLM 生成 pytest test_case。

- `RecordedFlowGenerator` 坐标语义化（OCR 优先 → 图像模板兜底 → 冲突 LLM 裁决）
- LLM 生成 pytest 代码 + Pydantic spec
- **试跑回喂**：生成后自动跑一次，失败则把错误信息喂回 LLM 重写（最多 N 轮）
- `QualityChecker`（ruff + ast.parse）兜底

一次固化多次回归，脱离 LLM。

**代码**：`flow/generator.py`、`flow/verifier.py`

### 4.5 Execute Stage

**职责**：跑 pytest，脱离 LLM。

`runner.py` 用 pytest inline plugin 收集 TestSession（含每个测试的 pass/fail/error + 截图 + 耗时）。

**代码**：`runner.py`、`pipeline/stages/execute.py`

### 4.6 Report Stage

**职责**：汇聚各阶段产物为综合探索报告。

**TestReporter 协议**（Protocol + `@runtime_checkable`，5 个 hook）：

| Reporter | 状态 | 用途 |
|---|---|---|
| `JsonReporter` | ✅ | 机器可读 JSON（CI 消费） |
| `HtmlReporter` | ✅ | 单 HTML 文件（本地开发） |
| `MultiReporter` | ✅ | 广播 + 错误隔离 |
| `ExploreReporter` | ✅ | 综合探索报告（汇聚 pipeline 各阶段产物） |
| AllureReporter | ❌ 规划 | 工业标准 Web 报告（见 §7） |

**代码**：`reporters/base.py`（协议 + 数据契约）、`reporters/{json,html,multi}/`、`reporters/explore.py`

### 4.7 Reflect Stage

**职责**：评估测试可信度、提示风险、过滤误报。四项职责：

- **卡死检测**（确定性，`reflection.detect_stuck_loop`）：连续 N 次操作信号相同 → 疑似卡死
- **误报审查**（LLM，`reflection.review_failure`）：判断"真 bug vs 误报（断言写错/fixture 问题/环境问题）"
- **可信度评分**（LLM）：综合断言强度/覆盖率/探索充分度给 0.0-1.0 分
- **风险提示**（LLM）：未覆盖区域/探索中断/低可信等风险项（含 severity P1/P2）

**降级策略**：LLM 调用失败时降级为低可信度（0.3）+ 确定性卡死检测，不阻断流水线。

**游戏 Bug 标准**（5 类，权威契约，供反思/审查 prompt 引用）：
- 视觉异常：穿模/UI 错位/贴图丢失/动画卡死/坐标越界
- 状态异常：数值越界（金币为负/血量超上限）/状态不同步
- 流程异常：任务无法完成/对话矛盾/流程死锁/软锁存档
- 经济异常：金币物品复制/刷分漏洞/负数套利
- 性能异常：FPS 暴跌/内存泄漏/加载超时

**代码**：`pipeline/stages/reflect.py`、`reflection.py`

### 4.8 CLI（编排 5 Stage 的统一入口）

**7 个子命令**（`cli.py`，无参数默认等价 `run-all`）：

| 子命令 | 职责 | 关键参数 |
|--------|------|---------|
| `explore` | 智能探索入口（固化命中检查 + 三模式探索） | `--strategy conversation\|react_state`、`--config`、`--mode` |
| `run-all` | AgenticOrchestrator 编排 5 Stage | `--config` |
| `generate-charter` | Charter 生成（§5.5） | — |
| `explore-ui` | 界面探索（产出 StateMap） | — |
| `run-smoke` | 跑冒烟测试 + 报告 | — |
| `validate` | 校验 charter schema + 测试语法 | — |
| `record` | 录制操作流程 → 生成 test_case | — |

`explore`/`run-all`/`explore-ui`/`record` 支持 `--no-trace` 关闭全局 trace（默认开启）。

**配置文件**（`config.py:load_config`）：首次运行自动生成 `joker-test-config.json`。

**集成方式**（ADR-006）：CLI 是核心契约，人/外部 harness/内部 orchestrator 都面向同一套 CLI。主集成 = AGENTS.md + CLI（最省 token），MCP 降为可选增强。

**代码**：`cli.py`

---

## 5. 支撑子系统

> 以下是探索流水线 5 Stage 的上下游依赖模块。每个子系统状态自洽，不互相反向引用。

### 5.1 感知层（PerceptionEngine）

**职责**：跨 backend 的界面识别，三层漏斗（LLM 克制——OCR 和 match 是预检查，LLM 是兜底）。

| 层 | 工具 | 用途 |
|---|---|---|
| OCR | RapidOCR | 文字识别（快+便宜，先跑） |
| 图像匹配 | ImageMatcher | 已知图标匹配（算法抄自 airtest aircv，backend 无关） |
| LLM 识图 | AnthropicProvider | 语义理解（慢+贵，仅 use_llm 时兜底） |

**PerceptionResult** 数据结构：`texts`（纯文字列表）+ `text_elements`（文字+中心坐标）+ `matches` + `description` + `ui_elements`。

**关键**：感知层**不依赖任何具体 backend**。任何 ExecutorBackend 的截图都能喂给它。当前 `LLMExplorer._perceive` 优先从 `backend.state` 取 OCR（已含坐标），降级用 PerceptionEngine。

**代码**：`perception/base.py`（PerceptionEngine + PerceptionResult）、`perception/matching/`（ImageMatcher + 抄自 aircv 的算法）

### 5.2 ExecutorBackend

**职责**：游戏操作引擎无关抽象。坐标契约：归一化 [0,1]，基准 = screenshot() 返回图像的尺寸（G6 自洽）。

| 实现 | 用途 |
|------|------|
| `AirtestBackend`（默认） | airtest 图像识别为核心，引擎无关 |
| `FakeBackend` | 内存模拟，CI 用 |

**全局注册**（ADR-014）：`set_active_backend()` / `get_active_backend()`（仿 trace 的 set_tracer/get_tracer 模式）。`parse_coords` 无截图时从全局 backend 获取帧尺寸，不硬编码屏幕尺寸——跨机器/跨游戏自适应。

**坐标解析**（`parse_coords`）：从实际截图 `.shape[:2]` 提取 (h,w)，三层降级（截图 → 全局 backend → 钳位）。

**find_text**：精确匹配优先，降级子串匹配（避免"设置"误匹配"显示设置"）。

**代码**：`executor/base.py`（Protocol + BBox + LazyState）、`executor/__init__.py`（全局注册）、`executor/backends/airtest/`、`executor/backends/fake/`、`executor/coords.py`

### 5.3 LLMProvider

**职责**：LLM 调用抽象，对齐 anthropic SDK 接口。

```python
class LLMProvider(Protocol):
    def create(self, *, messages, model=None, max_tokens=None, tools=None, tool_choice=None) -> Message: ...
    def parse(self, *, messages, response_model, model=None, max_tokens=None) -> Any: ...
    def stream(self, *, messages, model=None, max_tokens=None) -> Any: ...
    def count_tokens(self, *, messages) -> int: ...
```

| 实现 | 用途 |
|------|------|
| `AnthropicProvider` | 唯一真实实现，通过 base_url/model 兼容 MiMo/GLM/Claude 等任意 Anthropic 协议服务 |
| `MockProvider` | 固定回复，CI 用 |

**trace 内置**：每次 `create` 自动调 `trace_llm`（含完整 prompt_dump/reply_dump），不再有独立 TracingProvider。

**tool_use 结构化输出**：`create(tools=[...], tool_choice={"type":"tool","name":"..."})` 强制结构化输出，API 层 constrained decoding 保证格式。tool_use 未返回时自动重试 2 次。

**代码**：`llm/base.py`（Protocol + build_user_message + format_trace_messages）、`llm/providers/anthropic/`（AnthropicProvider）、`llm/providers/mock/`（MockProvider）

### 5.4 操作录制（flow 包）

**职责**："录制操作 → LLM 语义化 → test_case"闭环。

两种输入源统一走 `GlobalRecorder.record_action()`：
- **pynput 模式**：全局监听人类操作（回调冻结窗口几何 + pid 自排除 + mss 全屏动态间隔截图）
- **程序化模式**：backend 主动操作 + 录制

录制产物是目录（`flow.yaml` + `screenshots/`），经 `FlowNamer` LLM 起名 rename 后，`RecordedFlowGenerator` 坐标语义化 + LLM 生成 pytest test_case。

**代码**：`flow/recorder.py`、`flow/namer.py`、`flow/generator.py`、`flow/verifier.py`

### 5.5 Charter 生成（探索式测试入口）

**职责**：LLM 生成探索意图（不是具体步骤）。

**输入**：游戏元数据 + 被测系统列表 + Persona 库 + Heuristics 库
**输出**：Charter JSON 数组（每个 target × persona 一个独立 JSON）

**关键设计**：
- Persona 驱动（5 种玩家人格：破坏狂/贪婪者/急躁鬼/完美主义/混乱中立）
- 双 specialist 架构（Architect 生成 + Analyst 反思）
- `env_probing_required` 字段决定是否启动 Investigator（规划态）

**注意**：`charter_gen.py` 运行时依赖外部 `SpecOps-src`（调 AWS Bedrock）。独立探索流水线（pipeline）不走 SpecOps-src，用 AnthropicProvider。

**代码**：`charter_gen.py`、`prompts/`（模板已抽离到独立包）

### 5.6 冒烟线（Python + pytest + Backend）

**职责**：LLM 生成 Python 测试代码 + spec 数据，pytest 执行，**执行完全无 LLM**。

**生成链路**：`UIExplorer` 探索界面 → `SmokeTestGenerator` 调 LLM 生成代码 → `QualityChecker` 兜底 → 落盘 → `runner.py` 执行。

**代码**：`generator/`（生成）、`runner.py`（执行）、`explorer/explorer.py`（UIExplorer DFS）

---

## 6. 数据契约

### 6.1 Charter JSON Schema（已实现）

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

字段重命名契约：`write_charter` 会把 LLM 输出的 `charter_changes_game_state` 转写成 `env_probing_required`（"yes"/"no"）。**不要改字段名**。

### 6.2 测试代码规范（Python + Pydantic spec，ADR-008）

分两层：
- 测试**逻辑**：Python 代码（pytest 测试函数），LLM 生成 + 人工 review
- 测试**数据**：Pydantic 校验的 spec 文件，LLM 生成

测试代码规范详见 `examples/smoke_quality_rules.md`。

### 6.3 Reporter 数据契约

`TestSession` / `TestCase` / `TestResult` 定义在 `reporters/base.py`。`ExploreReport` 定义在 `reporters/explore.py`。

---

## 7. 规划态

> 以下是未实现的目标架构，集中在此避免污染已落地章节的 forward-reference。

### 7.1 三层异步引擎

按延迟分级用不同模型：

| 层 | 频率 | 工具 | 模型 |
|---|---|---|---|
| L1 感知 | 60 FPS | YOLO/GroundingDINO + OCR + 模板匹配 + 物理监控 | 无（纯算法） |
| L2 决策 | 1-3 秒 | 本地 VLM 决策探索方向 | 本地 VLM |
| L3 战略 | 30秒-2分 | session 总结 + Coverage gap 分析 | 云端 LLM |

### 7.2 Investigator + Meta-CoT Judge

- **Investigator**：独立验证者（反向 VLM，ADR-005），验证 Explore 产出的操作轨迹是否真的可达
- **Meta-CoT Judge**：综合判断，取代当前 ReflectStage 的轻量反思

### 7.3 BugReport Schema

```json
{
  "bug_id": string,
  "session_id": string,
  "severity": "P0 | P1 | P2",
  "category": "视觉 | 状态 | 流程 | 经济 | 性能",
  "description": string,
  "reproduction_steps": string[],
  "evidence": {"screenshots": string[], "video": string?},
  "first_seen_step": int,
  "charter_id": int?
}
```

### 7.4 GameState Schema

完整版 L1 感知输出（当前 `LazyState` 只有 `texts` + `text_elements` + `find_text`）。

### 7.5 AllureReporter + CoverageMapReporter + CharterReplayReporter

- AllureReporter：工业标准 Web 报告
- CoverageMapReporter：4 维覆盖度热图（特色）
- CharterReplayReporter：Charter 轨迹回放

### 7.6 MCP Server

可选增强（R-ADR-8），仅在需结构化 tool 发现时实现。

---

## 8. 关键决策记录（ADR）

### ADR-001：测试用 Python+pytest，不用 DSL

- **状态**：已采纳（v0.3 撤销原 DSL 设计）
- **决策**：测试逻辑用 Python（pytest），测试数据用 Pydantic spec
- **理由**：Python 表达力强，LLM 生成代码质量高，pytest 生态丰富

### ADR-002：默认 Backend = Airtest（图像识别为核心）

- **状态**：已采纳（修订：原 OpenCV-backend 改为 Airtest，见 R-ADR-5）
- **决策**：`AirtestBackend` 为默认（图像识别核心，引擎无关），Poco 为 Unity 可选增强
- **理由**：Airtest 成熟、跨平台、自带 Windows 支持

### ADR-003：插件用 Python 类，不用配置文件

- **状态**：已采纳（v1.0 修订：GamePlugin 并入 AgentPlugin，不再有独立 Protocol）
- **决策**：游戏特化逻辑用 Python 类实现 AgentPlugin（通过注入点提供数据/规则/工具），不用纯配置文件
- **理由**：校验规则和工具函数天然是代码，配置文件表达力不够
- **注意**：**插件选择列表**用配置文件（joker-test-config.json）不与此冲突——配置文件选"激活哪些插件"，插件实现本身仍是 Python 类

### ADR-004：三层异步感知-决策-战略架构

- **状态**：规划态（见 §7.1）
- **决策**：完整版按延迟分级（L1 60FPS / L2 1-3s / L3 30s-2m）
- **理由**：不同频率用不同模型，避免单层瓶颈

### ADR-005：Investigator 用反向 VLM

- **状态**：规划态（见 §7.2）
- **决策**：Investigator 默认用反向 VLM（给操作序列+截图，验证是否可达）
- **理由**：正向执行不可逆，反向验证更安全

### ADR-006：CLI 是统一契约，MCP 可选

- **状态**：已采纳
- **决策**：主集成方式 = CLI + AGENTS.md（最省 token），MCP 降为可选增强
- **理由**：CLI 通用性最强，MCP 的 tool 发现能力在 CLI + AGENTS.md 组合下非必需

### ADR-007：v0.1 不开放 Persona/Heuristics 扩展

- **状态**：已采纳
- **决策**：接口稳定性优先，Persona/Heuristics 走默认（用户改 game_metadata.json 覆盖）

### ADR-008：测试代码组织 = Python 模块 + Pydantic spec 数据

- **状态**：已采纳
- **决策**：逻辑层用 Python，数据层有 schema 校验

### ADR-009：抽象 TestReporter 接口

- **状态**：已采纳
- **决策**：Reporter 与 Backend 平行（输入侧/输出侧各一套抽象），MultiReporter 广播+错误隔离

### ADR-010：插件可以提供 Reporter

- **状态**：已采纳
- **决策**：插件接口加 `get_reporters()` 方法，允许游戏特化 reporter

### ADR-011：探索流水线取代战术库 bug 查找

- **状态**：已采纳
- **决策**：删除 `orchestrator.py`/`exploratory.py`/`tactics.py`，以 `pipeline/` 包的 5 Stage 作为统一探索流水线

### ADR-012：LLM 层简化为 AnthropicProvider + MockProvider

- **状态**：已采纳
- **决策**：删除 Bedrock/GLM/MiMo/Tracing provider，只保留 AnthropicProvider + MockProvider。trace 内置进 provider。LLMProvider 协议全面对齐 anthropic SDK（create/parse/stream/count_tokens）
- **理由**：5 个 provider 本质是同一个 Anthropic Messages API 的不同 base_url，重复代码 ~200 行

### ADR-013：AgentPlugin 注入点插件系统

- **状态**：已采纳
- **决策**：`AgentPlugin` Protocol（4 注入点）是唯一的插件接口，游戏特化的数据/规则/工具也通过注入点提供。PluginManager 拼接+异常隔离。通过配置文件配置激活插件列表
- **理由**：探索流程中"给 LLM 提供上下文信息"的逻辑硬编码在策略类里不可扩展。插件化后 OCR/图像匹配/游戏规则都可以独立注入

### ADR-014：executor 全局 backend 注册

- **状态**：已采纳
- **决策**：`executor/__init__.py` 新增 `set_active_backend()` / `get_active_backend()` 全局注册。`parse_coords` 无截图时从全局 backend 获取帧尺寸
- **理由**：backend 在一次运行中固定不变，全局注册避免逐层传参

---

## 9. 风险与开放问题

### 9.1 已识别风险

| # | 风险 | 概率 | 缓解 | 状态 |
|---|---|---|---|---|
| R1 | LLM 坐标定位不准 | 高 | OCR 文字+坐标注入 LLM + tool_use 结构化输出 | ✅ 已缓解 |
| R2 | airtest Windows 截图缩放 | 中 | 坐标基准=screenshot 尺寸，全局 backend 自适应 | ✅ 已解决 |
| R3 | SpecOps-src 硬依赖 | 中 | 独立流水线用 AnthropicProvider 不走 SpecOps | ✅ 已解决 |
| R4 | 通用 VLM 不遵守 prompt 约束 | 高 | tool_use API 层 constrained decoding | ✅ 已解决 |
| R5 | 插件间冲突 | 低 | PluginManager 异常隔离 | ✅ 已解决 |

### 9.2 开放问题

- [ ] **Q1**：Charter 优先级排序机制？
- [ ] **Q2**：跨 Charter 的 Coverage Map 共享？
- [ ] **Q3**：插件安全性？（任意 Python 代码，是否需要沙箱）
- [ ] **Q4**：插件间冲突解决？
- [ ] **Q5**：session 状态持久化格式？

---

## 10. 术语表

| 术语 | 含义 |
|---|---|
| **Charter** | 探索式测试章程（一个 30 分钟的探索目标） |
| **Persona** | 玩家人格（破坏狂/贪婪者/急躁鬼/完美主义/混乱中立） |
| **Stage** | 流水线阶段（Explore/Solidify/Execute/Report/Reflect，状态自洽） |
| **AgenticOrchestrator** | 薄编排器（按分支调用 5 个 Stage） |
| **ExploreStrategy** | 探索策略协议（ConversationStrategy 默认 / ReactStateStrategy 对比） |
| **ConversationStrategy** | conversation context 策略（对齐 Open-AutoGLM，默认策略） |
| **ReactStateStrategy** | ReAct 思维链 + 状态机驱动（固定 token，对比基准） |
| **AgentPlugin** | 唯一插件 Protocol（4 注入点：系统提示词/每轮对话/动作建议/校验）。游戏特化的数据/规则/工具也通过注入点提供 |
| **PluginManager** | 插件管理器（拼接注入内容 + 异常隔离） |
| **OCRPlugin** | 内置 OCR 插件（从 backend.state 提取文字+坐标注入每轮对话） |
| **DefaultAgentPlugin** | 默认空插件（所有注入点返回空值） |
| **ExecutorBackend** | 执行后端抽象（AirtestBackend 默认 / FakeBackend CI 用） |
| **StateMap** | 探索状态地图（Screen 集合 + Exit 边） |
| **BBox** | 归一化边界框 [0,1]（x,y,w,h，基准=screenshot 尺寸） |
| **PerceptionEngine** | 多层界面识别（OCR + 图像匹配 + LLM 识图，漏斗） |
| **LLMProvider** | LLM 调用协议（对齐 anthropic SDK：create/parse/stream/count_tokens） |
| **AnthropicProvider** | 唯一真实 LLM 实现（通过 base_url/model 兼容任意 Anthropic 协议服务） |
| **TestReporter** | 报告器抽象（Json/Html/Multi/Explore 已实现，Protocol） |
| **LazyState** | 按需解析的游戏状态代理（texts + text_elements + find_text） |
| **Investigator** | 独立验证者（❌ 规划中，见 §7.2） |
| **Allure** | 工业标准 Web 报告（❌ 规划中，见 §7.5） |
| **Spec** | Pydantic 校验的测试数据（参数化测试输入） |
| **Session** | 一次 charter 执行的会话 |

---

## 11. 工程规范

> 详见各子章节。动手写代码前必看，违规会被 review 打回。

### 11.1 目录组织

有抽象的包 = `__init__.py` + `base.py`（协议）+ `<utils>.py`（公共工具）+ `<impl>/`（每个实现一个子文件夹）。数据驱动包（如 `prompts/`）不套用。

### 11.2 命名规则

类 PascalCase / 函数变量方法 snake_case / 常量 UPPER_SNAKE_CASE / 协议无 `I` 前缀 / 文件 snake_case。

### 11.3 _ 前缀的边界

`_` 前缀**唯一标准**是"包外会不会直接 import"。会 → 无前缀；不会 → `_`。禁止用 `_` 表达"不重要"。

### 11.4 __init__.py 导出规则

每个 `__init__.py` 用 `__all__` 显式声明，`_` 前缀不导出。**禁 `from x import *`**。

### 11.5 docstring 风格

中文 + Google 风格（Args/Returns/Raises）。模块/类/公共函数必须有，私有可简化。标杆：`executor/base.py`。

### 11.6 类型注解

公共 API 必须标注，顶部加 `from __future__ import annotations`。重依赖用 `TYPE_CHECKING` 延迟导入。

### 11.7 import 规范

分组 `__future__`→标准库→第三方→本项目→同包相对，组间空行。重依赖（cv2/numpy/airtest）函数内懒导入。

### 11.8 错误处理

自定义异常用 `Error` 后缀继承 `Exception`。禁裸 `except:`。跨层用异常隔离降级。

### 11.9 日志 + Tracer

- `_LOGGER = logging.getLogger(__name__)`。不在模块顶层配 `basicConfig`。用户进度用 `print` 不用 logging。
- **全局 Tracer**（仿 logging 模式）：惰性初始化 + atexit 自动收尾。业务代码调 `trace_event`/`trace_llm`/`trace_stage` 模块级函数，零参数零感知。
- **trace 内置到 provider**：`AnthropicProvider.create` 和 `MockProvider.create` 内部直接调 `trace_llm`（含完整 prompt_dump/reply_dump）。
- **产物**：`traces/<时间戳>_run/{trace.html, events.jsonl, summary.json}`
  - `trace.html`：统一可折叠时间线——每个事件都是 `<details>` 卡片，summary 显示缩略信息+状态灯（🟢成功/🔴失败/🟡不确定/⚪中性），点击展开看完整细节。LLM 调用展开后显示完整 prompt/reply（左右并排）
  - `events.jsonl`：事件流（grep/jq 友好）
  - `summary.json`：数字摘要（CI 判断用）
- **打点覆盖**：`perceive`（含截图尺寸+OCR 坐标）/ `explore_think` / `dispatch_click` / `dispatch_swipe` / `dispatch` / `action_result` / `repeat_warning` / `plugin_inject` / `plugin_validate` / 每次 LLM `create`

### 11.10 测试规范

`test_<被测模块>.py`，真机加 `_real` 后缀放 `tests/real/`。CI 用 Fake+Mock。数据模型加 `__test__ = False`。

---

## 12. 修改记录

| 版本 | 日期 | 改了什么 | 备注 |
|---|---|---|---|
| v0.1-v0.8 | 2026-06-26 ~ 2026-07-09 | DSL 撤销 → Airtest 默认 → §11 工程规范 → 文档精简 → Tracer 全局化 → click_coord | 详见 git log |
| v0.9 | 2026-07-09 | 探索流水线落地 + 删除战术库 + 全文审查 | pipeline 落地 |
| v1.0 | 2026-07-13 | **以 5 Stage + AgentPlugin 为骨架重构全文**。① §4 重构为流水线树状结构（5 Stage 主轴 + AgentPlugin 横切注入层）；② §5 支撑子系统（感知/Backend/LLM/录制/Charter/冒烟）降为 Stage 依赖；③ §7 规划态集中（三层引擎/Investigator/Judge/BugReport/GameState/Allure/MCP）；④ ADR-012/013/014 新增；⑤ trace 状态灯+统一可折叠时间线+prompt/reply dump；⑥ OCR 文字+坐标注入 LLM；⑦ parse_coords 参数化 + executor 全局 backend 注册；⑧ LLM 层简化（Anthropic+Mock，对齐 SDK） | 5 Stage + AgentPlugin 重构 |

---

## 附录：参考文档

- SpecOps 论文精读：`D:/AI游戏测试调研/SpecOps论文精读.md`
- SpecOps 代码精读分析：`D:/AI游戏测试调研/SpecOps代码精读分析.md`
- 游戏测试方案调研：`D:/AI游戏测试调研/游戏AI自动测试方案调研.md`
- 探索流水线设计 spec：`docs/superpowers/specs/2026-07-09-explore-pipeline-design.md`
- AgentPlugin 设计 spec：`docs/superpowers/specs/2026-07-13-agent-plugin-design.md`
- 迭代路线图：`docs/roadmap/iteration-roadmap.md`

---

**文档结束** | v1.0 | 2026-07-13
