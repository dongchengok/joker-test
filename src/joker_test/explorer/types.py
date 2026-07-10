"""状态地图数据结构（pydantic）。

M2 的核心产出物：探索器遍历游戏界面后，产出 StateMap（状态地图）JSON。
这个 JSON 是编排架构里"探查类命令"的输出（D10），上层（M3 用例生成、外部 harness）依赖它。

设计要点：
- 坐标全部归一化 [0,1]，基准=screenshot 图像尺寸（与 ExecutorBackend 契约一致，G6 自洽）
- Screen 有 fingerprint（去重指纹），Explorer 用它判断两个界面是否相同
- Exit 编码图结构（界面 A 点按钮 → 界面 B），含切屏证据（pixel_diff_ratio）
- StateMap 可序列化为 JSON 落盘（pydantic .model_dump()）
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 元素类型分类（detection.py 产出）
# - button：可点击文本（OCR 识别到 + 有 bbox）
# - icon：无文本图标（cv2 模板匹配识别）
# - label：静态文本（不可点击，如标题）
ElementType = Literal["button", "icon", "label"]

# Exit 的动作类型（对应 ExecutorBackend 的方法）
ExitAction = Literal["click_text", "click", "press_key"]


class UIElement(BaseModel):
    """界面上的一个 UI 元素。"""

    type: ElementType
    text: str | None = None  # OCR 文本（button/label 有，icon 为 None）
    bbox: tuple[float, float, float, float]  # 归一化 [x, y, w, h]
    template_ref: str | None = None  # 模板图像路径（icon 才有）


class Exit(BaseModel):
    """从一个界面出去的边（图结构的出边）。

    记录"点哪个元素 → 到哪个界面"，含切屏证据。
    """

    from_screen: str  # 源界面 id
    element_text: str | None  # 触发元素（点击哪个按钮），press_key 时为 None
    action: ExitAction  # 动作类型
    to_screen: str  # 目标界面 id（"unknown"=未探索/死路）
    evidence: dict[str, float] = Field(default_factory=dict)  # {"pixel_diff_ratio": 0.21}


class Screen(BaseModel):
    """单个界面的记录。"""

    id: str  # 界面标识（slug，如 "root" / "screen_1"）
    name: str = ""  # 界面名（LLM/人类起的可读名，如"主菜单"；默认空串）
    elements: list[UIElement]  # 该界面的元素
    exits: list[Exit] = Field(default_factory=list)  # 出边
    entry: dict[str, object] | None = None  # 怎么来的（根界面为 None）
    fingerprint: str  # 去重指纹（元素文本签名 hash）
    screenshot_ref: str | None = None  # 截图保存路径（可选）


class StateMap(BaseModel):
    """完整状态地图（M2 最终产出）。"""

    screens: list[Screen]
    root_screen_id: str  # 起始界面
    explored_at: str  # ISO 时间戳
    backend_info: dict[str, object] = Field(default_factory=dict)  # {"type":..., "window":...}


__all__ = [
    "UIElement",
    "Exit",
    "Screen",
    "StateMap",
    "ElementType",
    "ExitAction",
]
