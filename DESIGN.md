# joker-test 架构设计文档

> **文档版本**：v0.4
> **创建日期**：2026-06-26
> **状态**：草案，欢迎频繁修改
> **关联代码**：`charter_gen.py`（Phase 1 已实现）、`SpecOps-src/`（参考实现）

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
│ Python + pytest│  LLM + 战术脚本                   │
│ CI 每次构建跑  │  版本前/每周跑                    │
│ 找已知 bug     │  找未知 bug                       │
│ ¥0/次          │  ¥0.3-1/次                       │
└────────────────┴─────────────────────────────────┘
```

**关键洞察**：**冒烟和探索式不是替代关系，是互补**。LLM 跑冒烟是浪费（确定性需求 + 高频次），脚本跑探索做不到（找不到未知 bug）。

### 2.2 LLM 克制使用

| 场景 | 用 LLM? | 原因 |
|---|---|---|
| Charter 生成（探索意图） | ✅ | 需要创意和领域知识 |
| 冒烟 Python 测试代码生成 | ✅ | 把需求转代码 |
| L2 决策（探索中） | ✅ | 非确定性判断 |
| L3 战略反思 | ✅ | 综合分析 |
| Bug Judge | ✅ | 语义判断 |
| 冒烟测试执行 | ❌ | 需要确定性 + 高频 |
| L1 实时感知 | ❌ | 延迟要求高（60 FPS） |
| 战术脚本执行 | ❌ | 毫秒级反应 |
| 数据合法性校验 | ❌ | 规则化、可枚举 |

### 2.3 插件扩展性

每个游戏可以有专属插件，提供：
- **数据源**（NPC/物品/任务的合法值）
- **校验规则**（数值边界、状态约束）
- **工具**（内存读取、控制台命令）
- **Reporter**（游戏特有可视化，如经济平衡图）

主框架保持通用，**游戏特化逻辑全部走插件**。

### 2.4 可集成性

- **CLI 是核心**：所有功能可通过命令行调用
- **MCP 是包装**：一个 MCP tool 对应一个 CLI 命令
- **可被任何 AI Harness 调用**：Claude Code、Cursor、自研 Agent

### 2.5 可视化插件化

- **Reporter 是和 Backend 平行的抽象**（输入侧 / 输出侧各一套）
- 内置 5 个 reporter（Allure / Html / Json / CoverageMap / CharterReplay）
- 用户可自研 reporter，通过配置启用
- 多个 reporter 可同时跑（MultiReporter 广播）

---

## 3. 整体架构

### 3.1 模块全景

```
                    ┌──────────────────────────────────────┐
                    │  CLI 入口 / MCP Server               │
                    │  (暴露给 AI Harness: Claude Code,    │
                    │   Cursor, 自研 Agent 等)             │
                    └──────────────────────────────────────┘
                                    │
                ┌───────────────────┼───────────────────┐
                │                   │                   │
            ┌───▼───┐           ┌───▼───┐           ┌───▼────┐
            │ 生成  │           │ 执行  │           │ 报告   │
            └───────┘           └───────┘           └────────┘
                │                   │                   │
   ┌────────────┼────────────┐      │                   │
   │            │            │      │              ┌────▼─────┐
┌──▼──┐    ┌────▼─────┐ ┌────▼───┐  │              │ Reporter │
│Charter│   │冒烟 Python│ │数据表 │  │              │ 插件化   │
│生成  │   │代码生成   │ │插件   │  │              │ (Allure/ │
│(LLM) │   │(LLM 生成, │ └────▼───┘  │              │  Html/   │
└──┬───┘   │ 执行无 LLM)│     │      │              │  Json/   │
   │       └─────┬─────┘     │      │              │  Coverage│
   │             │           │      │              │  /Replay)│
   │             ▼           │      │              └────┬─────┘
   │       ┌──────────┐     │      │                   │
   │       │ pytest   │     │      │                   │
   │       │ +Backend │     │      │                   │
   │       └──────────┘     │      │                   │
   │                        │      │              ┌────▼────┐
   │            ┌───────────┴──────┴──────┐       │Investiga│
   │            │  探索式测试(三层架构)    │       │tor 验证 │
   │            │  ┌────────────────────┐ │       │(独立查) │
   └───────────►│  │ L1 感知 (60FPS)    │ │       └────┬────┘
                │  │ YOLO+OCR+模板匹配  │ │            │
                │  ├────────────────────┤ │       ┌────▼────┐
                │  │ L2 决策 (1-3s)     │ │       │ Judge   │
                │  │ 本地VLM+战术脚本库 │ │       │(Meta-CoT│
                │  ├────────────────────┤ │       │ +规则校验│
                │  │ L3 战略 (30s-2min) │ │       │ +插件)  │
                │  │ 云端 LLM 反思      │ │       └─────────┘
                │  └────────────────────┘ │
                └─────────────────────────┘
```

### 3.2 数据流

```
[游戏元数据]            [插件]
    │                    │
    ▼                    ▼
[Charter 生成]      [数据表+规则+工具+Reporter]
    │                    │
    ├─────────────┬──────┘
    │             │
    ▼             ▼
[Charter JSON]  [Python 测试代码] ← 由 LLM 分别生成
    │             │
    │             ▼
    │        [pytest + Backend]──[CI/CD]──→ Allure/Html 报告
    │             │
    │             ▼
    │           测试结果
    │
    ▼
[三层探索引擎]
    │
    ├─ L1 感知(60FPS)
    ├─ L2 决策(1-3s)→ 战术脚本库(毫秒级执行)
    └─ L3 战略(30s-2min)
                │
                ▼
        [异常事件流]
                │
                ▼
      [Investigator 独立验证]
                │
                ▼
       [Judge 综合]
                │
                ▼
        [Bug 报告 JSON]
                │
                ▼
        [CLI/MCP/Reporter 输出]
```

---

## 4. 模块详解

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

**当前状态**：✅ `charter_gen.py` 已实现

**潜在改进**：
- ⚠️ Charter 优先级排序（5 系统 × 5 Persona = 25 个，需要排优先级）
- ⚠️ 跨 Charter 的 Coverage Map 共享（避免重复探索）

**关联文件**：`joker_test/charter_gen.py`、`joker_test/examples/*.json`

---

### 4.2 冒烟测试（Python + pytest + Backend）

**职责**：LLM 生成 Python 测试代码 + spec 数据，pytest 执行，**执行完全无 LLM**。

**核心设计**：
- 测试**逻辑** = Python 代码（pytest 测试函数）
- 测试**数据** = Pydantic spec（JSON/YAML）
- 测试**执行** = pytest + ExecutorBackend

**Python 测试代码示例**（LLM 生成）：

```python
# tests/test_inventory_smoke.py
"""背包系统冒烟测试 (LLM 生成)"""
import pytest
from joker_test.executor.backends import OpenCVBackend
from joker_test.plugins import get_plugin

@pytest.fixture
def backend():
    b = OpenCVBackend(window_title="Example ARPG")
    b.connect()
    yield b
    b.close()

@pytest.fixture
def game_state(backend):
    return get_plugin("example_arpg").create_state_reader(backend)

def test_open_close_inventory(backend):
    """测试 1:开关背包"""
    backend.press_key("i")
    assert backend.wait_until(lambda: backend.state.inventory_open, timeout=3)
    backend.press_key("escape")
    assert backend.wait_until(lambda: not backend.state.inventory_open, timeout=3)

def test_continuous_open_close_memory(backend, game_state):
    """测试:连续开关 20 次检测内存泄漏"""
    initial_mem = game_state.memory_mb
    for _ in range(20):
        backend.press_key("i")
        backend.press_key("escape")
    final_mem = game_state.memory_mb
    assert final_mem - initial_mem < 100, f"内存泄漏 {final_mem - initial_mem}MB"
```

**Pydantic spec 数据示例**（参数化测试）：

```python
# tests/specs/inventory_smoke_spec.py
from pydantic import BaseModel
from typing import Literal

class InventorySmokeSpec(BaseModel):
    target_save: str
    expected_initial_items: list[str]
    severity: Literal["P0", "P1", "P2"] = "P1"

SPECS = [
    InventorySmokeSpec(target_save="chapter1_start", expected_initial_items=["村长剑"]),
    InventorySmokeSpec(target_save="mid_game", expected_initial_items=["铁剑+5"]),
]

@pytest.mark.parametrize("spec", SPECS)
def test_inventory_with_spec(backend, spec):
    backend.load_save(spec.target_save)
    for item in spec.expected_items:
        assert backend.inventory_has(item)
```

**Backend 抽象**：

```
Python 测试代码
    ↓
ExecutorBackend 接口
    ↓
┌─────────────────┬──────────────────┬──────────────┐
│ AirtestBackend  │ OpenCVBackend    │ 自研 Backend │
│ (跨平台,可选)    │ (默认,Windows)   │ (未来扩展)   │
└─────────────────┴──────────────────┴──────────────┘
```

**Backend 接口要点**：
- `press_key / click_text / click_image / type_text / screenshot / wait_until`
- `state` 属性（LazyState 代理，按需解析）
- 每个 Backend 必须线程安全（为 v0.2 并行测试预留）

**CI 集成**：直接用 pytest：

```bash
pytest tests/smoke/ --game=example_arpg --backend=opencv --reporters=allure
```

**潜在风险**：
- ⚠️ LLM 生成的 Python 代码质量（用 pytest 跑一遍 + mypy 类型检查兜底）
- ⚠️ 测试代码依赖 Backend API（Backend 接口变更要批量改测试，IDE 重构可缓解）

**关联文件**：`joker_test/executor/backends/`、`tests/smoke/`（待实现）

---

### 4.3 三层探索引擎

**职责**：执行 Charter，30 分钟内自由探索找茬。

**核心改进**（相对 SpecOps 单线程）：用**战术脚本库**解决"急躁鬼快速反应"问题。

#### L1 感知层（60 FPS）

| 工具 | 用途 | 速度 |
|---|---|---|
| YOLO/RT-DETR/GroundingDINO | 实时 UI 元素检测 | 5-15ms |
| OCR (PaddleOCR) | 数值文本识别 | 30ms |
| 模板匹配 (cv2.matchTemplate) | 已知图标定位 | 1-5ms |
| 物理规则监控 | FPS/内存/坐标越界 | <1ms |

**输出**：每帧的 `GameState` 快照（detections + texts + 物理指标）

#### L2 决策层（1-3 秒）

**输入**：当前 GameState + Charter context + Coverage Map
**输出**：决策 JSON

```json
{
  "decision_type": "apply_tactic",
  "tactic": "rapid_esc",
  "params": {"duration_seconds": 10},
  "reasoning": "对话开始,急躁鬼应跳过",
  "coverage_target": "operation.esc_skip"
}
```

**模型**：本地 VLM（Qwen2.5-VL-7B/32B），省 API 成本。

**关键**：决策"用哪个战术"，**不直接操作**。战术脚本执行具体动作。

#### 战术脚本库（毫秒级执行）

```python
# joker_test/tactics/registry.py
TACTICS = {
    "rapid_esc": rapid_esc_loop,              # 急躁鬼: 快速 ESC 循环
    "instant_click_on_popup": instant_click,  # 急躁鬼: UI 弹窗瞬间点击
    "save_load_loop": save_load_loop,         # 贪婪者: 保存读档循环
    "boundary_value_test": boundary_test,     # 贪婪者: 边界值测试
    "extreme_combo": extreme_combo,           # 破坏狂: 极端组合输入
    # ... 每个 Persona 一组
}
```

**LLM 1 次决策 → 脚本执行 100 次操作**，省 99% LLM 调用。

#### L3 战略层（30 秒-2 分钟）

**输入**：session 总结 + Coverage Map + 已发现的异常
**输出**：策略调整

```json
{
  "reflection": "对话跳过战术覆盖了 80%,但状态穿越类没探索",
  "next_focus": "status_traversal",
  "coverage_gap": ["state.during_animation", "state.ui_popup"]
}
```

**模型**：云端 LLM（Claude Sonnet / GPT-4o），关键判断兜底。

#### 三层之间的通信

```
L1 → L2: event trigger (状态变化时唤醒 L2)
L2 → L1: action commands (通过战术脚本注入)
L3 → L2: strategy update (调整决策 prompt)
L2 → L3: summary trigger (周期性总结)
```

**关联文件**：`joker_test/explorer/`（待实现）

---

### 4.4 报告与误报处理

**职责**：验证异常、生成 Bug 报告、防误报。

继承 SpecOps Phase 4 的核心结构（Investigator + Judge + Meta-CoT），增加游戏特化能力。

#### 4.4.1 异常事件触发

```python
TRIGGER_CONDITIONS = [
    physical_rule_violation,        # FPS<30, 内存>2GB, 坐标越界
    state_deviation_from_baseline,  # 偏离 expected_behaviors
    plugin_rule_violation,          # 插件规则校验失败
    vlm_anomaly_detection,          # VLM 判断"不正常"
]
```

#### 4.4.2 Investigator 独立验证（三方案可选）

| 方案 | 精度 | 侵入性 | 通用性 | 推荐 |
|---|---|---|---|---|
| 内存读取（插件） | 100% | 高 | 低（每游戏适配） | 关键场景 |
| 反向 VLM Agent | 中 | 低 | 高 | **默认方案** |
| 游戏控制台（插件） | 高 | 中 | 中 | 游戏支持时 |

**默认走 VLM Agent**，内存读取和控制台作为可选插件增强。

#### 4.4.3 Judge 综合判断

```python
class Judge:
    def __init__(self, plugins):
        self.plugins = plugins
    
    def judge(self, evidence):
        # Step 1: 插件规则校验 (确定性,优先级最高)
        rule_violations = self.check_plugin_rules(evidence)
        
        # Step 2: Meta-CoT (LLM 生成问题→回答)
        questions = self.generate_cot_questions(evidence)
        answers = self.answer_questions(evidence, questions)
        
        # Step 3: 综合 5 条 bug 标准 (游戏版)
        bug_decision = self.apply_bug_standards(answers, rule_violations)
        return bug_decision
```

#### 4.4.4 游戏 Bug 标准

```python
GAME_BUG_STANDARDS = [
    "视觉异常:穿模/UI 错位/贴图丢失/动画卡死/坐标越界",
    "状态异常:数值越界(金币为负/血量超上限)/状态不同步",
    "流程异常:任务无法完成/对话矛盾/流程死锁/软锁存档",
    "经济异常:金币物品复制/刷分漏洞/负数套利",
    "性能异常:FPS 暴跌/内存泄漏/加载超时",
    
    # 防过度报
    "agent 不是千里眼:bug 必须 prompt 和环境可观察下可避免",
    "体验建议不算 bug:细节优化走另外的报告渠道",
]
```

#### 4.4.5 Bug 报告 Schema

详见 [5.3 Bug 报告 Schema](#53-bug-报告-schema待实现)。

**关联文件**：`joker_test/judge/`（待实现）

---

### 4.5 插件系统

**职责**：让每个游戏可以有专属扩展。

#### 4.5.1 插件能提供什么

| 类型 | 提供什么 | 例子 |
|---|---|---|
| 数据源 | 游戏数据表 schema | 所有 NPC/武器/任务的合法值 |
| 校验规则 | 自定义 Bug 检测 | "金币不能为负" |
| 工具 | 自定义操作工具 | 内存读取、控制台命令 |
| Reporter | 自定义可视化 | 经济平衡图、战斗统计图 |

#### 4.5.2 插件接口（v0.1 最小集）

```python
# joker_test/plugins/base.py
from typing import Callable
from pydantic import BaseModel

class PluginDataSchema(BaseModel):
    name: str
    schema: dict
    data: list[dict]

class ValidationRule(BaseModel):
    name: str
    description: str
    check: Callable[[GameState], bool]
    severity: str = "P1"

class GamePlugin:
    """所有插件的基类。"""
    
    name: str
    version: str = "0.1.0"
    
    def get_data_schemas(self) -> list[PluginDataSchema]:
        return []
    
    def get_validation_rules(self) -> list[ValidationRule]:
        return []
    
    def get_tools(self) -> dict[str, Callable]:
        return {}
    
    def get_reporters(self) -> list:  # 返回 list[TestReporter]
        return []
```

**v0.1 故意不开放 Persona / Heuristics 扩展**（接口稳定性优先）。

#### 4.5.3 加载机制

```python
# joker_test/plugins/loader.py
def load_plugins(plugin_dir: Path) -> list[GamePlugin]:
    """自动发现并加载插件(类似 pytest 插件机制)"""
    plugins = []
    for py_file in plugin_dir.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        module = importlib.import_module(py_file.stem)
        for attr in dir(module):
            obj = getattr(module, attr)
            if (isinstance(obj, type) 
                and issubclass(obj, GamePlugin) 
                and obj is not GamePlugin):
                plugins.append(obj())
    return plugins
```

#### 4.5.4 插件示例

```python
# plugins/example_arpg.py
class ExampleARPGPlugin(GamePlugin):
    name = "example_arpg"
    version = "1.0.0"
    
    def get_data_schemas(self):
        return [
            PluginDataSchema(
                name="weapons",
                schema={"type": "object", "properties": {...}},
                data=load_weapon_table(),
            ),
        ]
    
    def get_validation_rules(self):
        return [
            ValidationRule(
                name="gold_non_negative",
                description="金币不能为负",
                check=lambda state: state.gold >= 0,
                severity="P0",
            ),
        ]
    
    def get_tools(self):
        return {
            "read_memory": self._read_memory,
            "console_command": self._console_command,
        }
    
    def get_reporters(self):
        from joker_test.reporters.base import TestReporter
        return [EconomyBalanceReporter()]   # 游戏特有可视化
```

**关联文件**：`joker_test/plugins/`（待实现）

---

### 4.6 CLI + MCP 集成

**职责**：让 joker-test 可被任何 AI Harness 调用。

#### 4.6.1 CLI 命令设计

```bash
# 生成
joker-test generate charter \
    --game example_arpg \
    --targets 铁匠铺 \
    --personas 破坏狂 贪婪者

joker-test generate smoke \
    --game example_arpg \
    --targets 主线任务 \
    --output tests/smoke/

# 执行
joker-test run charter T01_C01_破坏狂_铁匠铺.json [--async]
joker-test run smoke tests/smoke/test_inventory.py
joker-test run all --game example_arpg --type exploratory --limit 5

# 查询
joker-test status <session_id>
joker-test list sessions [--game example_arpg]

# 报告(详见 4.7)
joker-test report <session_id> --reporters allure,html
joker-test report <session_id> --bugs-only --severity P0

# 校验
joker-test validate charter T01_C01_破坏狂_铁匠铺.json
joker-test validate smoke tests/smoke/test_inventory.py

# 插件
joker-test plugin list
joker-test plugin info example_arpg
```

#### 4.6.2 MCP Server 设计

```python
# joker_test/mcp/server.py
from mcp.server import Server

server = Server("joker-test")

@server.tool()
async def generate_charters(game: str, targets: list[str], personas: list[str]) -> list[dict]:
    """生成探索式测试章程"""
    ...

@server.tool()
async def run_charter_async(charter_id: str) -> str:
    """异步执行,返回 session_id"""
    ...

@server.tool()
async def get_session_status(session_id: str) -> dict:
    """查询异步执行状态"""
    ...

@server.tool()
async def get_bug_report(session_id: str) -> dict:
    """获取 bug 报告"""
    ...
```

#### 4.6.3 AI Harness 调用示例

```
用户: "帮我测一下 example_arpg 的铁匠铺强化系统"
Claude Code → MCP → joker_test.generate_charters(...) 
         → 拿到 charter 列表 → 给用户看
         → 用户选一个 → MCP → joker_test.run_charter_async(...)
         → 拿到 session_id → MCP → joker_test.get_bug_report(...)
         → 给用户看 bug
```

**关联文件**：`joker_test/cli/`、`joker_test/mcp/`（待实现）

---

### 4.7 测试可视化（Reporter 插件化）

**职责**：把"测试做了什么、结果如何、为什么失败"用人类能看懂的方式呈现。

**核心设计**：Reporter 是和 Backend 平行的抽象 —— Backend 管"操作游戏"，Reporter 管"呈现结果"。两者互不耦合。

#### 4.7.1 TestReporter 接口

```python
# joker_test/reporters/base.py
class TestReporter(ABC):
    """所有 reporter 的基类。"""
    
    name: str
    
    @abstractmethod
    def on_session_start(self, session: TestSession) -> None: ...
    
    @abstractmethod
    def on_test_start(self, test: TestCase) -> None: ...
    
    @abstractmethod
    def on_step(self, test: TestCase, step: TestStep) -> None: ...
    
    @abstractmethod
    def on_attachment(self, test_name: str, attachment: Attachment) -> None: ...
    
    @abstractmethod
    def on_test_end(self, result: TestResult) -> None: ...
    
    @abstractmethod
    def on_session_end(self, session: TestSession) -> None: ...
    
    @abstractmethod
    def finalize(self) -> str:
        """生成报告,返回路径或 URL"""
```

#### 4.7.2 内置 5 个 Reporter

| Reporter | 用途 | 典型场景 |
|---|---|---|
| **AllureReporter**（默认） | 工业标准 Web 报告 + 历史趋势 + 附件管理 | CI / 团队 server |
| **HtmlReporter** | 单 HTML 文件 | 本地开发 |
| **JsonReporter** | 机器可读 JSON | 给 CI 系统消费 |
| **CoverageMapReporter** | 4 维覆盖度热图（**joker-test 特色**） | 探索式测试 |
| **CharterReplayReporter** | Charter 30 分钟轨迹回放（15x 加速） | 探索式测试归档 |

#### 4.7.3 MultiReporter：组合多个 reporter 同时跑

```python
class MultiReporter(TestReporter):
    """组合多个 reporter,事件广播。"""
    
    def __init__(self, reporters: list[TestReporter]):
        self.reporters = reporters
    
    def on_test_start(self, test):
        for r in self.reporters:
            try:
                r.on_test_start(test)
            except Exception as e:
                logger.warning(f"Reporter {r.name} 失败: {e}")   # 错误隔离
    
    # ... 其他方法同理
    
    def finalize(self) -> str:
        paths = []
        for r in self.reporters:
            path = r.finalize()
            paths.append(f"  • {r.name}: {path}")
        return "\n".join(paths)
```

**关键设计**：**错误隔离**（一个 reporter 崩了不影响其他）。

#### 4.7.4 配置方式

```yaml
# joker-test.yaml
reporters:
  - name: allure
    enabled: true
    config:
      results_dir: allure-results
  - name: json
    enabled: true
    config:
      output: test-results.json
  - name: coverage_map
    enabled: false   # 按需开启
```

CLI：

```bash
# 启用多个
joker-test run smoke tests/ --reporters allure,json

# 禁用所有(只输出控制台)
joker-test run smoke tests/ --no-reports
```

#### 4.7.5 典型场景下的 Reporter 组合

| 场景 | 推荐 reporter 组合 |
|---|---|
| 本地开发调试 | html |
| CI 跑冒烟 | allure + json |
| 团队 server 部署 | allure + coverage_map |
| 探索式 charter 跑 | allure + charter_replay + coverage |
| Bug 复现归档 | json + charter_replay |

#### 4.7.6 Allure 集成要点

```python
# tests/conftest.py
import allure

@pytest.fixture
def backend():
    b = OpenCVBackend(window_title="Example ARPG")
    b.connect()
    
    # 钩子:每次操作自动截图给 Allure
    original_click = b.click_text
    def wrapped_click(text):
        result = original_click(text)
        allure.attach(b.screenshot(), name=f"点击: {text}", 
                      attachment_type=allure.attachment_type.PNG)
        return result
    b.click_text = wrapped_click
    
    yield b
    b.close()

@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    
    # 测试失败时自动附加完整状态
    if report.failed and "backend" in item.funcargs:
        backend = item.funcargs["backend"]
        allure.attach(str(backend.state.dict()), 
                      name="失败时的 GameState",
                      attachment_type=allure.attachment_type.TEXT)
```

**关联文件**：`joker_test/reporters/`（待实现）

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

### 5.2 测试代码规范（Python + Pydantic spec）

**测试逻辑层**（Python 代码）：
- 文件位置：`tests/smoke/test_<system>.py`
- 命名规范：`test_<测试目标>_<测试点>`（如 `test_inventory_open_close`）
- 必须用 pytest fixture（`backend` / `game_state`）
- 一个测试函数只测一件事

**测试数据层**（Pydantic spec）：
- 文件位置：`tests/specs/<system>_spec.py` 或 `.json` / `.yaml`
- 用 Pydantic BaseModel 定义
- 通过 pytest 参数化（`@pytest.mark.parametrize`）注入

**示例**：

```python
# tests/specs/inventory_spec.py
from pydantic import BaseModel, Field
from typing import Literal

class InventoryTestSpec(BaseModel):
    name: str = Field(..., min_length=3)
    target_save: str
    expected_items: list[str] = Field(..., min_items=1)
    severity: Literal["P0", "P1", "P2"] = "P1"
```

**关联文件**：`joker_test/specs/`、`tests/smoke/`（待实现）

### 5.3 Bug 报告 Schema（待实现）

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

**关联文件**：`joker_test/judge/schema.py`（待实现）

### 5.4 GameState Schema（运行时状态）

```python
class GameState(BaseModel):
    """L1 感知层输出的状态快照"""
    timestamp: float
    frame: bytes
    
    detections: list[Detection]           # YOLO 检测结果
    texts: list[TextRecognition]          # OCR 结果
    
    fps: float
    memory_mb: float
    cpu_percent: float
    
    player_state: dict | None             # 玩家位置/血量/buff
    inventory: list | None
    quest_log: list | None
```

**关联文件**：`joker_test/explorer/state.py`（待实现）

### 5.5 Reporter 数据契约

```python
@dataclass
class TestSession:
    id: str
    started_at: datetime
    game: str
    backend: str
    tests: list[TestCase]

@dataclass
class TestCase:
    name: str
    module: str
    tags: list[str]
    severity: str
    steps: list[TestStep]

@dataclass
class TestResult:
    test: TestCase
    status: TestStatus    # passed/failed/skipped/error
    duration: float
    error: str | None
    attachments: list[Attachment]

@dataclass
class Attachment:
    name: str
    data: bytes | str
    type: Literal["png", "jpg", "mp4", "json", "text", "html"]
```

**关联文件**：`joker_test/reporters/base.py`（待实现）

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

### ADR-002：抽象 ExecutorBackend 接口，Airtest 是可选 backend

- **状态**：已采纳
- **决策**：抽象 `ExecutorBackend` 接口，提供至少 2 个实现：
  - `OpenCVBackend`（默认，cv2 + pyautogui + easyocr，Windows 友好）
  - `AirtestBackend`（可选，airtest + pocoui，跨平台 + Poco UI 树）
- **理由**：不被任何框架锁定；默认依赖最小；用户按需选 backend。
- **后果**：
  - ✅ 主体是 joker-test，Airtest 是插件
  - ❌ 需要维护多个 backend 实现

### ADR-003：插件用 Python 类，不用配置文件

- **状态**：已采纳
- **决策**：用 Python 类。
- **理由**：校验规则天然是代码（lambda/函数）；工具函数必须是代码；配置文件表达力不够。
- **后果**：
  - ✅ 表达力强，类型提示友好
  - ❌ 写插件门槛高（需要懂 Python）

### ADR-004：三层架构而非两层

- **状态**：已采纳
- **决策**：三层异步架构（L1 感知 60FPS / L2 决策 1-3s / L3 战略 30s-2min）。
- **理由**：游戏实时性要求高；不同延迟层级用不同模型；战术脚本独立执行。
- **后果**：
  - ✅ 延迟可控，成本优化（80% 用本地模型）
  - ❌ 架构复杂度增加

### ADR-005：默认 Investigator 用反向 VLM，不用内存读取

- **状态**：已采纳
- **决策**：默认反向 VLM，内存读取作为可选插件。
- **理由**：反向 VLM 通用（跨游戏）；内存读取侵入性强；反向 VLM 可作为内存读取的兜底。
- **后果**：
  - ✅ 开箱即用，通用性强
  - ❌ 精度不如内存读取

### ADR-006：CLI 是核心，MCP 是包装

- **状态**：已采纳
- **决策**：CLI 核心优先，MCP 包装 CLI。
- **理由**：CLI 易测试；MCP 实现简单（一个 tool 对应一个 CLI 命令）；用户可单独用 CLI。
- **后果**：
  - ✅ 测试友好，灵活集成
  - ❌ CLI 状态管理稍复杂

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

### ADR-009：抽象 TestReporter 接口，Allure 是默认 reporter

- **状态**：已采纳
- **决策**：抽象 `TestReporter` 接口，内置 5 个实现（Allure / Html / Json / CoverageMap / CharterReplay），Allure 是默认。
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

---

## 7. 落地路径

### 阶段 1：MVP 骨架（1-2 周）

**目标**：能生成 charter + 加载插件 + 输出 CLI 报告（mock LLM 跑通端到端）

**任务**：
- [ ] Plugin 接口（v0.1 最小集）+ 加载器
- [ ] CLI 入口框架（基于 click 或 typer）
- [ ] validate 命令（校验 charter / Python 测试代码）
- [ ] 最小 mock 执行器（不真跑游戏，只模拟输出）
- [ ] 端到端 demo 脚本

**完成标志**：`joker-test generate charter ... && joker-test validate charter ... && joker-test run charter ... --mock` 能跑通

### 阶段 2：冒烟测试链路（1-2 周）

**目标**：LLM 生成 Python 测试代码 → pytest 跑 → Allure 报告

**任务**：
- [ ] `ExecutorBackend` 接口定义（Pydantic-style ABC）
- [ ] `OpenCVBackend` 实现（默认，cv2 + pyautogui + easyocr）
- [ ] `AirtestBackend` 实现（可选）
- [ ] `LazyState` 代理（按需解析 state）
- [ ] LLM 生成 Python 测试代码的 prompt 模板
- [ ] Pytest fixture 体系（`backend` / `game_state` / `plugin`）
- [ ] `TestReporter` 接口 + `AllureReporter` + `HtmlReporter` + `JsonReporter`
- [ ] `MultiReporter` 广播机制
- [ ] conftest.py 集成（自动截图 / 失败附加 state）

**完成标志**：能在 example_arpg 上跑通 1 个 Python 冒烟测试，产出 Allure 报告

### 阶段 3：探索式执行链路（3-4 周）

**目标**：Charter 能跑 30 分钟，产出 bug 报告

**任务**：
- [ ] 战术脚本库（5 个 Persona × 5 个战术 = 25 个）
- [ ] 三层异步架构（L1/L2/L3 协程）
- [ ] Investigator（默认 VLM Agent）
- [ ] Judge（Meta-CoT + 插件规则校验）
- [ ] Bug 报告生成
- [ ] `CoverageMapReporter` + `CharterReplayReporter`

**完成标志**：在 example_arpg 上跑一个破坏狂 charter，发现 1-3 个真实 bug

### 阶段 4：工业化包装（1-2 周）

**目标**：Claude Code 能通过 MCP 调用整套测试流程

**任务**：
- [ ] MCP Server（基于官方 MCP SDK）
- [ ] 文档（README + 用户指南 + 插件开发指南）
- [ ] 性能优化（缓存、并发）
- [ ] 示例插件（example_arpg）

**完成标志**：Claude Code 通过 MCP 完成一次"生成 → 执行 → 报告"全流程

---

## 8. 风险与开放问题

### 8.1 已识别风险

| # | 风险 | 严重度 | 缓解 | 状态 |
|---|---|---|---|---|
| R1 | 插件接口稳定性（一旦定下难改） | 高 | v0.1 只开放核心扩展点 | 监控中 |
| R2 | LLM 生成 Python 测试代码质量参差 | 高 | pytest 跑一遍 + mypy 类型检查 + 人工 review | 待验证 |
| R3 | 内存读取的游戏适配成本 | 中 | 默认 VLM Agent，内存读取可选 | 已决策 |
| R4 | 长时间 charter 状态管理 | 中 | session ID + 中间状态持久化 | 待设计 |
| R5 | Airtest 是 GPL-3.0 | 中 | 仅分发触发，不传染 SaaS | 可接受 |
| R6 | 战术脚本库的覆盖度 | 中 | 5 Persona × 5 战术起步，可扩展 | 待实现 |
| R7 | LLM 成本失控 | 中 | 本地 VLM 为主，关键判断走云端 | 设计中 |
| R8 | Reporter 接口稳定性 | 中 | v0.1 接口简单(7 方法)，稳定后再扩展 | 监控中 |

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
| **Tactic** | 战术脚本（毫秒级执行的具体操作模式） |
| **ExecutorBackend** | 执行后端抽象（OpenCVBackend / AirtestBackend 等） |
| **TestReporter** | 报告器抽象（AllureReporter / HtmlReporter 等） |
| **MultiReporter** | 多 reporter 组合器（事件广播 + 错误隔离） |
| **LazyState** | 按需解析的游戏状态代理 |
| **Investigator** | 独立验证者（不信任 subject agent 自报告） |
| **Judge** | 综合判断者（Meta-CoT + 规则校验） |
| **GameState** | 游戏状态快照（L1 感知输出） |
| **Plugin** | 游戏特化扩展（数据/规则/工具/Reporter） |
| **Session** | 一次 charter 执行的会话 |
| **Spec** | Pydantic 校验的测试数据（参数化测试输入） |
| **Allure** | 默认 Reporter 实现之一（工业标准 Web 报告） |

---

## 10. 修改记录

| 版本 | 日期 | 修改 | 修改人 |
|---|---|---|---|
| v0.1 | 2026-06-26 | 初稿，覆盖 6 个模块、4 阶段路径、7 条 ADR、10 个开放问题 | — |
| v0.2 | 2026-06-26 | 修订 ADR-002（ExecutorBackend 抽象）；4.2 节加入执行模式说明；追加 ADR-008/009 | — |
| v0.3 | 2026-06-26 | **撤销 DSL 设计**：ADR-001 改为 Python+pytest+Backend；ADR-008 改为 Python 模块 + Pydantic spec；ADR-009 撤销 | — |
| v0.4 | 2026-06-26 | **整体审阅清理**：① 清除所有 DSL 残留（2.1/2.2/3.1/3.2/4.5/4.6）② 新增 4.7 测试可视化（Reporter 插件化）③ 新增 ADR-009（TestReporter 抽象）+ ADR-010（插件提供 Reporter）④ 4.5 插件去掉 Action 扩展，加 Reporter 扩展 ⑤ 新增 5.5 Reporter 数据契约 ⑥ 阶段 2 加入 Reporter 任务 ⑦ 开放问题 Q3 删除（DSL 相关）⑧ 术语表补充 TestReporter/MultiReporter/Allure ⑨ 简化 ADR（去除冗余"撤销说明"） | — |

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

**文档结束** | v0.4 | 2026-06-26
