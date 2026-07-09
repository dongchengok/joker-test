"""aircv 错误类型（抄自 airtest 1.4.3 aircv/error.py，去耦版）。

只保留模板匹配用到的错误，去掉 airtest 专有的 NoModuleError/BaseError 体系。
"""

from __future__ import annotations


class TemplateInputError(Exception):
    """模板匹配输入错误（如模板比截图大、图像无效）。"""


class BaseError(Exception):
    """aircv 算法层基础错误（保留用于异常隔离）。"""


__all__ = ["TemplateInputError", "BaseError"]
