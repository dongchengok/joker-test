"""录制操作流数据结构（pydantic）。

录制器（recorder.py）的核心产出物：用户操作游戏时，把每一步操作（点击/按键/输入）
冻结成一个 RecordedStep，多条 step 组成 RecordedFlow。这个 flow 是**中间产物**，
最终会被 RecordedFlowGenerator（generator.py）交给 LLM 重组成 pytest test_case。

设计要点：
- 两种输入源统一走 RecordedStep：pynput 监听人类操作（screen_x/y + window 几何快照）
  和程序化 backend 调用（x/y 归一化坐标），都在同一个结构里
- 坐标双轨：screen_x/screen_y 是屏幕像素（pynput 模式冻结），x/y 是归一化 [0,1]
  （程序化模式直接给，或生成阶段从 screen 坐标换算出来）
- 每步带 before/after OCR 文本 + 截图引用，供语义化（semantics.py）和 LLM 理解用
- __test__ = False：类名不以 Test 开头但防 pytest 误收集（§11.10 约定）

与 explorer/types.py 的关系：ExitAction（图边动作）是"界面拓扑图"的概念，
FlowAction（操作流动作）是"时间序列"的概念，语义相近但用途不同，故独立定义。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 操作流的动作类型（对应 ExecutorBackend 的方法 + click_text 语义化结果）
# - click：坐标点击（pynput 录的原始坐标，或程序化给的归一化坐标）
# - click_text：语义化后的文字点击（semantics.py 产出，或程序化直接给）
# - click_image：语义化后的图像模板点击（semantics.py 产出）
# - click_coord：语义化后的归一化坐标点击（纯图标界面 OCR 无文字时用，semantics.py 产出）
# - press_key：按键
# - type_text：文本输入（pynput 键盘聚合后，或程序化直接给）
FlowAction = Literal["click", "click_text", "click_image", "click_coord", "press_key", "type_text"]

# 语义化置信度（semantics.py 产出）
# - high：OCR 找到文字（→click_text）或纯图标界面用坐标（→click_coord）
# - none：无法语义化（无坐标/press_key 等无需语义化的动作）
SemanticConfidence = Literal["high", "none"]


class WindowInfo(BaseModel):
    """操作发生时的目标窗口几何快照（pynput 回调时冻结）。

    pynput 模式下，每次点击回调同步抓取点击位置下的顶层窗口 hwnd + 矩形 + 标题，
    冻结成不可变快照。这样即使后续窗口移动/关闭，换算坐标仍基于点击时的几何。

    程序化模式（backend 调用）通常不填（留 hwnd=0），因为程序化直接给归一化坐标。
    """

    __test__ = False

    hwnd: int = 0
    title: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom（屏幕像素）
    width: int = 0
    height: int = 0


class RecordedStep(BaseModel):
    """录制的单个操作步骤。

    两种输入源的字段使用约定：
      - pynput 模式：填 screen_x/screen_y + window（几何快照），x/y 留空（生成阶段换算）
      - 程序化模式：填 x/y（归一化坐标），screen_x/screen_y + window 留空
    """

    __test__ = False

    action: FlowAction
    # 坐标（pynput 模式填 screen，程序化模式填归一化 x/y）
    screen_x: int = 0
    screen_y: int = 0
    x: float | None = None  # 归一化 [0,1]（click / click_text / click_image 的目标位置）
    y: float | None = None
    window: WindowInfo | None = None  # pynput 模式的窗口几何快照

    # 动作参数
    key: str | None = None  # press_key：airtest keyname（如 "escape" / "enter"）
    text: str | None = None  # click_text 的目标文字 / type_text 的完整文本

    # 语义化辅助信息（录制时采集，供 semantics.py 转换用）
    ocr_texts_before: list[str] = Field(default_factory=list)
    ocr_texts_after: list[str] = Field(default_factory=list)
    screenshot_before: str | None = None  # 操作前截图路径
    screenshot_after: str | None = None  # 操作后截图路径

    note: str = ""  # 人写的备注（录制时可加，生成时展示给 LLM）
    elapsed_s: float = 0.0  # 录制时刻相对开始的秒数


class RecordedFlow(BaseModel):
    """完整操作流（一次录制 = 一个目录产物）。

    录制产物是目录（含 flow.yaml + screenshots/），不是单文件，因为含截图。
    """

    __test__ = False

    name: str  # LLM 起的中文名（如 "进入地牢流程"），用于目录命名
    description: str = ""  # LLM 填的用途说明
    steps: list[RecordedStep] = Field(default_factory=list)
    recorded_at: str = ""  # ISO 时间戳
    screenshots_dir: str = ""  # 截图目录（相对 flow.yaml 的路径，通常 "screenshots"）


class SemanticResult(BaseModel):
    """坐标语义化结果（semantics.py 产出）。

    把 RecordedStep 的原始坐标（screen 或归一化）转换成稳定的语义定位。
    按界面能否 OCR 分两路：
      - 有文字的界面 → click_text（OCR 文字最稳定，回放时重新定位）
      - 纯图标界面（OCR 无文字）→ click_coord（归一化坐标，固定窗口+分辨率下稳定）

    为什么不用图像模板：纯图标界面的截图必然含动态内容（等级数字/血条/时间），
    第二次跑模板匹配必然失败。模板这条路在纯图标界面两头不讨好——有文字用
    click_text 就够，无文字用坐标更可靠，故砍掉模板匹配。
    """

    __test__ = False

    locator_type: Literal["click_text", "click_coord", "none"]
    text: str | None = None  # click_text 的文字
    x: float | None = None  # click_coord 的归一化 x
    y: float | None = None  # click_coord 的归一化 y
    confidence: SemanticConfidence = "none"
    warning: str = ""  # 失败时的警告说明


class FlowGenResult(BaseModel):
    """录制→生成 test_case 的最终结果（汇总）。"""

    __test__ = False

    flow_dir: str  # 录制产物目录（含 flow.yaml + screenshots/）
    flow_name: str  # LLM 起的中文名
    test_files: list[str] = Field(default_factory=list)  # 生成的 test_*.py 路径列表
    warnings: list[str] = Field(default_factory=list)  # 语义化冲突等警告


__all__ = [
    "FlowAction",
    "SemanticConfidence",
    "WindowInfo",
    "RecordedStep",
    "RecordedFlow",
    "SemanticResult",
    "FlowGenResult",
]
