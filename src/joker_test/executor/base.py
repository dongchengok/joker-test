"""ExecutorBackend 协议 —— 游戏操作的引擎无关抽象（M1 核心）。

职责：把"操作游戏窗口"（截图、点击、按键、读状态）抽象成统一接口，让上层
（M2 界面探索器、M3 冒烟测试、M4 探索引擎）不关心底层用 airtest 还是别的。

设计要点：
- **Protocol + @runtime_checkable**（与 llm 包一致，DESIGN §11）。结构性子类型，
  FakeBackend/AirtestBackend 无需显式继承。
- **坐标契约：归一化 [0,1]，基准 = screenshot() 返回图像的尺寸**（G6 自洽）。
  上层做 OCR/检测拿到的 bbox 都基于 screenshot 图像坐标系，直接传 click 即可，
  不需要知道 airtest 截图尺寸 ≠ 窗口物理尺寸的转换（G6 footgun 在 Backend 内部消化）。
- **全集接口 + 渐进实现**（M1 决策）：接口声明 DESIGN §4.2 全集 8 方法 + state 属性，
  AirtestBackend 未实现的（click_image/type_text）留 NotImplementedError。
- 引擎无关（D7）：核心 = 图像识别，Poco 为 Unity 可选增强（M1 不做）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple, Protocol, runtime_checkable

# numpy.ndarray 的类型注解（运行时不必导入 numpy，注解用字符串避免硬依赖）
NDArray = "numpy.ndarray"


class BBox(NamedTuple):
    """归一化边界框（全部 [0,1]，基准 = screenshot 图像尺寸）。

    与 click(x, y) 同坐标系，保证 G6 自洽。
    x,y 是左上角，w,h 是宽高。
    """
    x: float
    y: float
    w: float
    h: float


@runtime_checkable
class LazyState(Protocol):
    """按需解析的游戏状态代理（M1 最小子集）。

    首次访问属性时触发解析（OCR/检测），之后缓存到当前帧（仿 prompts 的 lru_cache 思路，
    但按帧失效——Backend 每次新 screenshot 后 state 缓存失效）。

    M4 会扩展完整 GameState（detections/fps/memory 等，DESIGN §5.4）。
    """

    @property
    def texts(self) -> list[str]:
        """当前帧 OCR 识别出的所有文本。"""
        ...

    def find_text(self, text: str) -> BBox | None:
        """查找指定文本的位置，返回归一化 BBox；找不到返回 None。"""
        ...


@runtime_checkable
class ExecutorBackend(Protocol):
    """游戏操作后端协议。所有上层代码面向此协议，不关心具体实现。

    实现：
      - AirtestBackend（默认，airtest 图像识别，D6/R-ADR-5）
      - FakeBackend（内存模拟，CI 用）
    """

    # ===== 生命周期 =====

    def connect(self) -> None:
        """连接目标（游戏窗口）。AirtestBackend 会检测窗口可见性（G7）。"""
        ...

    def close(self) -> None:
        """释放资源（断开连接）。"""
        ...

    # ===== 感知（核心）=====

    def screenshot(self) -> NDArray:  # type: ignore[valid-type]
        """截取当前画面，返回 BGR ndarray。坐标基准由此图像尺寸决定（G6）。"""
        ...

    # ===== 操作（坐标统一归一化 [0,1]，基准 = screenshot 图像尺寸）=====

    def click(self, x: float, y: float) -> None:
        """在归一化坐标 (x, y) 处单击。x,y ∈ [0,1]。"""
        ...

    def click_text(self, text: str) -> bool:
        """找到屏幕上的指定文本并点击。成功返回 True，找不到返回 False。"""
        ...

    def click_image(self, template: str | NDArray, threshold: float = 0.8) -> bool:  # type: ignore[valid-type]
        """用模板匹配定位并点击。成功返回 True，找不到返回 False。

        Args:
            template: 图像文件路径或 ndarray（具体支持看实现）
            threshold: 匹配阈值 [0,1]，默认 0.8。越高越严格

        实现说明：AirtestBackend 复用 airtest Template（只支持文件路径，
        ndarray 会 ValueError），FakeBackend 仅记录历史。
        """
        ...

    def press_key(self, key: str) -> None:
        """按键。key 如 "escape" / "i" / "enter"。"""
        ...

    def type_text(self, text: str) -> None:
        """输入文本（到当前焦点输入框）。M1 的 AirtestBackend 留 NotImplementedError。"""
        ...

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration: float = 0.5) -> None:
        """滑动（归一化坐标 [0,1]）。"""
        ...

    def long_press(self, x: float, y: float, duration: float = 2.0) -> None:
        """长按（归一化坐标 [0,1]）。"""
        ...

    # ===== 同步 =====

    def wait_until(self, predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
        """轮询 predicate，满足返回 True；超时返回 False。默认 0.5s 间隔。"""
        ...

    # ===== 状态 =====

    @property
    def state(self) -> LazyState:
        """当前帧的状态代理（按需 OCR/检测，缓存到本帧）。"""
        ...


__all__ = ["ExecutorBackend", "LazyState", "BBox", "NDArray"]
