"""joker_test.executor.backends —— ExecutorBackend 的具体实现。

- AirtestBackend（默认，图像识别为核心，需要 [airtest] extras）
- FakeBackend（内存模拟，CI/测试用，无外部依赖）
"""

from joker_test.executor.backends.fake.backend import FakeBackend

__all__ = ["FakeBackend"]

# AirtestBackend 依赖 airtest extras，延迟导入（导入失败不影响 FakeBackend 用户）
try:
    from joker_test.executor.backends.airtest.backend import AirtestBackend  # noqa: F401
except ImportError:
    pass
else:
    __all__.append("AirtestBackend")
