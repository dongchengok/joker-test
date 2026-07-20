# OCR 提示词辅助化 + 探索提前停止 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 OCRPlugin 系统提示词从"直接用坐标"改为"辅助参考"（区分按钮可用、滑块不可用），并在 `_BASE_PROMPT` 中引导 LLM 主动输出 `goal_completed=true` 实现提前停止。

**Architecture:** 纯提示词改动，零新代码。OCRPlugin 的 `inject_system_prompt()` 重写为辅助定位描述（区分按钮 vs 图形控件）。`_BASE_PROMPT` 规则区加一条完成判定引导。两处改动各配一个测试断言关键词存在。

**Tech Stack:** Python 3.12, pytest, 无外部依赖

---

## File Structure

| 文件 | 改动类型 | 职责 |
|---|---|---|
| `src/joker_test/plugins/ocr/__init__.py` | 修改 | OCRPlugin.inject_system_prompt 提示词重写 |
| `src/joker_test/explorer/conversation_strategy.py` | 修改 | _BASE_PROMPT 规则区加完成判定引导 |
| `tests/test_ocr_plugin.py` | 修改 | 更新系统提示词断言 |
| `tests/test_conversation_strategy.py` | 修改 | 新增 prompt 完成判定引导断言 |

---

### Task 1: OCRPlugin 系统提示词辅助化

**Files:**
- Modify: `src/joker_test/plugins/ocr/__init__.py:45-51`
- Test: `tests/test_ocr_plugin.py:9-14`

- [ ] **Step 1: 更新系统提示词测试断言**

把 `test_inject_system_prompt_has_format_explanation` 改为验证辅助化后的关键词。旧断言只检查"文字@"和"归一化"，新断言额外验证区分按钮 vs 滑块的辅助说明。

`tests/test_ocr_plugin.py` 中将 `test_inject_system_prompt_has_format_explanation` 替换为：

```python
def test_inject_system_prompt_explains_auxiliary_role():
    """系统提示词说明 OCR 坐标是辅助参考，区分按钮（可用）和滑块（不可用）。"""
    p = OCRPlugin()
    frag = p.inject_system_prompt()
    # 基本格式说明仍在
    assert "文字@" in frag
    assert "归一化" in frag or "[0,1]" in frag
    # 辅助定位说明
    assert "辅助" in frag
    # 区分按钮 vs 图形控件
    assert "按钮" in frag
    assert "滑块" in frag
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ocr_plugin.py::test_inject_system_prompt_explains_auxiliary_role -v`
Expected: FAIL — `assert "辅助" in frag` 失败（旧提示词没有"辅助"字样）

- [ ] **Step 3: 重写 inject_system_prompt 提示词**

`src/joker_test/plugins/ocr/__init__.py` 第 45-51 行，将 `inject_system_prompt` 方法替换为：

```python
    def inject_system_prompt(self) -> str:
        """告诉 LLM OCR 坐标是辅助参考（文字位置，非控件手柄位置）。"""
        return (
            "界面元素格式：文字@(x,y)，x/y 是归一化坐标[0,1]"
            "（左0右1，上0下1），表示该文字在屏幕上的中心位置。\n"
            "这是辅助参考信息，帮助定位界面元素：\n"
            "- 按钮/菜单项：可以直接用文字坐标作为点击位置\n"
            "- 滑块/进度条等图形控件：坐标是文字标签位置，不是手柄位置。"
            "需要从截图视觉定位手柄，不能用 OCR 文字坐标拖拽"
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_ocr_plugin.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/joker_test/plugins/ocr/__init__.py tests/test_ocr_plugin.py
git commit -m "refactor(ocr-plugin): 系统提示词从'直接用坐标'改为'辅助参考'，区分按钮和滑块"
```

---

### Task 2: _BASE_PROMPT 加完成判定引导

**Files:**
- Modify: `src/joker_test/explorer/conversation_strategy.py:48-49`
- Test: `tests/test_conversation_strategy.py`

- [ ] **Step 1: 写完成判定引导的测试**

在 `tests/test_conversation_strategy.py` 末尾追加：

```python
def test_base_prompt_guides_goal_completion() -> None:
    """_BASE_PROMPT 引导 LLM 在目标达成时输出 goal_completed=true。"""
    from joker_test.explorer.conversation_strategy import _BASE_PROMPT

    assert "goal_completed" in _BASE_PROMPT
    # 有明确的完成判定引导（不只是字段说明，还要引导主动输出）
    assert "完成" in _BASE_PROMPT or "达成" in _BASE_PROMPT
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_conversation_strategy.py::test_base_prompt_guides_goal_completion -v`
Expected: 可能 PASS 也可能 FAIL（取决于"完成"是否已在 prompt 中）。如果 PASS 说明现有文本已覆盖，仍继续 Step 3 加更强的引导。

- [ ] **Step 3: 在 _BASE_PROMPT 规则区加完成判定引导**

`src/joker_test/explorer/conversation_strategy.py` 第 48-49 行，将最后一行规则替换为：

```python
- goal_progress 描述当前进度，goal_completed 为 true 时表示目标完成
- 每步评估目标是否达成（如设置项已修改、界面已切换到目标页）。
  达成时必须输出 goal_completed=true，探索会提前结束，不浪费步数"""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_conversation_strategy.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src/joker_test/explorer/conversation_strategy.py tests/test_conversation_strategy.py
git commit -m "feat(prompt): _BASE_PROMPT 加完成判定引导，让 LLM 主动输出 goal_completed"
```

---

### Task 3: 全量测试验证 + 清理

**Files:**
- 无文件改动，仅验证

- [ ] **Step 1: 运行全部单元测试**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/real --ignore=tests/generated_smoke`
Expected: 全部 PASS（应为 223+，因为 Task 1 替换了 1 个测试 + Task 2 新增了 1 个测试，总数不变或 +1）

- [ ] **Step 2: 检查 lint**

Run: `.venv/Scripts/python.exe -m ruff check src/joker_test/plugins/ocr/__init__.py src/joker_test/explorer/conversation_strategy.py`
Expected: 无错误

- [ ] **Step 3: 最终 Commit（如有 lint 修复）**

```bash
git add -A
git commit -m "chore: lint clean after prompt changes"
```
（如果 lint 无问题则跳过此步）
