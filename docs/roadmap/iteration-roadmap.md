# joker-test 迭代方案（Roadmap）

> **文档用途**：开发 joker-test 本身的推进路线图。
> **状态**：Pre-Alpha。M0-M6 + 探索流水线已完成，当前进入"能力增强 + 真机集成"阶段。
> **维护规则**：每次讨论有变更，**立即更新本文件**并在 [§5 修改记录](#5-修改记录) 追加一条。

---

## 1. 总目标

分阶段把 joker-test 从"能跑通"推进到"**给定被测游戏，自动探索→固化→执行→报告→反思，全链路可信赖**"，并最终在你的自研游戏上稳定运行。

核心理念不变（DESIGN §2）：**LLM 用得克制**——冒烟/回归（80%）用 Python+pytest 脱离 LLM，探索式（15-20%）用 LLM 探索流水线（pipeline）。

---

## 2. 已完成能力（M0-M6 + 探索流水线）

> 以下能力均已实现并有测试覆盖（176 个测试，74 源文件，8600+ 行）。

### 2.1 能力清单

| 领域 | 能力 | 状态 | 代码 |
|---|---|---|---|
| **Charter 生成** | LLM 生成探索式测试章程（Persona 驱动 + Architect/Analyst 双 specialist） | ✅ | `charter_gen.py`、`prompts/` |
| **Backend 抽象** | ExecutorBackend 协议 + AirtestBackend（图像识别默认）+ FakeBackend（CI） | ✅ | `executor/` |
| **感知层** | PerceptionEngine 三层漏斗（OCR→图像匹配→LLM 识图），backend 无关 | ✅ | `perception/`、`ocr/` |
| **OCR** | OCRProvider 协议 + RapidOCR（onnxruntime 轻量，中文继承 PaddleOCR） | ✅ | `ocr/providers/rapidocr/` |
| **界面探索** | UIExplorer（DFS 确定性）+ LLMExplorer（agentic loop）+ UIMap 数据结构 | ✅ | `explorer/` |
| **冒烟生成** | SmokeTestGenerator（UIMap→LLM→pytest+spec）+ QualityChecker（ruff+ast） | ✅ | `generator/` |
| **操作录制** | GlobalRecorder（pynput 全局监听 + 程序化录制双输入源）+ 动态截图 | ✅ | `flow/recorder.py` |
| **固化生成** | RecordedFlowGenerator（语义化+LLM 生成 pytest）+ FlowNamer + TestCaseVerifier | ✅ | `flow/` |
| **探索流水线** | AgenticOrchestrator 薄编排 5 Stage（Explore/Solidify/Execute/Report/Reflect） | ✅ | `pipeline/` |
| **反思** | ReflectStage（可信度+风险+误报审查+卡死检测），复用 `reflection.py` | ✅ | `pipeline/stages/reflect.py`、`reflection.py` |
| **报告** | Json/Html/Multi Reporter + ExploreReporter（综合探索报告） | ✅ | `reporters/` |
| **执行** | runner.py（pytest inline plugin 收集结果）+ ExecuteStage（脱离 LLM） | ✅ | `runner.py`、`pipeline/stages/execute.py` |
| **LLM 抽象** | LLMProvider 协议 + Mock/GLM/MiMo/Anthropic/Bedrock + TracingProvider | ✅ | `llm/` |
| **插件系统** | GamePlugin Protocol + DefaultPlugin + loader | ✅ | `plugins/` |
| **CLI** | 7 子命令（explore/record/explore-ui/run-smoke/run-all/validate/generate-charter），无参数默认 run-all | ✅ | `cli.py` |
| **全局 Tracer** | 仿 logging 全局惰性模式，atexit 自动收尾，trace.html + events.jsonl | ✅ | `trace.py` |
| **真机验证** | SPD（Shattered Pixel Dungeon）全链路真机端到端（探索→生成→执行→报告） | ✅ | `scripts/e2e_*.py`、`tests/real/` |

### 2.2 架构红线（不可违背）

1. **测试分层**：Python 跑冒烟，LLM 跑探索（DESIGN §2.1）
2. **LLM 克制**：只在生成/决策/验证的高价值点用 LLM（DESIGN §2.2）
3. **插件扩展**：游戏特化逻辑走 Python 插件类（ADR-003）
4. **Backend 抽象**：ExecutorBackend 接口，AirtestBackend 默认（ADR-002）
5. **Reporter 抽象**：与 Backend 平行，MultiReporter 广播+错误隔离（ADR-009）
6. **CLI 统一契约**：编排者（人/harness/orchestrator）面向同一套 CLI（R-ADR-6）
7. **编排策略可插拔**：AgenticOrchestrator 薄编排，不抽象 Harness 层（R-ADR-7）
8. **类状态自洽**：减少相互依赖，避免网状依赖（AGENTS.md 工程规范）

---

## 3. 未来迭代规划

> 基于"探索流水线已落地"的现状，按优先级排列。每个迭代 = 独立 spec → plan → 实现 循环。

### 迭代 A：真机集成强化（优先级：高）

> **目标**：让 pipeline 在真游戏（SPD → 自研 Unity）上稳定跑通，端到端可信赖。

| 任务 | 说明 | 状态 |
|---|---|---|
| A1. pipeline 真机端到端 | `joker-test explore --backend airtest` 在 SPD 上跑完整五阶段（探索→固化→执行→报告→反思），验证固化命中检查 + 三模式探索在真机的表现 | ⬜ |
| A2. AirtestBackend 稳定性 | 解决 G7（click 后偶发空帧）、G6（截图缩放校准）已知限制，保证 pipeline 探索不因截图异常中断 | ⬜ |
| A3. 固化资产积累 | 对 SPD 核心流程（启动退出/存档/设置）跑多轮 explore 固化，积累 `generated_smoke/` 资产，验证固化命中复用 | ⬜ |
| A4. 自研 Unity 游戏接入 | 接入用户自研游戏（exe 路径 + 窗口名），验证 Poco 可选增强（D7） | ⬜（可选） |

**完成标志**：`joker-test explore --intent "SPD 启动退出" --backend airtest` 一条命令在真游戏上跑完五阶段，产出综合报告 + 可信度评分。

### 迭代 B：探索流水线增强（优先级：高）

> **目标**：提升探索的覆盖度、固化的质量、反思的准确度。

| 任务 | 说明 | 状态 |
|---|---|---|
| B1. LLMExplorer 探索深度 | 重写 LLMExplorer 为策略外壳 + ExploreStrategy 协议（ReactStateStrategy 默认 ReAct+状态机 / ConversationStrategy 对齐 Open-AutoGLM）。目标驱动(intent) + 路径回溯 + 动作空间扩展(swipe/long_press) + StateMap 改名。196 测试全绿。 | ✅ |
| B2. 固化质量提升 | RecordedFlowGenerator 的语义化准确度（OCR 匹配阈值调优）+ 试跑回喂闭环（TestCaseVerifier 接入 CLI） | ⬜ |
| B3. 反思维度扩展 | ReflectStage 加入覆盖度评估（UIMap 已探索界面/元素占比）、断言强度分析（值断言>存在性>无断言） | ⬜ |
| B4. ExploreReporter Html 可视化 | 当前只输出 Json，补 Html（探索轨迹时间线 + 阶段覆盖 + 风险列表） | ⬜ |
| B5. manual 模式增强 | record 命令与 ExploreStage(manual) 对齐，统一产物格式，支持手动录像后接入 pipeline 固化 | ⬜ |

**完成标志**：pipeline 在 FakeBackend 测试场景下，固化命中文档化场景 ≥ 80%，LLMExplorer 探索覆盖度（发现的可点击元素占比）≥ 60%。

### 迭代 C：完整三层异步引擎（优先级：中，长期）

> **目标**：从同步流水线升级到三层异步架构（DESIGN §4.3 规划态），支持实时探索。

| 任务 | 说明 | 状态 |
|---|---|---|
| C1. L1 感知层（60FPS） | YOLO/GroundingDINO 目标检测 + 物理监控（fps/memory/cpu），替代当前按需 OCR | ⬜ |
| C2. L2 决策层（本地 VLM） | 本地 VLM 做探索决策（降低延迟和成本），替代当前云端 LLM 每步调用 | ⬜ |
| C3. L3 战略层（云端 LLM） | session 总结 + Coverage gap 分析，跨 charter 共享覆盖度避免重复探索 | ⬜ |
| C4. 异步协调 | L1/L2/L3 三层异步协调（事件驱动，非当前同步串行） | ⬜ |

**完成标志**：三层引擎能以 ≥ 10 FPS 感知 + ≤ 3s 决策延迟持续探索 30 分钟，LLM 调用成本降低 ≥ 80%（相对当前每步云端调用）。

### 迭代 D：Bug 发现能力回归（优先级：中）

> **目标**：恢复 ADR-011 删除的战术库 bug 查找能力，以新形式（pipeline 集成）回归。

| 任务 | 说明 | 状态 |
|---|---|---|
| D1. 战术脚本库（新版） | 重新设计战术库（急躁鬼/贪婪者/破坏狂 Persona），作为 ExploreStage 的第四种探索模式或 ReflectStage 的风险探测 | ⬜ |
| D2. Investigator 独立验证 | ADR-005 三方案落地（默认反向 VLM），对疑似 bug 做二次验证 | ⬜ |
| D3. Meta-CoT Judge | 综合判断推理链（替代当前 ReflectStage 的轻量可信度评分） | ⬜ |
| D4. 完整 BugReport Schema | DESIGN §5.3 完整字段（evidence/investigator_report/judge_reasoning） | ⬜ |

**完成标志**：跑一个破坏狂 Charter，产出 1-3 个经 Investigator 验证的 Bug 报告 JSON（含 evidence + confidence）。

### 迭代 E：工业化包装（优先级：低）

> **目标**：让 joker-test 可被外部 harness / CI / 团队可靠集成。

| 任务 | 说明 | 状态 |
|---|---|---|
| E1. AGENTS.md 完善 | 给外部 harness 的集成指引（读指引→调 CLI，最省 token），覆盖全部 7 子命令 | ⬜ |
| E2. AllureReporter | 工业标准 Web 报告（ADR-009 规划） | ⬜ |
| E3. MCP Server（可选） | 仅在需结构化 tool 发现时实现（R-ADR-8），一个 tool 对应一个 CLI 命令 | ⬜（可选） |
| E4. CI 流水线 | GitHub Actions：pytest（FakeBackend）+ ruff + mypy，PR 检查 | ⬜ |
| E5. CoverageMapReporter | 4 维覆盖度热图（区域/功能/操作/状态） | ⬜ |

**完成标志**：外部 AI harness 读 AGENTS.md 后能正确调 CLI 完成测试任务；CI 流水线绿。

---

## 4. 关键约束与已知问题

### 4.1 开发环境

- **Python 3.12**（`.venv/`，全局 3.13 装 airtest 有 numpy 冲突）
- **numpy 版本脆弱**：airtest requirements 声明 numpy<2，但 rapidocr 需 numpy≥2。实测 airtest 1.4.3 的 aircv 在 numpy 2.5.1 + cv2 5.0.0 下可工作，属脆弱状态（G5）
- **opencv 冲突**：opencv-python 5.0 与 opencv-contrib-python 4.6 共存时，5.0 覆盖 contrib，KAZE/AKAZE/BRISK/xfeatures2d 缺失。本项目不用这些（G10）

### 4.2 AirtestBackend 已知限制

| 编号 | 问题 | 影响 |
|---|---|---|
| G6 | 截图尺寸（647×818）≠ 窗口客户区（2048×1152），有缩放 | 归一化坐标基于 screenshot 尺寸校准（已自洽） |
| G7 | 后台截图限制：前台✅/后台可见✅/完全遮挡❌白屏/最小化❌黑屏 | 游戏窗口须保持可见，click 后偶发空帧 |
| G8 | OCR 真机已验证（RapidOCR，518×738 真实画面） | 已解决 |

### 4.3 SpecOps-src 硬依赖

`charter_gen.py` 运行时硬依赖外部 `SpecOps-src`（调 AWS Bedrock）。查找顺序：`<repo>/SpecOps-src` → `<repo>/../../SpecOps-src` → `$SPECOPS_SRC`。改此依赖前看 DESIGN ADR-002。**独立模式的 LLM provider（GLM/MiMo/Anthropic）不依赖 SpecOps-src**。

---

## 5. 修改记录

| 日期 | 修改 | 来源 |
|---|---|---|
| 2026-07-03 ~ 2026-07-09 | M0-M6 + 探索流水线全部完成。历史 milestone 详解见 git log + DESIGN.md §10 修改记录。 | 多轮开发 |
| 2026-07-09 | **roadmap 重构**：从 M0-M6 线性 milestone 框架重构为"已完成能力清单 + 未来迭代规划"。已完成部分压缩为状态表（§2），未来按 5 个迭代规划（A 真机集成/B 流水线增强/C 三层引擎/D bug 发现/E 工业化）。删除过时的 M4 战术库/M5 反思/M6 StaticOrchestrator 描述（这些已被 pipeline 取代，见 DESIGN ADR-011）。§9 集成架构精简（核心结论保留，细节见 DESIGN §4.6）。 | pipeline 落地后全文审查 |

---

## 附录：集成与编排架构（精简版）

> 核心命题：**CLI 命令集是统一契约**。无论编排者是谁（人/外部 harness/内部 orchestrator），面向同一套命令，差异只在"谁决定命令序列"。

- **三种编排者**：人（shell）/ 外部 harness（读 AGENTS.md → 调 CLI）/ 内部 orchestrator（AgenticOrchestrator，进程内调 Python）
- **三种集成方式**：CLI（核心）/ AGENTS.md+CLI（主集成方式，最省 token）/ MCP Server（可选增强）
- **不抽象 Harness 层**：外部编排单向施加不可借；内部编排走 AgenticOrchestrator（pipeline 包）

详见 DESIGN.md §4.6 + R-ADR-6/7/8。
