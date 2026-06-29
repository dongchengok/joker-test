# joker-test

> AI-driven game testing platform — charter-driven exploratory testing + Python smoke testing with pluggable backends.

`joker-test` 是一个面向游戏 QA 团队的 AI 驱动测试平台。核心理念是 **"LLM 用得克制"** —— 在生成、决策、验证的高价值环节用 LLM，在执行、回归、批处理环节用脚本。

名字致敬 SpecOps（Specialist Operations）。

---

## 状态

🚧 **Pre-Alpha**：当前仓库只实现了 Phase 1（Charter 生成）。完整路线见 [DESIGN.md](./DESIGN.md) 第 7 节"落地路径"。

| 模块 | 状态 |
|---|---|
| Charter 生成（探索式入口） | ✅ 已实现（`joker_test/charter_gen.py`） |
| 冒烟测试（Python + pytest + Backend 抽象） | 🚧 待实现 |
| 三层探索引擎（L1 感知 / L2 决策 / L3 战略） | 🚧 待实现 |
| Investigator + Judge（验证与误报处理） | 🚧 待实现 |
| 插件系统 | 🚧 待实现 |
| Reporter 插件化（Allure/Html/Json） | 🚧 待实现 |
| CLI + MCP 集成 | 🚧 待实现 |

---

## 核心理念

1. **测试分层**：冒烟测试用 Python（80%，CI 高频跑），探索式用 LLM（15-20%，找未知 bug）
2. **LLM 用得克制**：只在生成、决策、验证的高价值处用，执行不依赖 LLM
3. **插件扩展**：游戏特化逻辑全部走插件（数据/规则/工具/Reporter）
4. **可视化插件化**：Reporter 是和 Backend 平行的抽象（输入侧 / 输出侧各一套）
5. **可集成性**：CLI 核心，MCP 包装，可被任何 AI Harness 调用

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
├── DESIGN.md                       # 架构设计文档(v0.4,详细)
├── README.md                       # 本文件
├── LICENSE                         # Apache-2.0
├── pyproject.toml                  # Python 包配置
├── .gitignore
├── src/
│   └── joker_test/
│       ├── __init__.py
│       └── charter_gen.py          # Charter 生成器(Phase 1)
└── examples/
    ├── game_metadata.json          # 示例:虚构 ARPG 的元数据
    └── targets.json                # 示例:被测系统列表
```

---

## 设计文档

完整架构设计见 [DESIGN.md](./DESIGN.md)（v0.4，约 1100 行），包含：

- 6 个核心模块的详细设计
- 10 个 ADR（架构决策记录）
- 4 阶段落地路径
- 数据契约（Charter JSON、Bug 报告、GameState、Reporter）
- 风险与开放问题

---

## 概念速览

| 术语 | 含义 |
|---|---|
| **Charter** | 探索式测试章程（一个 30 分钟的探索目标） |
| **Persona** | 玩家人格（破坏狂/贪婪者/急躁鬼/完美主义/混乱中立） |
| **Heuristics** | QA 经验启发式（如"测试边界值"、"测试时序错乱"） |
| **ExecutorBackend** | 执行后端抽象（OpenCVBackend / AirtestBackend 等） |
| **TestReporter** | 报告器抽象（AllureReporter / HtmlReporter 等） |
| **Investigator** | 独立验证者（不信任 subject agent 自报告） |
| **Judge** | 综合判断者（Meta-CoT + 规则校验） |
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
| 集成 | 学术 prototype | **CLI + MCP** |

更多对比见 [DESIGN.md - 1.3 与 SpecOps 的关系](./DESIGN.md#13-与-specops-的关系)。

---

## 已知限制

1. **依赖 SpecOps-src**：当前 `charter_gen.py` 复用 SpecOps 的 converse.py 调用 Bedrock。后续会改成可选（参见 DESIGN.md ADR-002）。
2. **Bedrock 锁定**：依赖 AWS Bedrock。未来会支持 Anthropic 原生 SDK、OpenAI、本地 VLM。
3. **只有 Phase 1**：完整流程（Charter 执行、Investigator、Judge）尚未实现。

---

## 贡献

欢迎 Issue 和 PR。在开始重大改动前，请先阅读 [DESIGN.md](./DESIGN.md) 了解整体架构。

---

## 许可证

[Apache License 2.0](./LICENSE)

Copyright 2026 dongcheng
