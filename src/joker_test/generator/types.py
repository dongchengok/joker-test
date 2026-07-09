"""用例生成产物数据结构（M3a）。

LLM 生成的一个测试单元 = 一个被测系统的一份 pytest 代码 + Pydantic spec。
M3b 执行层消费这些产物（落盘为 test_*.py + *_spec.py）。
"""

from __future__ import annotations

from pydantic import BaseModel


class GeneratedTest(BaseModel):
    """LLM 生成的一个测试单元（一个系统的一份代码+spec）。

    分两层（ADR-008：测试逻辑层 + 数据层分离）：
      - test_code：pytest 测试函数（逻辑层），用 fixture（backend/game_state）
      - spec_code：Pydantic BaseModel 定义（数据层），参数化测试输入

    Attributes:
        system: 被测系统名（如 "inventory" / "铁匠铺"），用于文件名和报告
        test_code: test_<system>.py 的完整源码
        spec_code: <system>_spec.py 的完整源码（Pydantic spec）
        test_filename: 测试文件名（如 "test_inventory.py"）
        spec_filename: spec 文件名（如 "inventory_spec.py"）
    """

    system: str
    test_code: str
    spec_code: str
    test_filename: str
    spec_filename: str


__all__ = ["GeneratedTest"]
