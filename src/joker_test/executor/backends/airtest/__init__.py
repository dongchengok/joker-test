"""AirtestBackend 包（基于 airtest 的默认实现）。

导入本包需要 airtest extras（pip install -e .[airtest]）。
"""

from joker_test.executor.backends.airtest.backend import AirtestBackend

__all__ = ["AirtestBackend"]
