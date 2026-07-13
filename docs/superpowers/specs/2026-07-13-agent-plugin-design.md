# Agent Plugin 系统设计

> 日期：2026-07-13
> 状态：设计完成，待实现
> ADR：ADR-003（插件用 Python 类）扩展

## 1. 背景与动机

### 1.1 问题

当前探索流程中，给 LLM 提供上下文信息的逻辑硬编码在策略类里：

- `ConversationStrategy._SYSTEM_PROMPT`：OCR 格式说明写死在常量里
- `ConversationStrategy._build_step_text`：OCR 文字+坐标拼接逻辑写死在方法里
- `LLMExplorer._perceive`：只从 backend.state 取 OCR，不调 ImageMatcher/PerceptionEngine

这导致：
- **不可扩展**：加一个图像匹配提示要改 3 个文件
- **两套死代码**：`GamePlugin`（3 方法，零调用方）和 `PerceptionEngine`/`ImageMatcher`（已实现，未接入）
- **提示词不可配置**：换游戏/换场景无法调整提示信息

### 1.2 目标

把"给 LLM 提供上下文信息"变成插件化：插件通过注入点向探索流程提供信息，运行时通过配置文件选择激活哪些插件。

### 1.3 设计原则

- **插件定义注入点内容，不限定产出格式**：插件返回字符串（注入点内容），通过系统提示词告诉 LLM 如何理解
- **插件自包含**：一个插件从"告诉 LLM 格式"到"提供每步数据"到"校验结果"是闭环
- **异常隔离**：一个插件崩了不影响其他插件和探索流程（ADR-009 同理）
- **向后兼容**：默认配置行为 = 现有行为

## 2. AgentPlugin Protocol

### 2.1 接口定义

```python
# plugins/base.py（与现有 GamePlugin 并列）

@runtime_checkable
class AgentPlugin(Protocol):
    """测试 Agent 插件。通过注入点向探索流程提供信息。

    4 个注入点，每个返回空值 = 该插件不贡献此注入点的内容。
    所有注入点都有默认空实现（Protocol 不强制），插件只覆盖关心的方法。
    """

    @property
    def name(self) -> str:
        """插件唯一标识名。"""
        ...

    def inject_system_prompt(self) -> str:
        """系统提示词注入点（固定，只拼接一次）。
        告诉 LLM 如何理解本插件提供的信息格式。
        返回空串 = 不注入。"""
        ...

    def inject_step(self, screenshot: Any, backend: Any, ctx: Any) -> str:
        """每轮对话注入点（动态，每步都调）。
        返回本步要追加到 user message 的文本。
        返回空串 = 不注入。"""
        ...

    def inject_action_hint(self, screenshot: Any, backend: Any, ctx: Any) -> str:
        """动作建议注入点（动态，每步都调）。
        给 LLM 额外的动作提示。
        返回空串 = 不注入。"""
        ...

    def validate(self, decision: Any, result: Any) -> str | None:
        """校验注入点（动作执行后调）。
        检查动作结果，返回问题描述（None = 无问题）。
        返回的反馈文本会注入到下一步的 step_text 里让 LLM 自我纠错。"""
        ...
```

### 2.2 四个注入点

| 注入点 | 调用时机 | 返回值用途 | OCR 插件示例 |
|--------|---------|-----------|-------------|
| `inject_system_prompt` | 探索开始 1 次 | 拼入系统提示词 | `"界面元素格式：文字@(x,y)，x/y 是归一化坐标[0,1]..."` |
| `inject_step` | 每步 | 拼入 user message | `"界面元素(文字@坐标):\n  音乐音量@(0.35,0.38)..."` |
| `inject_action_hint` | 每步 | 拼入 user message | `""`（OCR 插件不做动作建议） |
| `validate` | 每步动作后 | 拼入下一步 user message 的反馈段落 | `None`（OCR 插件不校验） |

### 2.3 与 GamePlugin 的关系

`GamePlugin`（现有，`get_data_schemas`/`get_validation_rules`/`get_tools`）**保留不动**。它的职责是测试数据定义和操作工具，跟"给 LLM 注入上下文"无关。后续单独考虑是否将 `get_validation_rules` 映射到 `AgentPlugin.validate`。

## 3. PluginManager

### 3.1 职责

管理激活的插件列表，提供注入点的统一调用入口。负责：
- **拼接**：把所有插件的注入内容按顺序拼成完整文本
- **异常隔离**：每个插件调用 try/except，崩了跳过 + trace 记录
- **validate 反馈收集**：收集所有插件的校验结果

### 3.2 接口

```python
# plugins/manager.py

class PluginManager:
    """插件管理器。注入点统一调用入口 + 异常隔离。"""

    def __init__(self, plugins: list[AgentPlugin]) -> None:
        self._plugins = plugins

    def build_system_prompt(self, base: str) -> str:
        """base + 所有插件的 system_prompt 片段。"""
        ...

    def build_step_text(
        self, screenshot: Any, backend: Any, ctx: Any, base: str,
        validate_feedback: str = "",
    ) -> str:
        """base + 所有插件的 step 注入 + action_hint 注入 + validate 反馈。"""
        ...

    def validate(self, decision: Any, result: Any) -> str:
        """收集所有插件的校验结果，拼成反馈文本。"""
        ...
```

### 3.3 build_step_text 拼接结构

```
步数: 5/20

[OCR 插件注入的界面元素]
界面元素(文字@坐标):
  音乐音量@(0.35,0.38)
  10@(0.48,0.39)

[SliderHint 插件注入的动作建议]
检测到滑块控件，建议用 swipe 而非 click

[上一步 validate 反馈（如有）]
上一步反馈:
  [game_rule] 点击坐标(0.33,0.35)落在"音乐音量"文字上，可能未命中滑块
```

## 4. 内置插件

### 4.1 OCRPlugin（`plugins/ocr/`）

把现有 `_perceive` + `_build_step_text` 的 OCR 逻辑搬过来。

| 注入点 | 内容 |
|--------|------|
| `inject_system_prompt` | `"界面元素格式：文字@(x,y)，x/y 是归一化坐标[0,1]，表示该文字在屏幕上的中心位置。点击有文字的按钮时，直接用它的坐标作为 click 的 x/y，不需要自己估算。"` |
| `inject_step` | 从 `backend.state` 取 OCR 结果（`_ocr_results` 或 `text_elements`），返回 `"界面元素(文字@坐标):\n  文字@(x,y)..."` |
| `inject_action_hint` | `""` |
| `validate` | `None` |

### 4.2 GameRulePlugin（`plugins/game_rule/`，后续）

激活现有 `GamePlugin.get_validation_rules` 的校验规则。MVP 可先不交付。

### 4.3 注册表

```python
# plugins/__init__.py
BUILTIN_PLUGINS: dict[str, type[AgentPlugin]] = {
    "ocr": OCRPlugin,
}
```

## 5. 配置系统

### 5.1 配置文件（JSON）

默认文件名：`joker-test-config.json`

```json
{
  "plugins": ["ocr"],
  "plugin_path": null,
  "explore_strategy": "conversation",
  "max_steps": 20,
  "backend_name": "fake",
  "window_title": ""
}
```

Key 名自解释，不需要注释。

### 5.2 加载逻辑

```python
# src/joker_test/config.py

_DEFAULT_CONFIG_FILE = "joker-test-config.json"

_DEFAULT_CONFIG = {
    "plugins": ["ocr"],
    "plugin_path": None,
    "explore_strategy": "conversation",
    "max_steps": 20,
    "backend_name": "fake",
    "window_title": "",
}

def load_config(path: str | Path = _DEFAULT_CONFIG_FILE) -> dict:
    """加载 JSON 配置文件。

    不存在时用 _DEFAULT_CONFIG 写入默认配置文件后返回。
    """
    path = Path(path)
    if not path.exists():
        path.write_text(
            json.dumps(_DEFAULT_CONFIG, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"已生成默认配置文件: {path}")
        return _DEFAULT_CONFIG.copy()
    loaded = json.loads(path.read_text("utf-8"))
    # 浅合并：顶层 key 缺失用默认值补
    return {**_DEFAULT_CONFIG, **loaded}
```

### 5.3 CLI

```python
parser.add_argument("--config", default="joker-test-config.json",
                    help="配置文件路径（不存在则自动生成）")
```

### 5.4 外部游戏插件

配置文件里 `plugin_path` 指向单个 .py 文件，复用现有 `load_plugin()` 机制加载。

## 6. 目录结构

```
src/joker_test/
  config.py                 # 新增：load_config + _DEFAULT_CONFIG
  plugins/
    __init__.py             # 修改：导出 AgentPlugin + BUILTIN_PLUGINS 注册表
    base.py                 # 修改：加 AgentPlugin Protocol（与 GamePlugin 并列）
    manager.py              # 新增：PluginManager
    loader.py               # 保留不动
    ocr/                    # 新增：OCRPlugin
      __init__.py
    game_rule/              # 后续：GameRulePlugin
      __init__.py
  explorer/
    conversation_strategy.py  # 修改：_SYSTEM_PROMPT → _BASE_PROMPT + plugin_manager
    react_strategy.py         # 修改：_build_prompt 改用 plugin_manager
    llm_explorer.py           # 修改：_perceive 简化 + validate 调用
  cli.py                      # 修改：加 --config
```

## 7. 改动文件清单

### 新增（4 个）

| 文件 | 职责 |
|------|------|
| `src/joker_test/config.py` | `load_config()` + `_DEFAULT_CONFIG` + 自动生成 |
| `src/joker_test/plugins/manager.py` | `PluginManager` |
| `src/joker_test/plugins/ocr/__init__.py` | `OCRPlugin` |
| `src/joker_test/plugins/ocr/base.py` | OCR 感知逻辑（从 backend.state 提取文字+坐标） |

### 修改（5 个）

| 文件 | 改动 |
|------|------|
| `plugins/base.py` | 加 `AgentPlugin` Protocol（与 `GamePlugin` 并列） |
| `plugins/__init__.py` | 导出 `AgentPlugin`、`PluginManager`、`BUILTIN_PLUGINS` |
| `explorer/conversation_strategy.py` | `_SYSTEM_PROMPT` → `_BASE_PROMPT` + `plugin_manager.build_system_prompt()`；`_build_step_text` → `plugin_manager.build_step_text()`；`decide()` 加 validate 反馈 |
| `explorer/react_strategy.py` | 同上 |
| `explorer/llm_explorer.py` | `_perceive` 简化（OCR 逻辑移到 OCRPlugin）；`_run_step` 加 `plugin_manager.validate()` + trace |
| `cli.py` | 加 `--config` 参数；启动时 `load_config()` → `build_plugin_manager()` |

### 不改动

- `executor/` — backend 不变
- `perception/matching/` — ImageMatcher 保持，后续作为新插件接入
- `llm/` — LLM 层不变
- `trace.py` — Tracer 不变
- `flow/` — 录制层不变

## 8. 迁移路径（向后兼容）

每步完成后现有测试应全绿。

```
第 1 步：plugins/base.py 加 AgentPlugin Protocol
第 2 步：新建 plugins/manager.py（PluginManager，含异常隔离）
第 3 步：新建 plugins/ocr/（OCRPlugin，行为 = 现有 _perceive + _build_step_text）
第 4 步：ConversationStrategy / ReactStateStrategy 接入 PluginManager
         _SYSTEM_PROMPT 拆成 _BASE_PROMPT + plugin_manager.build_system_prompt()
         _build_step_text 改调 plugin_manager.build_step_text()
第 5 步：LLMExplorer._perceive 简化；_run_step 加 validate 调用
第 6 步：新建 config.py，CLI 加 --config
第 7 步：全量测试 + lint
```

## 9. 测试策略

| 测试文件 | 内容 |
|---------|------|
| `tests/test_plugin_manager.py` | 多插件注入拼接顺序、异常隔离（mock 插件崩了不影响其他）、空返回处理、validate 反馈拼接 |
| `tests/test_ocr_plugin.py` | OCR 文字+坐标格式正确、backend 无 OCR 时返回空串、FakeBackend 兼容 |
| `tests/test_config.py` | 配置文件不存在→自动生成、配置文件加载、默认值合并 |
| 现有测试全绿 | `plugins=["ocr"]` 行为 = 改造前 |

## 10. trace 可观测性

插件注入通过现有 `_trace` 记录：

```python
# PluginManager.build_step_text 内部
for p in self._plugins:
    injected = p.inject_step(screenshot, backend, ctx)
    if injected:
        trace_event("plugin_inject", {
            "plugin": p.name, "inject_point": "step", "length": len(injected),
        })
```

trace HTML 里插件注入以 `⚙️ plugin_inject` 卡片显示，诊断时知道哪个插件注入了什么。
