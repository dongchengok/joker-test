"""joker_test.executor —— 游戏操作抽象层。

公开 ExecutorBackend 协议、LazyState 协议、BBox 类型，以及具体实现。
具体实现在 backends/ 子目录（DESIGN §11.1）。
"""

from joker_test.executor.base import BBox, ExecutorBackend, LazyState

__all__ = ["ExecutorBackend", "LazyState", "BBox"]
