# SPD 端到端测试改进计划

> 基于 2026-07-14 多轮 SPD 端到端探索测试（音量调整场景）的分析结果。
> 本文档记录已修复的 bug、仍存在的根因问题、以及后续改进方向。

## 1. 已修复的问题（本轮）

### BUG1: 截图未落盘（trace 诊断盲区）
- **现象**: `LLMExplorer` 接受 `screenshot_dir` 参数，但 `_run_step` 从不调用 `cv2.imwrite`，导致 `screenshot_dir/` 目录为空。trace HTML 无法对照实际界面验证 LLM 当时看到了什么。
- **根因**: `_run_step` 截图后直接传给 perception，未落盘。
- **修复**: 新增 `_save_screenshot()` 方法，每步截图保存为 `step_NNN.png`，路径记入 trace `perceive` 事件。
- **文件**: `explorer/llm_explorer.py`

### BUG2: 动作无效反馈缺失（LLM 盲目重复）
- **现象**: LLM 反复点击同一个无效坐标（如音量滑块文字 15 次），因为策略不告诉 LLM"上一步没生效"。
- **根因**: `ConversationStrategy.on_action_executed` 是空操作（`pass`），`_build_step_text` 不注入上一步结果。
- **修复**: 
  - `on_action_executed` 记录 `_last_action_feedback` + `_stale_count`
  - 操作后界面无变化 → 注入"⚠ 界面无变化，滑块不能用 click，必须用 swipe 拖拽"提示
  - 连续 5 次无效 → `should_stop()` 返回 True（避免浪费 LLM 调用）
- **效果**: LLM 在收到反馈后从 click 切换到 swipe，改变了探索行为。
- **文件**: `explorer/conversation_strategy.py`

### BUG3: screen_changed 误报（装饰动画噪声）
- **现象**: SPD 的装饰动画（主菜单水波纹、右下角火焰效果、版本号闪烁）每帧产生 0.1-0.2 像素 diff，导致所有 no-op 点击被判为 `changed=True`。LLM 误以为操作成功。
- **根因**: 像素 diff 无法区分"界面语义变化"和"装饰动画噪声"。OCR 文本帧间也不稳定（版本号 `V3.3.8` ↔ `￥3.3.8` ↔ `B'E'EA` 交替出现）。
- **修复**（**通过插件机制，不污染 explorer**）:
  - `LLMExplorer._perform_action` 只做通用的像素 diff 变化检测（引擎无关）
  - **语义变化判断交给 OCRPlugin.validate**：用 OCR 文本变化（过滤噪声后）判断操作是否有效
  - OCR 噪声过滤规则（窗口标题/版本号/乱码）全部集中在 `OCRPlugin._filter_noise_texts`
  - validate 反馈通过 `_last_validate_feedback` 注入下一步 prompt，策略的 stale_count 结合 validate 反馈判断
  - e2e 脚本配上 PluginManager([OCRPlugin()]) 即生效
- **效果**: OCRPlugin.validate 在操作无效时反馈"界面文字未变"，LLM 收到后切换操作方法。
- **架构原则**: explorer 零感知知识（ADR-013），加新感知能力只需写插件 + 注册。
- **文件**: `plugins/ocr/__init__.py`（噪声过滤 + validate）、`explorer/llm_explorer.py`（传 backend 给 validate）

### BUG4: Screen 命名全是窗口标题
- **现象**: 所有 Screen 名字都是 "Shattered Pixel Dung"（`texts[0]` = 窗口标题），无法区分不同界面。
- **根因**: `_maybe_add_screen` 用 `texts[0][:20]` 做名字。
- **修复**: 新增 `_infer_screen_name()`，排除窗口标题/装饰文本，优先选含"设置/音频/菜单"等语义关键词的文本。
- **效果**: Screen 名字变为 "设置"、"显示设置"、"音频设置" 等有意义名称。
- **文件**: `explorer/conversation_strategy.py`

### BUG5: long_press airtest API 失效
- **现象**: `long_press` → `ImportError: cannot import name 'long_click' from 'airtest.core.api'`。
- **根因**: airtest 新版移除了 `long_click`，改用 `touch(pos, duration=...)`。
- **修复**: `long_press` 改用 `touch((x, y), duration=duration)`。
- **文件**: `executor/backends/airtest/backend.py`

### BUG6: swipe 方向缺失时默认全屏上滑
- **现象**: LLM 输出 `swipe` 带 x/y 坐标但 target 为空时，代码默认向上（`dy=-0.4`），导致滑块拖拽变成垂直滑动。
- **根因**: `_dispatch_action` 的 swipe 分支 else 默认向上。
- **修复**: 有坐标但方向未指定 → 默认水平拖（`dx=-0.3`，滑块最常见操作）。
- **文件**: `explorer/llm_explorer.py`

### BUG7: trace HTML explore_think 坐标 None 崩溃
- **现象**: LLM 输出 action=stop（无坐标）时，trace HTML 渲染 `f"({x:.2f},{y:.2f})"` 崩溃（NoneType format）。
- **修复**: 增加 None 检查。
- **文件**: `trace.py`

---

## 2. 仍存在的根因问题（下一轮改进）

### 问题 A: 滑块手柄定位（核心瓶颈）
- **现象**: 即使 LLM 切换到 swipe，滑块仍然没有移动（OCR 持续显示 "10"）。
- **根因**: 
  1. OCR 只识别文字（"音乐音量"、"10"），**识别不出滑块手柄（图形元素）**。LLM 不知道手柄在哪，只能从 "10" 的坐标 (0.48, 0.38) 猜起点。
  2. swipe 起点 (0.48, 0.38) 是 "10" 文字位置，不是手柄位置。手柄在更右边（~0.49）。从文字位置 swipe 无法"抓住"手柄。
- **改进方向**:
  - **方案 1（推荐）**: 用 `cv2.matchTemplate` 检测滑块手柄图标，提供精确手柄坐标给 LLM（需要手柄模板图）
  - **方案 2**: 在 OCRPlugin 注入中增加"滑块手柄检测"逻辑（图像分析找圆形/矩形手柄）
  - **方案 3**: 增强系统提示词，让 LLM 从截图中视觉定位手柄（依赖 LLM 视觉能力）

### 问题 B: OCR 文本波动仍存在
- **现象**: 虽然噪声过滤减少了误报，但 OCR 仍有偶发波动（`口`/`×` 窗口装饰有时识别不到，`V3.3.8`/`￥3.3.8` 版本号）。
- **改进方向**:
  - OCR 结果置信度过滤（RapidOCR 提供 confidence score，低置信度文本不参与变化比较）
  - 多帧投票（连续 2 帧都识别到的文本才视为稳定）

### 问题 C: 变化检测的边缘情况
- **现象**: 滑块拖拽成功时（手柄移动），OCR 文字不变（"10" 还是 "10"），像素 diff ~0.16 < 0.25，被判为 `changed=False`。
- **根因**: 纯 OCR 文本比较无法捕捉"图形元素位置变化"（如滑块手柄移动、列表滚动）。
- **改进方向**:
  - 区域级像素 diff（只比操作目标区域的 ROI，不比整屏）
  - 特定控件类型的状态追踪（滑块值从 OCR "10" 提取，前后比对）

### 问题 D: LLM prompt 利用率不足
- **现象**: 系统提示词强调"滑块用 swipe"，但 LLM 前 4-7 步仍用 click。反馈循环虽有改善，但需要 3-4 次无效才切换。
- **改进方向**:
  - 初始几步就注入更强约束（如 "如果界面有滑块控件，第一步就用 swipe"）
  - few-shot 示例（给一个滑块操作的正例）
  - 基于 OCR 文本识别界面类型，自动注入场景化提示（检测到"音量"字样 → 提示用 swipe）

---

## 3. 验证结果对比

| 指标 | 修复前（1209_run） | 修复后（1236_run） |
|---|---|---|
| trace HTML 生成 | ✅ | ✅ |
| 截图落盘 | ❌（0 张） | ✅（20 张） |
| LLM 调用完整记录 | ✅（20 次） | ✅（20 次） |
| OCR 噪声过滤位置 | ❌ hardcode 在 explorer | ✅ 集中在 OCRPlugin |
| 操作无效语义检测 | ❌ | ✅（OCRPlugin.validate 6 次） |
| 动作无效反馈注入 | ❌ | ✅（插件 validate + 策略 stale） |
| LLM 从 click 切换到 swipe | ❌（全程 click） | ✅（step 4 起切换） |
| Screen 命名有意义 | ❌（全是窗口标题） | ✅（设置/音频设置等） |
| 错误数 | 0 | 0 |
| long_press 可用 | ❌（ImportError） | ✅ |
| 单元测试 | 215 passed | 215 passed |

---

## 4. 后续改进优先级

| 优先级 | 改进项 | 预期收益 | 工作量 |
|---|---|---|---|
| P0 | 滑块手柄定位（matchTemplate / 图像分析） | 解决滑块交互核心瓶颈 | 中 |
| P1 | OCR 置信度过滤 | 减少 OCR 波动误报 | 小 |
| P1 | 区域级像素 diff（ROI） | 精准检测控件级变化 | 中 |
| P2 | 场景化提示注入（检测到"音量"→提示 swipe） | 提高 LLM 首次正确率 | 小 |
| P2 | few-shot 示例（滑块正例） | 加速 LLM 学习正确操作 | 小 |
| P3 | 多帧 OCR 投票 | 进一步稳定 OCR | 中 |
