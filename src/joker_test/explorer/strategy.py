"""探索策略协议 + 数据契约。

ExploreStrategy 是可替换的探索决策层。LLMExplorer 外壳持有策略实例，
每步调 decide() 获取决策，执行后调 on_action_executed() 更新策略内部状态。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

if TYPE_CHECKING:
    pass

ExploreAction = Literal[
    "click_text", "click_coord", "press_key", "type_text",
    "swipe", "scroll", "long_press",
    "back", "stop",
]


class ExploreContext(BaseModel):
    """每步决策的公共上下文（LLMExplorer 提供）。"""

    __test__ = False
    step: int
    max_steps: int
    intent: str
    backend: Any  # ExecutorBackend（Any 避免 Pydantic 序列化协议实例）
    llm: Any  # LLMProvider
    recorder: Any | None = None  # GlobalRecorder


class StepDecision(BaseModel):
    """单步决策结果。"""

    __test__ = False
    think: str
    action: ExploreAction
    stop: bool = False
    goal_progress: str = ""
    goal_completed: bool = False
    description: str = ""


class ActionResult(BaseModel):
    """动作执行结果（LLMExplorer 执行后回填）。"""

    __test__ = False
    success: bool
    screen_changed: bool
    pixel_diff_ratio: float = 0.0
    error: str | None = None


@runtime_checkable
class ExploreStrategy(Protocol):
    """探索策略协议。"""

    def decide(
        self,
        screenshot: Any,
        perception: Any,
        ctx: ExploreContext,
    ) -> StepDecision:
        """看当前截图 + perception，决策下一步。"""
        ...

    def on_action_executed(
        self, decision: StepDecision, result: ActionResult
    ) -> None:
        """动作执行后回调，更新内部状态。"""
        ...

    def should_stop(self) -> bool:
        """是否该停止。"""
        ...

    def get_state_map(self) -> Any:
        """返回构建的 StateMap。"""
        ...
