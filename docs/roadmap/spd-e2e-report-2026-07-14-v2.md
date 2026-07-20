# SPD 端到端测试报告 & 优化建议

> 2026-07-14 运行 | 代码版本：thinking API + 提示词文件化 + thinking 泄露修复

## 1. 运行数据

| 指标 | 本次 | 上次（改前） | 变化 |
|---|---|---|---|
| 总耗时 | 90s | 176s | **-49%** ⬇ |
| LLM 调用 | 11 次 | 22 次 | **-50%** ⬇ |
| 探索步数 | 10 | 17 | **-41%** ⬇ |
| 动作分布 | click 9, stop 1 | click 14, swipe 2, long_press 1 | swipe 消失 |
| goal_completed | ✅ step 9 | ❌ | 提前返回但误报 |
| 实际音量值 | 10（未变） | 10（未变） | ❌ |
| thinking 质量 | avg 313 字, max 1081 | avg 218 字 | **+44%** ⬆ |
| thinking 泄露 | 0 次 | 13 次 | ✅ 已修复 |
| 最后 prompt 大小 | 6932 chars | 18177 chars | **-62%** ⬇ |

## 2. 本次修复的 bug

**Bug: thinking 内容回传 API**

`decision.think` 来自 API thinking block（267 字完整推理），被写进 assistant 消息回传给 API，违反 Anthropic 协议。

**修复**：assistant 消息去掉 `<think>` 标签，只保留 `<action>/<target>/<coords>/<progress>`。thinking 仅在 trace 的 `llm_calls.jsonl` 中保留。

**效果**：
- context size 从 18177 → 6932 字（-62%）
- 不再违反 Anthropic 协议

## 3. 对话结构验证

| 检查项 | 结果 |
|---|---|
| assistant XML 格式 | ✅ `<action>click</action><target>设置</target><coords>0.310,0.760</coords>` |
| observation 行 | ✅ `[上一步] click (0.39,0.39) diff=0.122` |
| 无 `上一步反馈` | ✅ |
| 无判断文案 | ✅ |
| thinking 不回传 | ✅ 0 次 |
| _BASE_PROMPT 来自文件 | ✅ `exploration_system.md` + `ui_interaction.md` |

## 4. 根因分析

### 4.1 LLM 视觉定位精度不足

LLM 在 step 9 的 reasoning（272 字）中写道：
> "滑块指针位于大约中间偏左的位置，看起来大约是5..."

**实际手柄在最右边（值 10）。** LLM 无法准确判断手柄在滑块轨道上的具体位置。这是一个**感知层**的能力缺口，不是推理层的问题。

### 4.2 缺乏 swipe 尝试

9 步全部是 click，0 次 swipe。尽管提示词中明确指出"滑块需要 swipe 拖拽"，LLM 在实际执行中仍倾向于 click。可能是因为：
1. LLM 找不到明确的手柄起始位置，不敢 swipe
2. Click 是更简单的操作，LLM 倾向于先尝试简单方法

### 4.3 goal_completed 误报

两次 e2e（上次和本次）都出现了 goal_completed 误报。LLM 在视觉判断"手柄已经到了目标位置"时就会宣布完成，但实际上手柄没有移动。这表明 LLM 无法通过截图可靠地判断滑块状态。

## 5. 优化建议（按优先级）

### P0: 滑块手柄视觉检测（推荐立即实施）

当前瓶颈已从推理层转移到感知层。LLM 推理质量已经足够（avg 313 字），但无法精确定位手柄。

**方案**：通过 `cv2.matchTemplate` 检测滑块手柄（圆形/菱形标记），将精确坐标通过新插件注入给 LLM。

**预期效果**：
- LLM 获得手柄精确坐标后，可以从正确位置开始 swipe
- goal_completed 可以通过校验手柄坐标变化来确实判断，不依赖 LLM 视觉判断

### P1: 系统层 goal_completed 校验

**问题**：LLM 视觉判断目标达成不准确，导致误报 goal_completed。

**方案**：在 OCRPlugin 或新插件中增加数值变化检测——如果目标涉及数值变化（如"音量从 10 降到 5"），系统应校验 OCR 中的数字是否真的从 10 变成了其他值。如果没变，拒绝 LLM 的 goal_completed，注入提示"目标数值未变化，请确认操作是否生效"。

### P2: click 频率过高时强制引导 swipe

**问题**：LLM 在滑块场景下连续使用 click，即使提示词中有 swipe 指引。

**方案**：当检测到连续 3 次 click 且 pixel diff 在噪声范围（<0.15）时，在 observation 中加注："连续 N 次点击未改变界面，建议尝试 swipe"。这不是系统判断，是事实引用。

### P3: 探索策略——从上次成功界面继续

**问题**：每次 e2e 都从主菜单重新导航到音频设置，浪费步数。

**方案**：固化成功导航路径，下次直接从音频设置界面继续探索。目前不在 scope 内，列为远期优化。

## 6. 本次改动清单

| 文件 | 改动 |
|---|---|
| `src/joker_test/explorer/conversation_strategy.py` | assistant 消息去掉 `<think>` 标签 |
| `src/joker_test/prompts/constants/exploration_system.md` | 新建——探索主 prompt |
| `src/joker_test/prompts/constants/ui_interaction.md` | 新建——通用 UI 交互指南 |
| `src/joker_test/prompts/constants/ocr_format.md` | 新建——OCR 坐标格式说明 |
| `src/joker_test/prompts/loader.py` | 新增 prompt 加载函数 |
| `src/joker_test/plugins/ocr/__init__.py` | inject_system_prompt 改为从文件加载 |
| `src/joker_test/config.py` | llm.thinking 配置段 |
| `src/joker_test/llm/providers/anthropic/__init__.py` | thinking 参数 + thinking block 处理 |
| `src/joker_test/trace.py` | perceive/plugin_inject HTML 渲染优化 |
| `scripts/e2e_spd_explore_conversation.py` | 从 config 读 thinking 设置 |
