"""测试报告数据契约 + TestReporter 协议（DESIGN §5.5 + §4.7.1）。

TestSession/TestCase/TestResult/TestStep/Attachment 五个数据结构，
+ TestReporter Protocol（报告器接口）。
reporters 包和 runner 共享这些类型（pytest 事件转译成这些对象）。
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field, computed_field

TestStatus = Literal["passed", "failed", "skipped", "error"]


class TestStep(BaseModel):
    """测试中的一个步骤（如一次操作 + 截图）。"""

    name: str
    description: str = ""
    screenshot_ref: str | None = None


class Attachment(BaseModel):
    """测试附件（截图/日志/状态快照）。"""

    name: str
    data: str  # 文本数据或文件路径
    type: Literal["png", "jpg", "mp4", "json", "text", "html"] = "text"


class TestCase(BaseModel):
    """单个测试用例的元信息。"""

    # 告诉 pytest 不要把这个类当测试收集（名字以 Test 开头会被误收）
    __test__ = False

    name: str  # test 函数名
    module: str = ""  # 模块名
    tags: list[str] = Field(default_factory=list)
    severity: str = "P1"
    steps: list[TestStep] = Field(default_factory=list)


class TestResult(BaseModel):
    """单个测试的执行结果。"""

    __test__ = False

    test: TestCase
    status: TestStatus
    duration: float = 0.0
    error: str | None = None
    attachments: list[Attachment] = Field(default_factory=list)


class TestSession(BaseModel):
    """一次测试会话（一次 pytest 运行）。"""

    __test__ = False

    id: str
    started_at: str  # ISO 时间戳
    game: str = "unknown"
    backend: str = "unknown"
    tests: list[TestCase] = Field(default_factory=list)
    results: list[TestResult] = Field(default_factory=list)
    finished_at: str | None = None

    @computed_field  # type: ignore[prop-decorator]  # mypy 对叠加装饰器限制，写法为 Pydantic 官方推荐
    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == "passed")

    @computed_field  # type: ignore[prop-decorator]  # mypy 对叠加装饰器限制，写法为 Pydantic 官方推荐
    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")


@runtime_checkable
class TestReporter(Protocol):
    """报告器协议（DESIGN §4.7.1）。事件驱动，7 个 hook。

    实现者：JsonReporter / HtmlReporter / MultiReporter（组合 + 错误隔离）。
    结构性子类型，无需继承（与 LLMProvider/ExecutorBackend 风格一致）。

    注意：类名以 Test 开头会被 pytest 误当测试类收集，
    通过 pyproject.toml 的 python_classes 配置排除（只收集 test_ 开头的函数）。
    """

    name: str

    def on_session_start(self, session: TestSession) -> None: ...
    def on_test_start(self, test: TestCase) -> None: ...
    def on_test_end(self, result: TestResult) -> None: ...
    def on_session_end(self, session: TestSession) -> None: ...
    def finalize(self) -> str: ...  # 返回报告路径或摘要


__all__ = [
    "TestSession",
    "TestCase",
    "TestResult",
    "TestStep",
    "Attachment",
    "TestStatus",
    "TestReporter",
]
