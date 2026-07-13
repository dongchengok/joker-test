"""joker_test.executor —— 游戏操作抽象层。

公开 ExecutorBackend 协议、LazyState 协议、BBox 类型，以及具体实现。
具体实现在 backends/ 子目录（DESIGN §11.1）。

全局 backend 注册：backend 在一次运行中是固定的，选定了就不变（探索/固化/执行共用）。
set_active_backend(backend) 在程序入口注册一次，get_active_backend() 全局获取。
仿 trace 的 set_tracer/get_tracer 模式。
"""
from __future__ import annotations

from typing import Any

from joker_test.executor.base import BBox, ExecutorBackend, LazyState

# ---- 全局 backend 注册 ----
_active_backend: Any = None


def set_active_backend(backend: Any) -> None:
    """注册当前使用的 backend。程序入口（CLI/脚本/测试 conftest）调用一次。"""
    global _active_backend
    _active_backend = backend


def get_active_backend() -> Any:
    """获取当前注册的 backend。未注册返回 None。"""
    return _active_backend


__all__ = ["ExecutorBackend", "LazyState", "BBox", "set_active_backend", "get_active_backend"]
