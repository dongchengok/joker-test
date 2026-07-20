# 探索对话结构优化：assistant 结构化 + 取消系统反馈

> **对标**: Open-AutoGLM（截图即观察）+ OpenCode（事实不判断）

## 1. 问题

当前对话结构存在三个问题：

1. **assistant 消息太稀疏**：只存 `decision.think or decision.action` 文本，丢失 action/target/坐标/goal_progress。LLM 回头看历史不知道"上一步到底做了什么操作、点了哪里、当时的 OCR 状态是什么"。

2. **反馈替 LLM 下判断**：系统在 prompt 里注入"操作无效，滑块需用 swipe 拖拽"。不管 LLM 已经用了 swipe 还是 long_press，反馈始终一样。LLM 被动接受指令而非自主推理。

3. **混淆事实和建议**：`上一步反馈: [ocr] 操作后界面文字未变，操作可能未生效。滑块/旋钮不能用 click 点击，必须用 swipe 拖拽。` 这句话前半是事实（文字未变），后半是建议（用 swipe）。LLM 需要的是更多事实、更少指令。

## 2. 设计原则（对标两家）

| 原则 | Open-AutoGLM | OpenCode | 我们 |
|---|---|---|---|
| 观察来源 | 新截图（LLM 自己对比前后） | 工具原始输出 | 截图 + OCR 数据（比截图更精确） |
| 推理归属 | LLM 自己分析 | LLM 自己分析 | LLM 自己分析 |
| 系统反馈 | **无** | **无** | **改为无** |
| 执行历史 | assistant 保留完整动作+推理 | tool_result 保留完整输出 | assistant 保留完整动作+推理+状态 |

**核心：系统采集事实，LLM 自己推理。**

## 3. 改动

### 3.1 assistant 消息结构化

**现状**（`conversation_strategy.py:115-117`）：
```python
self._messages.append({
    "role": "assistant",
    "content": [{"type": "text", "text": decision.think or decision.action}],
})
```

**改为**：
```python
action_lines = [f"<think>{decision.think}</think>"]
action_lines.append(f"<action>{decision.action}</action>")
if decision.target:
    action_lines.append(f"<target>{decision.target}</target>")
if decision.x is not None and decision.y is not None:
    action_lines.append(f"<coords>{decision.x:.3f},{decision.y:.3f}</coords>")
if decision.goal_progress:
    action_lines.append(f"<progress>{decision.goal_progress}</progress>")
assistant_text = "\n".join(action_lines)
self._messages.append({
    "role": "assistant",
    "content": [{"type": "text", "text": assistant_text}],
})
```

这样 LLM 回头看历史时，每一步的执行意图、具体操作、目标对象一目了然。

### 3.2 取消系统反馈

**删除**：
- `ConversationStrategy._last_action_feedback` 字段
- `ConversationStrategy._last_validate_feedback` 字段
- `ConversationStrategy.on_action_executed` 里的反馈生成逻辑
- `_build_step_text` 里的 `feedback = self._last_action_feedback or self._last_validate_feedback` 前缀注入
- `LLMExplorer._run_step` 里 `_last_validate_feedback` 注入到 strategy 的逻辑（第 143-145 行）
- OCRPlugin.validate 保留但返回 None（空实现，不产出反馈文本）

**保留**：
- `_stale_count` 追踪逻辑（只用于 `should_stop` 判断，不用于生成反馈文案）
- `plugin_manager.validate()` 调用本身（保留接口，未来插件可能需要）

### 3.3 observation 改为纯数据行

`_build_step_text` 中添加一行纯事实的上一步摘要，不做判断：

```python
# 如果上一步有执行记录，加一行纯事实（不做判断）
if self._last_action_info:
    base = f"[上一步] {self._last_action_info}\n{base}"
    self._last_action_info = ""
```

`_last_action_info` 由 `on_action_executed` 设置，内容为：
```
click(0.34,0.39) diff=0.12
```
或带 OCR 对比（若有）：
```
swipe left(0.48→0.18,0.41) diff=0.14
```

只陈述"做了什么 + pixel diff 多少"，不解读"是否有效"。

### 3.4 _BASE_PROMPT 调优

删除"滑块/旋钮不能用 click 点击"这种预设判断的描述（LLM 应该自己从截图中判断），简化为：

```text
规则：
- action 必须是上面 8 个之一，不要发明其他动作名
- click 的 target 填按钮上的文字；有文字的按钮直接用界面元素给的坐标作为 x/y
- 坐标是归一化 [0,1]（左0右1，上0下1），不要输出绝对像素值
- swipe 的 target 填方向 left/right/up/down，x/y 填滑块手柄的当前坐标
- 不要点击窗口的关闭(×)/最小化/最大化按钮
- 不要重复点击同一个按钮
- 绝对不要点击全屏模式
- goal_progress 描述当前进度，goal_completed 为 true 时表示目标完成
- 每步评估目标是否达成。达成时必须输出 goal_completed=true
```

改动：把"滑块必须用 swipe、点击轨道无效"这种预设判断删掉。LLM 会从截图和 OCR 数据中自己学会。

## 4. 不改动

- `ExploreStrategy` 协议不变（`on_action_executed` 签名保持已有 `validate_feedback=""` 参数，只是不再使用）
- `AgentPlugin` 协议不变
- `PluginManager` 不变
- OCRPlugin.inject_step / inject_system_prompt 不变

## 5. 效果对比

| | 改前 | 改后 |
|---|---|---|
| assistant 消息 | `"点击设置按钮进入设置界面"` | `<think>...</think><action>click</action><target>设置</target>` |
| 动作结果 | `上一步反馈: 操作无效，用swipe...` | `[上一步] click(0.34,0.39) diff=0.12` |
| LLM 能否回溯历史 | ❌ 不知道 3 步前做了什么 | ✅ 每一步的 action/target/coords 完整保留 |
| 系统判断 | ✅ 帮 LLM 下结论 | ❌ 只给事实 |

## 6. 测试

- `tests/test_conversation_strategy.py`：更新 assistant 消息格式断言
- `tests/test_ocr_plugin.py`：validate 返回 None 的测试已存在（保持不变）
- `tests/test_llm_explorer.py`：更新 mock strategy 的 `on_action_executed` 签名
- `tests/test_trace.py`：确认 trace HTML 渲染不受影响
