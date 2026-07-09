"""FakeBackend 包（内存模拟，CI/测试用）。

M1：单屏无状态。M2：支持多屏状态机（ScreenCfg + transitions）。
"""

from joker_test.executor.backends.fake.backend import FakeBackend, ScreenCfg

__all__ = ["FakeBackend", "ScreenCfg"]
