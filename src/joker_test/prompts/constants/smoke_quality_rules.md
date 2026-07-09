冒烟测试用例质量标准：

1. **文件命名**：测试文件 `test_<system>.py`，spec 文件 `<system>_spec.py`（system 用英文小写+下划线）

2. **测试函数**：每个函数只测一件事，命名 `test_<测试目标>_<测试点>`（如 `test_inventory_open_close`）

3. **fixture 用法**：必须用 `backend` fixture（已定义，类型 ExecutorBackend），不要自己 connect/close
   - `backend` fixture 已由 conftest 自动提供，**不要 import backend**
   - `backend.state.texts` 获取当前界面 OCR 文本列表
   - `backend.click_text("按钮文本")` 点击按钮
   - `backend.wait_until(predicate, timeout)` 等待条件
   - `backend.press_key("escape")` 按键
   - 若需类型注解，正确路径是 `from joker_test.executor.base import ExecutorBackend`

4. **断言风格**：用 pytest 原生 assert，断言要具体可判断（如 `assert "背包" in backend.state.texts`）

5. **Pydantic spec**：spec 文件定义 BaseModel，字段带类型 + 校验（如 `severity: Literal["P0","P1","P2"]`），供参数化测试用

6. **不要**：
   - 不要写 `if __name__ == "__main__"` 块
   - 不要 import joker_test 内部模块（除 ExecutorBackend 类型注解）
   - 不要写需要真实游戏运行才能通过的硬编码断言（用 fixture 抽象）
