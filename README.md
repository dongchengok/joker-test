# joker-test

> AI-driven game testing platform — charter-driven exploratory testing + Python smoke testing with pluggable backends.

`joker-test` 是一个面向游戏 QA 团队的 AI 驱动测试平台。核心理念是 **"LLM 用得克制"** —— 在生成、决策、验证的高价值环节用 LLM，在执行、回归、批处理环节用脚本。

名字致敬 SpecOps（Specialist Operations）。

---

## 状态

🚧 **Pre-Alpha**：M0-M6 雏形已完成（Charter 生成、冒烟链路、UI/LLM 探索器、M4 简化探索引擎、插件系统、Reporter、CLI、PerceptionEngine、操作录制、全局 Tracer）。完整三层异步引擎 / Investigator / Meta-CoT Judge / MCP Server 为规划态。路线图见 [docs/roadmap/iteration-roadmap.md](./docs/roadmap/iteration-roadmap.md)。

| 模块 | 状态 |
|---|---|
| Charter 生成（探索式入口） | ✅ 已实现（`charter_gen.py`） |
| 冒烟测试（Python + pytest + Backend 抽象） | ✅ 已实现（`generator/` + `runner.py` + `executor/`） |
| UI 探索器（DFS + UIMap） | ✅ 已实现（`explorer/`） |
| LLM 探索器（截图理解 + 决策） | ✅ 已实现（`explorer/llm_explorer.py`） |
| M4 简化探索引擎（战术 + 确定性 Judge） | ✅ 已实现（`tactics.py` + `exploratory.py`） |
| 反思（卡死检测 + 误报审查） | ✅ 已实现（`reflection.py`） |
| 插件系统（GamePlugin Protocol） | ✅ 已实现（`plugins/`） |
| Reporter（Json/Html/Multi） | ✅ 已实现（`reporters/`） |
| PerceptionEngine（OCR + 图像匹配 + LLM 识图） | ✅ 已实现（`perception/`） |
| 操作录制→生成 test_case（flow 包） | ✅ 已实现（`flow/`） |
| CLI（6 子命令） | ✅ 已实现（`cli.py`） |
| 全局 Tracer（流程跟踪） | ✅ 已实现（`trace.py`） |
| 三层异步引擎（L1 60FPS / L2 / L3） | 🚧 规划中 |
| Investigator + Meta-CoT Judge | 🚧 规划中 |
| MCP Server | 🚧 可选增强 |
| AllureReporter / CoverageMapReporter | 🚧 规划中 |

---

## 核心理念

1. **测试分层**：冒烟测试用 Python（80%，CI 高频跑），探索式用 LLM（15-20%，找未知 bug）
2. **LLM 用得克制**：只在生成、决策、验证的高价值处用，执行不依赖 LLM
3. **插件扩展**：游戏特化逻辑全部走插件（数据/规则/工具/Reporter）
4. **可视化插件化**：Reporter 是和 Backend 平行的抽象（输入侧 / 输出侧各一套）
5. **可集成性**：CLI 核心，MCP 可选增强，可被任何 AI Harness 调用

详见 [DESIGN.md - 2. 核心理念](./DESIGN.md#2-核心理念)。

---

## 快速开始

### 前置条件

- Python 3.10+
- AWS Bedrock 访问权限（Claude 3.7 Sonnet 在 us-east-1）—— **当前 charter_gen.py 依赖 SpecOps-src 的 converse.py 调用 Bedrock**

### 安装

```bash
git clone https://github.com/dongchengok/joker-test.git
cd joker-test

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 安装本体(开发模式)
pip install -e ".[dev]"

# 当前 charter_gen.py 依赖 SpecOps-src 的 LLM 调用层,需另外 clone
git clone https://github.com/yusf1013/SpecOps.git SpecOps-src
pip install -r SpecOps-src/requirements.txt
```

### 配置 AWS 凭据

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1
```

### 生成 Charter（探索式测试章程）

```bash
# 方式 1:模块入口
python -m joker_test.charter_gen \
    examples/targets.json \
    examples/game_metadata.json \
    outputs/charters \
    --ids 1 \
    --personas 破坏狂 贪婪者 \
    --verbose

# 方式 2:pip install -e . 之后
joker-test generate-charter \
    examples/targets.json \
    examples/game_metadata.json \
    outputs/charters
```

### 输出示例

`outputs/charters/` 下会生成多个 Charter JSON：

```json
{
  "charter_id": 1,
  "target_id": 1,
  "persona": "破坏狂",
  "target_system": "铁匠铺强化",
  "goal": "找到铁匠铺强化系统的边界情况和崩溃触发条件",
  "exploration_targets": [
    "把武器强化到+10 后继续尝试强化",
    "在强化动画播放中途按 ESC",
    "组合操作:同时点击强化按钮和取消按钮"
  ],
  "heuristics": [
    "极端值:满级武器(+10)继续强化",
    "时序错乱:强化动画中途 ESC"
  ],
  "expected_behaviors": [
    "+10 满级武器强化时按钮应置灰或弹'已满级'",
    "强化动画中途 ESC 应取消操作、不消耗材料"
  ],
  "coverage_dimensions": {
    "region": ["铁匠铺 NPC", "强化界面"],
    "function": ["强化", "取消"],
    "operation": ["单击", "ESC"],
    "state": ["金币充足", "金币为0", "武器+10"]
  },
  "time_budget_minutes": 30,
  "env_probing_required": "yes",
  "severity_threshold": "P0"
}
```

---

## 仓库结构

```
joker-test/
├── DESIGN.md                       # 架构设计文档(v0.8)
├── README.md                       # 本文件
├── CLAUDE.md / AGENTS.md           # 工作指引（agent 用）
├── LICENSE                         # Apache-2.0
├── pyproject.toml                  # Python 包配置（src/ layout）
├── .gitignore
├── src/joker_test/                 # 全链路已实现（M0-M6）
│   ├── charter_gen.py              # Charter 生成（LLM）
│   ├── cli.py                      # CLI 入口（6 子命令）
│   ├── runner.py                   # pytest 执行 + 结果收集
│   ├── orchestrator.py             # StaticOrchestrator 编排
│   ├── reflection.py               # 冒烟+探索反思
│   ├── tactics.py + exploratory.py # 探索式 bug（战术+Judge+BugReport）
│   ├── trace.py                    # 全局 Tracer（流程跟踪）
│   ├── llm/                        # LLM 抽象（Mock/Bedrock/GLM/MiMo/Anthropic）
│   ├── executor/                   # Backend 抽象（Fake/Airtest）
│   ├── ocr/                        # OCR 抽象（RapidOCR）
│   ├── explorer/                   # 界面探索器（DFS + LLM 探索器）
│   ├── generator/                  # 用例生成（UIMap→pytest 代码）
│   ├── flow/                       # 操作录制→语义化→test_case
│   ├── perception/                 # 感知层（OCR+图像匹配+LLM 识图）
│   ├── reporters/                  # 报告（Json/Html/Multi）
│   ├── prompts/                    # prompt 工程化（Jinja2+XML+md/yaml）
│   └── plugins/                    # 插件系统（GamePlugin Protocol）
├── tests/                          # 测试（smoke/real/generated_smoke，CI 用 FakeBackend）
├── examples/                       # 示例数据 + 端到端示例
├── scripts/                        # 端到端脚本（e2e_traced.py 等）
├── .test-targets/SPD/             # 过渡被测游戏（Shattered Pixel Dungeon）
└── docs/roadmap/                   # 迭代路线图
```

---

## 设计文档

完整架构设计见 [DESIGN.md](./DESIGN.md)（v0.8），包含：

- 核心模块详解（Charter 生成、冒烟测试、探索引擎、报告、插件、CLI）
- 10 个 ADR（架构决策记录）
- 数据契约（Charter JSON、Bug 报告、GameState、Reporter）
- 工程规范（命名/目录/docstring/类型/import/错误处理/日志/测试）
- 风险与开放问题

---

## 概念速览

| 术语 | 含义 |
|---|---|
| **Charter** | 探索式测试章程（一个 30 分钟的探索目标） |
| **Persona** | 玩家人格（破坏狂/贪婪者/急躁鬼/完美主义/混乱中立） |
| **Heuristics** | QA 经验启发式（如"测试边界值"、"测试时序错乱"） |
| **ExecutorBackend** | 执行后端抽象（AirtestBackend 默认 / FakeBackend CI 用） |
| **PerceptionEngine** | 多层界面识别（OCR + 图像匹配 + LLM 识图，漏斗） |
| **TestReporter** | 报告器抽象（Json/Html/Multi 已实现） |
| **Tracer** | 流程跟踪器（全局惰性，记录 LLM 调用/阶段耗时/事件流） |
| **Investigator** | 独立验证者（规划中，M4 完整引擎） |
| **Judge** | 综合判断者（M4 简化版=确定性规则；完整版=Meta-CoT） |
| **Plugin** | 游戏特化扩展（数据/规则/工具/Reporter） |

完整术语表见 [DESIGN.md - 9. 术语表](./DESIGN.md#9-术语表)。

---

## 与 SpecOps 的关系

`joker-test` 借鉴了 [SpecOps (ICSE 2026)](https://arxiv.org/abs/2603.10268) 的核心架构（4 阶段 + Investigator + Meta-CoT），但有关键差异：

| 维度 | SpecOps | joker-test |
|---|---|---|
| 测试对象 | GUI-based AI Agent | 游戏（含玩家 Agent） |
| 测试范式 | feature-driven（功能验证） | **charter-driven**（探索式）+ **Python 脚本**（冒烟） |
| LLM 使用 | 全 pipeline 用 | **克制**：生成 + 关键决策处用 |
| 扩展性 | 无 | **插件系统** |
| 集成 | 学术 prototype | **CLI 核心 + MCP 可选** |

更多对比见 [DESIGN.md - 1.3 与 SpecOps 的关系](./DESIGN.md#13-与-specops-的关系)。

---

## 已知限制

1. **依赖 SpecOps-src**：`charter_gen.py` 默认 provider 复用 SpecOps 的 converse.py 调用 Bedrock。可用 `--provider mock` 或注入其他 provider（GLM/MiMo/Anthropic）绕过（参见 DESIGN.md ADR-002）。
2. **三层引擎简化**：当前是 M4 确定性简化版（战术脚本 + 确定性 Judge），完整三层异步引擎（L1 60FPS/L2/L3）+ Investigator + Meta-CoT Judge 为规划态。
3. **MCP 未实现**：MCP Server 为可选增强（R-ADR-8），主集成方式 = CLI + AGENTS.md。
4. **真机边界**：AirtestBackend 走 Win32 `PrintWindow` 截图，被完全遮挡/最小化时白屏/黑屏（G7）。

---

## 贡献

欢迎 Issue 和 PR。在开始重大改动前，请先阅读 [DESIGN.md](./DESIGN.md) 了解整体架构。

---

## 许可证

[Apache License 2.0](./LICENSE)

Copyright 2026 dongcheng
