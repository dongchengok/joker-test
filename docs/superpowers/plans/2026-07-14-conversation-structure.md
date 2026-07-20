# 对话结构优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** assistant 消息从单行文本改为结构化 XML（think+action+target+coords+progress），取消系统反馈判断，改为纯数据 observation 行。参照 Open-AutoGLM/OpenCode 的"系统采集事实，LLM 自己推理"原则。

**Architecture:** 改动集中在 `conversation_strategy.py`（assistant 消息格式 + 取消反馈 + observation + _BASE_PROMPT），`llm_explorer.py` 微小删减（去掉 feedback 注入），`ocr/__init__.py` 微小改动（validate 返回 None）。不改协议签名。

**Tech Stack:** Python 3.12, pytest

---

## File Structure

| 文件 | 改动 | 职责 |
|---|---|---|
| `src/joker_test/explorer/conversation_strategy.py` | 修改 | assistant XML 格式 + 取消反馈 + observation + _BASE_PROMPT |
| `src/joker_test/explorer/llm_explorer.py` | 修改 | 删除 _last_validate_feedback 注入 |
| `src/joker_test/plugins/ocr/__init__.py` | 修改 | validate 返回 None |
| `tests/test_conversation_strategy.py` | 修改 | 更新 assistant 消息格式断言 |

---

### Task 1: assistant 消息结构化 + 加 observation 字段

**Files:**
- Modify: `src/joker_test/explorer/conversation_strategy.py:68-72,113-131,259-277`
- Test: `tests/test_conversation_strategy.py`

- [ ] **Step 1: 写 assistant 消息 XML 格式的测试**

在 `tests/test_conversation_strategy.py` 的 `test_decide_strips_image_from_history` 函数末尾，追加 assistant 消息 XML 格式断言。将测试函数替换为：

```python
def test_decide_strips_image_from_history() -> None:
    """LLM 响应后，最后一条 user 消息不含图像，assistant 消息含结构化 XML。"""
    mock_llm = MagicMock()
    mock_llm.create.return_value = {
        "content": [
            {
                "type": "tool_use",
                "name": "execute_action",
                "input": {
                    "action": "click", "target": "开始",
                    "think": "需要点击开始按钮",
                },
            }
        ]
    }
    strategy = ConversationStrategy(llm=mock_llm, intent="测试")
    ctx = ExploreContext(
        step=0, max_steps=10, intent="测试", backend=None, llm=mock_llm
    )
    decision = strategy.decide(screenshot=b"fake_img", perception=None, ctx=ctx)

    last_user = [m for m in strategy._messages if m["role"] == "user"][-1]
    has_image = any(
        b.get("type") == "image" or b.get("type") == "image_url"
        for b in last_user["content"]
    )
    assert not has_image, "历史截图应被剥离"
    assert decision.action == "click"

    # assistant 消息含结构化 XML
    last_asst = [m for m in strategy._messages if m["role"] == "assistant"][-1]
    asst_text = last_asst["content"][0]["text"]
    assert "<think>" in asst_text
    assert "<action>click</action>" in asst_text
    assert "<target>开始</target>" in asst_text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_conversation_strategy.py::test_decide_strips_image_from_history -v`
Expected: FAIL — assistant 消息还是旧格式 `"需要点击开始按钮"`, 无 `<think>` 标签

- [ ] **Step 3: 改 assistant 消息为 XML 格式，加 `_last_action_info` 字段**

`src/joker_test/explorer/conversation_strategy.py`:

第 68-72 行，将字段声明改为：

```python
        self._messages: list[Message] = []
        self._screens: list[Screen] = []
        self._goal_completed: bool = False
        self._initialized = False
        # 上一步操作信息（纯事实，不做判断），供下一步 _build_step_text 注入 observation 行
        self._last_action_info: str = ""
```

第 120-123 行，将 assistant 消息从单行 think 改为 XML：

```python
        # 追加 assistant 消息到历史（结构化 XML，保留完整决策信息）
        action_lines = [f"<think>{decision.think}</think>"]
        action_lines.append(f"<action>{decision.action}</action>")
        if decision.target:
            action_lines.append(f"<target>{decision.target}</target>")
        if decision.x is not None and decision.y is not None:
            action_lines.append(f"<coords>{decision.x:.3f},{decision.y:.3f}</coords>")
        if decision.goal_progress:
            action_lines.append(f"<progress>{decision.goal_progress}</progress>")
        self._messages.append(
            {"role": "assistant", "content": [{"type": "text", "text": "\n".join(action_lines)}]}
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_conversation_strategy.py::test_decide_strips_image_from_history -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/joker_test/explorer/conversation_strategy.py tests/test_conversation_strategy.py
git commit -m "refactor(strategy): assistant 消息从 think 文本改为结构化 XML"
```

---

### Task 2: 取消反馈 + observation 纯数据行

**Files:**
- Modify: `src/joker_test/explorer/conversation_strategy.py:199-232,259-277`
- Modify: `src/joker_test/explorer/llm_explorer.py:138-146`

- [ ] **Step 1: 写 observation 数据行 + 无反馈前缀的测试**

在 `tests/test_conversation_strategy.py` 末尾追加：

```python
def test_on_action_executed_sets_last_action_info() -> None:
    """on_action_executed 设置 _last_action_info（纯事实行），不设反馈前缀。"""
    from joker_test.explorer.strategy import ActionResult, StepDecision

    strategy = ConversationStrategy(llm=MagicMock(), intent="测试")
    decision = StepDecision(think="测试", action="click", target="设置", x=0.31, y=0.76)
    result = ActionResult(success=True, screen_changed=True, pixel_diff_ratio=0.4)

    strategy.on_action_executed(decision, result)

    # observation 是纯事实行（含动作和 diff），不做判断
    assert "click" in strategy._last_action_info
    assert "0.4" in strategy._last_action_info
    assert "无效" not in strategy._last_action_info
    assert "swipe" not in strategy._last_action_info
    # _build_step_text 不再有反馈前缀
    text = strategy._build_step_text(None, None)
    assert "上一步反馈" not in text
    assert strategy._last_action_info == ""  # 消费后清空
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_conversation_strategy.py::test_on_action_executed_sets_last_action_info -v`
Expected: FAIL — `_last_action_info` 字段不存在、`_build_step_text` 需要参数

- [ ] **Step 3: 改 `on_action_executed`、`_build_step_text`、`_run_step`**

**3a.** `conversation_strategy.py` 第 199-232 行，将 `on_action_executed` 替换为：

```python
    def on_action_executed(
        self,
        decision: StepDecision,
        result: ActionResult,
        validate_feedback: str = "",
    ) -> None:
        """记录上一步操作的纯事实摘要（不做判断），供下一步 observation 使用。

        参照 Open-AutoGLM/OpenCode：系统只提供事实，LLM 自己判断效果。
        """
        if not result.success:
            self._last_action_info = f"{decision.action} 执行失败({result.error or '未知'})"
            self._stale_count += 1
            return

        # 构建纯事实摘要行
        parts = [decision.action]
        if decision.target:
            parts.append(decision.target[:15])
        elif decision.x is not None and decision.y is not None:
            parts.append(f"({decision.x:.2f},{decision.y:.2f})")
        parts.append(f"diff={result.pixel_diff_ratio:.3f}")
        self._last_action_info = " ".join(parts)

        # 只做通用 stale 追踪（用于 should_stop），不生成反馈文案
        if result.screen_changed:
            self._stale_count = 0
        else:
            self._stale_count += 1
```

**3b.** `conversation_strategy.py` 第 259-262 行，将 `_build_step_text` 的反馈前缀改为 observation 行：

```python
    def _build_step_text(self, perception: Any, ctx: ExploreContext, screenshot: Any = None) -> str:
        base = f"目标: {self._intent}\n步数: {ctx.step}/{ctx.max_steps}"
        # 上一步操作摘要（纯事实 observation，不做判断，不注入反馈文案）
        if self._last_action_info:
            base = f"[上一步] {self._last_action_info}\n{base}"
            self._last_action_info = ""
```

**3c.** `llm_explorer.py` 第 138-146 行，删除 `_last_validate_feedback` 注入逻辑：

将 第 138-146 行替换为：

```python
        # 插件校验保留调用（未来插件可能需要），但不注入反馈到 prompt
        if self._plugin_manager is not None:
            self._plugin_manager.validate(decision, result, backend=self._backend)

        self._strategy.on_action_executed(decision, result)

        if self._recorder is not None and result.success:
            self._record(decision, result)
```

（注：原来第 143-145 行是 `if hasattr(self._strategy, "_last_validate_feedback"): ...` 这段删掉）

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_conversation_strategy.py tests/test_llm_explorer.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/joker_test/explorer/conversation_strategy.py src/joker_test/explorer/llm_explorer.py tests/test_conversation_strategy.py
git commit -m "refactor(strategy): 取消系统反馈，改为纯事实 observation 行"
```

---

### Task 3: _BASE_PROMPT 简化 + OCRPlugin validate 返回 None

**Files:**
- Modify: `src/joker_test/explorer/conversation_strategy.py:39-49`
- Modify: `src/joker_test/plugins/ocr/__init__.py:89-115`

- [ ] **Step 1: 写 _BASE_PROMPT 简化 + validate 返回 None 的测试**

在 `tests/test_ocr_plugin.py` 中确认 `test_validate_always_none` 仍然通过（无改动）。

在 `tests/test_conversation_strategy.py` 中更新 `test_base_prompt_guides_goal_completion`，加断言验证 prompt 不含预设判断：

```python
def test_base_prompt_guides_goal_completion() -> None:
    """_BASE_PROMPT 无预设判断（不告诉 LLM '滑块必须用 swipe'），有完成指引。"""
    from joker_test.explorer.conversation_strategy import _BASE_PROMPT

    assert "goal_completed" in _BASE_PROMPT
    # 不预设判断：删掉"滑块必须用 swipe"之类的系统指令
    assert "点击轨道" not in _BASE_PROMPT
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_conversation_strategy.py::test_base_prompt_guides_goal_completion -v`
Expected: FAIL — `"点击轨道"` 仍在 prompt 中

- [ ] **Step 3: 改 _BASE_PROMPT + OCRPlugin.validate**

**3a.** `conversation_strategy.py` 第 39-49 行，简化规则区：

```python
规则：
- action 必须是上面 8 个之一，不要发明其他动作名
- click 的 target 填按钮上的文字；有文字的按钮直接用界面元素给的坐标作为 x/y
- 坐标是归一化 [0,1]（左0右1，上0下1），不要输出绝对像素值
- swipe 的 target 填方向 left/right/up/down，x/y 填滑块手柄的当前坐标
- 不要点击窗口的关闭(×)/最小化/最大化按钮
- 不要重复点击同一个按钮
- 绝对不要点击全屏模式
- goal_progress 描述当前进度，goal_completed 为 true 时表示目标完成
- 每步评估目标是否达成（如设置项已修改、界面已切换到目标页）。
  达成时必须输出 goal_completed=true，探索会提前结束，不浪费步数"""
```

**3b.** `ocr/__init__.py` 第 89-115 行，`validate` 方法替换为：

```python
    def validate(self, decision: Any, result: Any, backend: Any = None) -> str | None:
        """OCR 插件不做语义判断。只通过 inject_step 提供文字数据，
        LLM 自己从截图 + OCR 数据中判断操作是否生效。
        （参照 Open-AutoGLM：系统采集事实，LLM 自己推理）
        """
        return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_conversation_strategy.py tests/test_ocr_plugin.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/joker_test/explorer/conversation_strategy.py src/joker_test/plugins/ocr/__init__.py tests/test_conversation_strategy.py
git commit -m "refactor: _BASE_PROMPT 去掉预设判断，OCRPlugin.validate 返回 None"
```

---

### Task 4: 全量测试 + lint + e2e 验证

**Files:** 无改动，只验证

- [ ] **Step 1: 运行全部单元测试**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/real --ignore=tests/generated_smoke`
Expected: 全部 PASS

- [ ] **Step 2: 运行 lint**

Run: `.venv/Scripts/python.exe -m ruff check src/joker_test/explorer/conversation_strategy.py src/joker_test/explorer/llm_explorer.py src/joker_test/plugins/ocr/__init__.py`
Expected: 无错误

- [ ] **Step 3: 跑 SPD e2e 验证 conversation 结构**

启动 SPD，运行 `python scripts/e2e_spd_explore_conversation.py`，检查 trace 中：
- assistant 消息是否含 `<think>/<action>/<target>` XML
- prompt 中不再有 `上一步反馈:` 前缀
- prompt 中有 `[上一步] click xxx diff=0.xx` observation 行

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: 全量测试 + lint 通过"
```
