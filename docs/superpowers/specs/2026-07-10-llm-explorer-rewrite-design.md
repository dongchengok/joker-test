# LLMExplorer 重写设计：ExploreStrategy 抽象 + Open-AutoGLM 借鉴

> 日期：2026-07-10
> 状态：待实现
> 关联：DESIGN.md §4.3（探索流水线）、ADR-011（pipeline 取代战术库）、roadmap 迭代 B

## 1. 背景与目标

### 1.1 现状

当前 `LLMExplorer`（`explorer/llm_explorer.py`，644 行）有 7 个核心缺陷：无目标引导（`config.intent` 未使用）、无回退机制、无跨步记忆/规划、单步 JSON 而非思维链、Exit action 硬编码 click_text、`time.sleep(1.5)` 替代 wait_until、纯图标界面指纹退化。

### 1.2 目标

用 Open-AutoGLM 的循环骨架重新实现 LLMExplorer，完全重写 + 架构升级。探索策略抽象为可替换层（`ExploreStrategy` 协议），默认实现 ReactStateStrategy（方案 A：ReAct + 状态机），可选 ConversationStrategy（方案 C：对齐 Open-AutoGLM 的 conversation context 循环逻辑，作为对比基准）。重叠工程（LLM 后端、动作执行、录制）用现有项目扩展。

### 1.3 设计约束

- **LLM 克制红线**（DESIGN §2.2）：A 用状态机替代 conversation context 传递上下文（固定 token）；C 保留 conversation context 但剥离历史截图（与 Open-AutoGLM 一致，O(N×文+1图)）
- **状态自洽**（AGENTS.md）：每个策略类自持状态，不与 LLMExplorer 反向引用
- **保留项目优势**：perception 辅助（OCR 全文+bbox+图标匹配）、StateMap 记忆、GlobalRecorder 录制
- **动作格式统一**：A/C 共用 JSON + 归一化 [0,1] 坐标（不照搬 Open-AutoGLM 的 Python 伪代码 + 0-999 坐标），保证对比的是循环逻辑差异

## 2. 整体架构

### 2.1 分层

```
LLMExplorer（基础设施外壳，公共逻辑）
  │ 持有：backend / perception / recorder / llm / screenshot_dir
  │ 职责：截图 / perception / 动作执行 / 录制 / trace / wait_until 等稳定
  │
  └─ ExploreStrategy（协议，可替换）
       ├── ReactStateStrategy    ← 默认：ReAct + 状态机
       └── ConversationStrategy  ← 可选：conversation context（对齐 Open-AutoGLM）
```

LLMExplorer 退化为基础设施外壳。策略只负责决策和状态管理，不感知截图/录制/动作执行的细节。

### 2.2 数据流

```
LLMExplorer.explore(intent) → StateMap + RecordedFlow
  │
  └─ while not strategy.should_stop() and step < max_steps:
       1. 截图 + perception（OCR全文+bbox + 图标匹配）
       2. decision = strategy.decide(screenshot, perception, ctx)
       3. trace(decision.think)
       4. if decision.stop: break
       5. result = perform_action(decision.action) + wait_until 等稳定
       6. strategy.on_action_executed(decision, result)
       7. record(decision, result)  ← 公共录制，两种策略都走
       8. step += 1
  return strategy.get_state_map()
```

## 3. ExploreStrategy 协议

```python
@runtime_checkable
class ExploreStrategy(Protocol):
    def decide(self, screenshot: NDArray, perception: PerceptionResult,
               ctx: ExploreContext) -> StepDecision: ...
    def on_action_executed(self, decision: StepDecision,
                           result: ActionResult) -> None: ...
    def should_stop(self) -> bool: ...
    def get_state_map(self) -> StateMap: ...
```

### 3.1 数据契约

```python
class ExploreContext(BaseModel):
    __test__ = False
    step: int
    max_steps: int
    intent: str
    backend: ExecutorBackend
    llm: LLMProvider
    recorder: GlobalRecorder | None

class StepDecision(BaseModel):
    __test__ = False
    think: str
    action: ExploreAction
    stop: bool = False
    goal_progress: str = ""
    goal_completed: bool = False
    description: str = ""

class ActionResult(BaseModel):
    __test__ = False
    success: bool
    screen_changed: bool
    pixel_diff_ratio: float = 0.0
    error: str | None = None
```

### 3.2 配置

```python
# ExploreConfig 加字段
explore_strategy: Literal["react_state", "conversation"] = "react_state"
```

CLI：`joker-test explore --strategy react_state`（默认）/ `--strategy conversation`

## 4. ReactStateStrategy（方案 A，默认）

### 4.1 ExploreState 内部状态

```python
class ExploreState:
    screens: list[Screen]                  # 已发现界面（节点）
    exits: list[Exit]                      # 界面间的边（含 action + evidence）
    path_stack: list[Exit]                 # 来时路径（存 Exit，含反向信息）
    unvisited: dict[str, list[UIElement]]  # 每界面待探索元素 {screen_id: [未点过的]}
    goal: str
    goal_completed: bool = False
    goal_progress: str = ""
    stale_count: int = 0
    tried_actions: set[tuple[str, str]]    # (指纹, 量化target) 去重
```

改进点（相对当前 LLMExplorer）：
- `name` 并入 Screen 模型，消除平行列表
- `tried_actions` 对坐标做网格量化（`round(x*10)/10`），相近坐标视为同一操作
- `path_stack` 存 Exit（含来时路径），不存 screen_id

### 4.2 ReAct prompt（固定大小，不随步数增长）

LLM 每步收到：截图 base64 + perception 辅助（OCR 全文带 bbox + 图标匹配）+ 探索状态摘要。

```
[截图 base64]

[perception 辅助]
OCR 文本（带 bbox）:
  - "进入地牢" bbox=[0.35, 0.45, 0.3, 0.1]
  - "设置" bbox=[0.8, 0.05, 0.15, 0.08]
图标匹配: [设置图标 threshold=0.92]

[探索状态]
目标: {goal}
进度: {goal_progress}
已探索界面图:
  主菜单 →(点进入地牢)→ 存档列表
  主菜单 →(点设置)→ 设置页
当前位置: 存档列表
路径: 主菜单→存档列表
待探索元素:
  主菜单: [退出按钮]
  设置页: [音频、视频、返回]

请推理下一步：
1. 目标完成了吗？
2. 当前位置该前进、回退、还是探索新分支？
3. 具体做什么动作？

用 <think>...</think><answer>{json}</answer> 回答。
```

LLM 返回：

```xml
<think>
目标"进入地牢并返回主菜单"未完成。当前在存档列表，已进入地牢但还没返回。
应该点"返回"回到主菜单，完成目标。
</think>
<answer>
{"action": "click_text", "target": "返回", "description": "点击返回按钮回主菜单",
 "goal_progress": "已进入地牢，正在返回主菜单", "goal_completed": false}
</answer>
```

### 4.3 back 动作处理

`back` 动作优先沿已知 Exit 路径回退（0 LLM），无已知反向路径才 LLM 决策：
- 有已知反向 Exit（如"设置页→主菜单"的返回按钮）→ 直接执行该 Exit 的反向动作
- 无已知反向路径 → LLM 下一步决策"怎么回去"（兜底）

path_stack 存 Exit：回退时弹出栈顶 Exit，获取其反向信息。

### 4.4 should_stop()

```python
def should_stop(self) -> bool:
    return self._state.goal_completed or self._state.stale_count >= self._max_stale
```

### 4.5 复用 detection.py

- `compute_fingerprint`（界面去重，修掉当前内联重复实现）
- `has_screen_changed`（切屏判断，替代硬编码阈值）

纯图标界面指纹退化：OCR 无文本时用截图 md5 补充（零新依赖）。pHash 留后续迭代。

## 5. ConversationStrategy（方案 C，对比基准）

### 5.1 循环逻辑对齐 Open-AutoGLM

- **历史截图剥离**：每步截图进 messages，LLM 响应后立即剥离图像块，历史只保留文本
- **assistant 重封装**：不管 LLM 实际输出什么，重新封装为 `<think>{thinking}</think><answer>{action}</answer>` 存入历史
- **system 一次**：system prompt 只在第一步追加

### 5.2 上下文结构（N 步后）

```
system: [角色 + 动作空间 + 推理规则]
user:   [图像已剥离] 任务目标 + perception 信息
assistant: <think>看到主菜单...</think><answer>{"action":"click_text","target":"进入地牢"}</answer>
user:   [图像已剥离] perception 信息
assistant: <think>进入存档列表...</think><answer>{"action":"click_text","target":"返回"}</answer>
...
user:   [当前步，图像未剥离] perception 信息  ← 只有这条有图
```

### 5.3 保留的项目增强（不对齐）

- `_screens` 维护：被动 append（get_state_map 要返回），不影响循环逻辑
- 动作后验证：LLMExplorer 外壳的 wait_until + pixel_diff（公共逻辑，A/C 共用）

### 5.4 should_stop()

```python
def should_stop(self) -> bool:
    return self._goal_completed or self._token_exceeded()
```

`max_conversation_tokens` 默认 8000，防爆炸。

## 6. 动作空间扩展

### 6.1 新增动作

| 动作 | 签名 | 说明 |
|---|---|---|
| `swipe` | `swipe(x1, y1, x2, y2, duration=0.5)` | 滑动（滚动背包/拖拽） |
| `scroll` | `scroll(direction, amount=1)` | 翻页（内部转 swipe，简化 LLM 决策） |
| `long_press` | `long_press(x, y, duration=2.0)` | 长按 |
| `back` | `back()` | 回退（path_stack 驱动） |
| `stop` | `stop(reason)` | 终止探索 |

### 6.2 ExecutorBackend 协议扩展

```python
# 新增方法
def swipe(self, x1: float, y1: float, x2: float, y2: float, duration: float = 0.5) -> None: ...
def long_press(self, x: float, y: float, duration: float = 2.0) -> None: ...
```

`scroll`/`back`/`stop` 不进协议——探索层语义动作，由 LLMExplorer 外壳翻译。

### 6.3 FakeBackend 扩展

加 `swipe_history` / `long_press_history` 列表（与现有 `click_history` 同模式）。

### 6.4 LLM 动作 JSON 格式（A/C 统一）

归一化 [0,1] 坐标。示例：
```json
{"action": "swipe", "start_x": 0.5, "start_y": 0.7, "end_x": 0.5, "end_y": 0.3, "duration": 0.5}
{"action": "scroll", "direction": "down", "amount": 2}
{"action": "long_press", "x": 0.5, "y": 0.3, "duration": 2.0}
{"action": "back"}
{"action": "stop", "reason": "目标完成"}
```

## 7. UIMap → StateMap 改名

全项目统一改名。Screen 模型增强（name 并入）。

### 7.1 Screen 增强

```python
class Screen(BaseModel):
    id: str
    name: str = ""              # 新增（原平行存储并入）
    elements: list[UIElement]
    exits: list[Exit] = Field(default_factory=list)
    entry: dict[str, object] | None = None
    fingerprint: str
    screenshot_ref: str | None = None
```

### 7.2 影响文件

| 文件 | 改动 |
|---|---|
| `explorer/types.py` | UIMap→StateMap + Screen.name |
| `explorer/explorer.py` | UIExplorer 返回类型 + 引用 |
| `explorer/llm_explorer.py` | 完全重写 |
| `pipeline/types.py` | ExploreResult.uimap→state_map |
| `pipeline/stages/*.py` | 引用 |
| `tests/test_pipeline_*.py` `tests/test_explorer*.py` | 引用 |
| `DESIGN.md` `AGENTS.md` | 术语 + 文档 |

## 8. 文件结构

### 新增文件

| 文件 | 职责 |
|---|---|
| `src/joker_test/explorer/strategy.py` | ExploreStrategy 协议 + ExploreContext/StepDecision/ActionResult/ExploreAction |
| `src/joker_test/explorer/react_strategy.py` | ReactStateStrategy + ExploreState |
| `src/joker_test/explorer/conversation_strategy.py` | ConversationStrategy |
| `tests/test_explore_strategy.py` | 协议 + 数据契约 |
| `tests/test_react_strategy.py` | ReactStateStrategy |
| `tests/test_conversation_strategy.py` | ConversationStrategy |

### 修改文件

| 文件 | 改动 |
|---|---|
| `explorer/types.py` | UIMap→StateMap + Screen.name |
| `explorer/llm_explorer.py` | 完全重写为外壳 |
| `explorer/explorer.py` | UIMap→StateMap 引用 |
| `executor/base.py` | +swipe +long_press |
| `executor/backends/fake/backend.py` | +swipe +long_press |
| `executor/backends/airtest/backend.py` | +swipe +long_press |
| `pipeline/types.py` | +explore_strategy 字段 + uimap→state_map |
| `pipeline/stages/explore.py` | 策略选择 + 引用 |
| `pipeline/stages/report.py` | 引用 |
| `pipeline/stages/reflect.py` | 引用 |
| `cli.py` | +--strategy 参数 |
| `tests/test_pipeline_*.py` | 引用 |
| `DESIGN.md` `AGENTS.md` | 文档 |

## 9. 错误处理

| 故障点 | 处理 |
|---|---|
| 截图失败 | LLMExplorer._retry_screenshot（3 次，与现有一致） |
| perception 失败 | 降级为空 PerceptionResult，LLM 仍能看图 |
| LLM 决策失败 | 单步异常隔离，step+=1，stale_count+=1，继续 |
| 动作执行失败 | 记录 ActionResult(success=False)，策略决定是否重试 |
| back 无已知路径 | LLM 下步决策兜底 |
| conversation token 超限 | should_stop 返回 True |

## 10. 测试策略

| 测试文件 | 覆盖 |
|---|---|
| `test_explore_strategy.py` | 协议 + 数据契约往返 |
| `test_react_strategy.py` | ExploreState + ReAct prompt + back 回退 + 去重 + should_stop |
| `test_conversation_strategy.py` | 历史截图剥离 + assistant 重封装 + token 超限停止 |
| `test_llm_explorer.py`（重写） | 外壳循环 + 动作执行 + 录制 + trace |
| `test_backends_fake.py`（扩展） | swipe/long_press |

CI 用 FakeBackend + MockProvider。真机测试加 `_real` 后缀放 `tests/real/`。

## 11. 不做的事（YAGNI）

- 不实现 Planner/Grounder 分离（方案 B）
- 不引入 pHash（OCR 无文本时用截图 md5，pHash 留后续）
- 不照搬 Open-AutoGLM 的 Python 伪代码动作格式 + 0-999 坐标（用 JSON + 归一化）
- 不实现 Open-AutoGLM 的 Take_over/Call_API/Interact/Note 等非测试场景动作
- 不做 conversation context 的压缩/总结（C 只加 token 超限停止）

## 12. 实现顺序（建议）

1. UIMap→StateMap 改名 + Screen.name 增强（基础，先做）
2. 动作空间扩展（ExecutorBackend + FakeBackend + AirtestBackend）
3. ExploreStrategy 协议 + 数据契约
4. LLMExplorer 外壳重写
5. ReactStateStrategy（默认，核心）
6. ConversationStrategy（对比基准）
7. pipeline + CLI 接入
8. 文档更新
