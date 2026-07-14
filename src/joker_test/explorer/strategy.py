"""探索策略协议 + 数据契约。

ExploreStrategy 是可替换的探索决策层。LLMExplorer 外壳持有策略实例，
每步调 decide() 获取决策，执行后调 on_action_executed() 更新策略内部状态。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from joker_test.executor import get_active_backend

if TYPE_CHECKING:
    pass

ExploreAction = Literal[
    "click", "press_key", "type_text",
    "swipe", "scroll", "long_press",
    "back", "stop",
]

# LLM 可能输出的别名 → 标准动作映射
ACTION_ALIASES: dict[str, str] = {
    "tap": "click",
    "touch": "click",
    "click_text": "click",
    "click_coord": "click",
    "press": "press_key",
    "hotkey": "press_key",
    "key": "press_key",
    "keyboard": "press_key",
    "type": "type_text",
    "input": "type_text",
    "input_text": "type_text",
    "swipe_up": "swipe",
    "swipe_down": "swipe",
    "swipe_left": "swipe",
    "swipe_right": "swipe",
    "drag": "swipe",
    "long_click": "long_press",
    "hold": "long_press",
    "press_and_hold": "long_press",
    "scroll_down": "scroll",
    "scroll_up": "scroll",
    "return": "back",
    "escape": "back",
    "navigate_back": "back",
    "home": "back",
    "wait": "stop",
    "finish": "stop",
    "done": "stop",
    "complete": "stop",
    "end": "stop",
}

# 标准动作集合
VALID_ACTIONS = {"click", "press_key", "type_text", "swipe", "scroll", "long_press", "back", "stop"}

# 探索动作的 tool_use schema（Anthropic 格式）
# 传给 LLMProvider.create(tool_schema=...) 强制结构化输出
EXPLORE_TOOL_SCHEMA: dict[str, Any] = {
    "name": "execute_action",
    "description": "执行一个游戏探索操作",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["click", "press_key", "type_text", "swipe", "scroll", "long_press", "back", "stop"],
                "description": "动作类型",
            },
            "target": {
                "type": "string",
                "description": "操作目标：click=按钮文字(OCR里的)，纯图标留空用x/y；swipe=left/right/up/down方向；press_key=键名",
            },
            "x": {
                "type": "number",
                "description": "归一化横坐标 [0,1]（左0右1）。纯图标按钮必填，有文字的按钮可不填",
            },
            "y": {
                "type": "number",
                "description": "归一化纵坐标 [0,1]（上0下1）。纯图标按钮必填，有文字的按钮可不填",
            },
            "think": {
                "type": "string",
                "description": "推理过程：为什么选这个动作",
            },
            "goal_progress": {
                "type": "string",
                "description": "当前进度描述",
            },
            "goal_completed": {
                "type": "boolean",
                "description": "目标是否已完成",
            },
        },
        "required": ["action"],
    },
}


def normalize_action(raw: str) -> str:
    """把 LLM 输出的动作名标准化，未知动作降级为 stop。"""
    action = raw.strip().lower()
    action = ACTION_ALIASES.get(action, action)
    if action not in VALID_ACTIONS:
        action = "stop"
    return action


def parse_coords(
    inp: dict[str, Any],
    screenshot: Any = None,
) -> tuple[float | None, float | None]:
    """从 tool_use input 解析坐标，绝对像素自动转归一化。

    支持 x/y 字段。值 >1 视为绝对像素，按截图实际尺寸归一化。
    尺寸来源：截图 .shape → 全局 backend（get_explore_backend）→ 钳位。

    不再硬编码屏幕尺寸——换机器/换游戏自动适配。
    """
    x = inp.get("x")
    y = inp.get("y")
    if x is None and y is None:
        return None, None
    fx = float(x) if x is not None else None
    fy = float(y) if y is not None else None

    # 尝试多种途径获取实际截图尺寸
    h = w = 0
    if screenshot is not None:
        try:
            h, w = screenshot.shape[:2]
        except Exception:
            pass
    if (h == 0 or w == 0):
        backend = get_active_backend()
        if backend is not None:
            try:
                frame = backend.screenshot()
                h, w = frame.shape[:2]
            except Exception:
                pass

    if fx is not None and fx > 1.0:
        fx = min(max(fx / w, 0.0), 1.0) if w > 0 else min(max(fx, 0.0), 1.0)
    if fy is not None and fy > 1.0:
        fy = min(max(fy / h, 0.0), 1.0) if h > 0 else min(max(fy, 0.0), 1.0)
    return fx, fy


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
    """单步决策结果。click 统一带坐标(x,y)和文本(target)，至少一个非空。"""

    __test__ = False
    think: str
    action: ExploreAction
    stop: bool = False
    goal_progress: str = ""
    goal_completed: bool = False
    target: str = ""
    x: float | None = None
    y: float | None = None
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
        self,
        decision: StepDecision,
        result: ActionResult,
        validate_feedback: str = "",
    ) -> None:
        """动作执行后回调，更新内部状态。

        Args:
            decision: 本步决策
            result: 执行结果（像素 diff 层）
            validate_feedback: 插件语义校验反馈（空串 = 无问题/无插件）
        """
        ...

    def should_stop(self) -> bool:
        """是否该停止。"""
        ...

    def get_state_map(self) -> Any:
        """返回构建的 StateMap。"""
        ...
