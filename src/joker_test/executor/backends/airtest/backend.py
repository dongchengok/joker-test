"""AirtestBackend —— 基于 airtest 的 ExecutorBackend 默认实现（图像识别为核心）。

核心方法已实现（connect/screenshot/click/click_text/click_image/press_key/
wait_until/state）。click_image 复用 airtest Template（自带 mstpl+tpl 级联 + RGB
三通道 HSV 校验 + 多尺度搜索 + 错误隔离），type_text 仍留 NotImplementedError。

依赖：airtest + rapidocr（在 [airtest] extras）。导入失败给明确提示。

关键约束（已实测，见 docs/roadmap G6/G7 + .test-targets/verify_*.py）：
- G6 坐标缩放：airtest 截图尺寸 ≠ 窗口物理尺寸，归一化坐标基准=screenshot 图像尺寸
- G7 后台限制：窗口被完全遮挡/最小化时截图失败（白屏/黑屏），connect 时检测并警告
- title_re 不加引号：connect_device("Windows:///?title_re=.*X.*")
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from joker_test.ocr.base import OCRProvider

# airtest 是 [airtest] extras，导入兜底（仿 charter_gen 的 SpecOps 兜底风格）
try:
    from airtest.core.api import connect_device, exists, keyevent, touch
    from airtest.core.cv import Template
except ImportError as e:
    raise ImportError(
        "AirtestBackend 需要 airtest。请装 airtest extras: pip install -e .[airtest]"
    ) from e

from joker_test.executor.backends.airtest.state import _AirtestState
from joker_test.executor.base import LazyState, NDArray
from joker_test.executor.coords import analyze_screenshot

logger = logging.getLogger(__name__)


class AirtestBackend:
    """基于 airtest 的 Backend。满足 ExecutorBackend 协议（结构性子类型）。

    Args:
        window_title: 目标窗口标题（子串匹配，会拼进 title_re 正则）
        ocr_enabled: 是否启用 OCR（click_text/state.texts 用）。默认 True。禁用则这些报错

    线程安全：M1 非线程安全（单线程串行），v0.2 再加锁（DESIGN §4.2）。
    """

    def __init__(
        self,
        window_title: str,
        ocr_enabled: bool = True,
        ocr: OCRProvider | None = None,
    ) -> None:
        self._window_title = window_title
        self._ocr_enabled = ocr_enabled
        self._ocr = ocr  # OCRProvider（None 时 _AirtestState 懒加载默认 RapidOCR）
        self._dev: object | None = None
        self._current_frame: NDArray | None = None  # type: ignore[valid-type]
        self._state: _AirtestState | None = None

    # ===== 生命周期 =====

    def connect(self) -> None:
        """连接游戏窗口 + G7 可见性健康检测。

        title_re 不加引号（实测 pywinauto 不认引号，见 AGENTS.md footgun 7）。
        """
        # 子串拼进正则（用 .* 前后通配，比精确匹配鲁棒）
        self._dev = connect_device(f"Windows:///?title_re=.*{self._window_title}.*")

        # G7 健康检测：截一张图看是不是黑屏/白屏
        try:
            frame = self.screenshot()
            health = analyze_screenshot(frame)
            if "失败" in health or "异常" in health:
                logger.warning(
                    "窗口截图异常: %s。可能窗口被遮挡或已最小化（G7）。"
                    "请保持游戏窗口在屏幕上可见。",
                    health,
                )
            else:
                logger.info("已连接窗口 '%s'，截图健康: %s", self._window_title, health)
        except Exception as e:  # noqa: BLE001
            logger.warning("connect 后健康自检失败: %s", e)

    def close(self) -> None:
        # airtest 的 Windows backend 无显式断开，释放引用即可
        self._dev = None

    # ===== 感知 =====

    def screenshot(self) -> NDArray:  # type: ignore[valid-type]
        """截图，返回 BGR ndarray。坐标基准由此图像尺寸决定（G6）。"""
        import numpy as np  # noqa: PLC0415

        # 全局 snapshot() 不传 filename 时返回 None（G6 实测）。
        # 改用底层设备方法：G.DEVICE.snapshot() 返回 numpy ndarray（BGR）。
        from airtest.core.api import G  # noqa: PLC0415

        img = G.DEVICE.snapshot(filename=None, quality=99)
        if img is None:
            # 兜底：部分 backend 需要显式调 minicap/rotation
            raise RuntimeError("截图返回 None（G7：窗口可能被遮挡或最小化）")
        if isinstance(img, np.ndarray):
            frame = img
        else:
            frame = np.array(img)
        self._current_frame = frame
        # 新帧 → state 缓存失效
        if self._state is not None:
            self._state.invalidate()
        return frame

    # ===== 操作（归一化坐标，基准=screenshot 图像尺寸）=====

    def click(self, x: float, y: float) -> None:
        """归一化坐标点击。airtest 的 touch 直接接受 [0,1] 归一化坐标。"""
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            raise ValueError(f"click 坐标必须在 [0,1]，收到 ({x}, {y})")
        touch((x, y))

    def click_text(self, text: str) -> bool:
        """OCR 定位文本 → 点 bbox 中心。找不到返回 False。"""
        if not self._ocr_enabled:
            raise RuntimeError("OCR 未启用（ocr_enabled=False）")
        bbox = self.state.find_text(text)
        if bbox is None:
            return False
        self.click(bbox.x + bbox.w / 2, bbox.y + bbox.h / 2)
        return True

    def click_image(self, template: str | NDArray, threshold: float = 0.8) -> bool:  # type: ignore[valid-type]
        """用模板匹配定位并点击。成功返回 True，找不到返回 False。

        复用 airtest 的 Template + exists + touch（白嫖 airtest aircv 全部能力）：
        - mstpl→tpl 算法级联（MultiScaleTemplateMatching 降级 TemplateMatching）
        - RGB 三通道 HSV 校验（挡"灰度相似但颜色不同"的误匹配）
        - 多尺度搜索（抗分辨率变化）
        - 错误隔离（单算法异常自动降级）

        Args:
            template: 图像文件路径（airtest Template 要求 filepath，绝对路径最稳）。
                ndarray 不支持（airtest Template 内部用 cv2.imread 读路径），
                如需 ndarray 模板匹配请用 perception.ImageMatcher。
            threshold: 匹配阈值 [0,1]，默认 0.8

        坐标由 airtest Windows backend 处理（内部支持绝对/归一化坐标），
        不走 self.click 归一化路径 —— airtest 自己的坐标闭环更准。
        """
        if not isinstance(template, str):
            raise ValueError(
                "click_image 暂只支持文件路径模板（airtest Template 要求 filepath）。"
                "ndarray 模板请用 perception.ImageMatcher。"
            )

        tpl = Template(template, threshold=threshold, rgb=True)
        pos = exists(tpl)  # 返回 (x,y) 像素坐标 或 False
        if not pos:
            return False
        touch(pos)  # airtest touch 自带坐标转换，直接点
        return True

    def press_key(self, key: str) -> None:
        """按键。key 如 'escape'/'i'/'enter'（airtest keyevent 语义）。"""
        keyevent(key)

    def type_text(self, text: str) -> None:
        """输入文本（到当前焦点输入框）。

        用 airtest 的 text（Windows 下走 pywinauto SendKeys，逐字符发送）。
        适合纯 ASCII（如游戏控制台命令 `pve ghostPvpShadow 70001 --local`）。
        中文/多字节文本 Windows 下不可靠（SendKeys 不直接支持 unicode），需剪贴板方案。
        """
        from airtest.core.api import text as air_text  # noqa: PLC0415

        air_text(text, enter=False)

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration: float = 0.5) -> None:
        """滑动（归一化坐标 [0,1]）。"""
        from airtest.core.api import swipe  # noqa: PLC0415

        swipe((x1, y1), (x2, y2), duration=duration)

    def long_press(self, x: float, y: float, duration: float = 2.0) -> None:
        """长按（归一化坐标 [0,1]）。

        airtest 无 long_click，用 touch(pos, duration=...) 实现（Windows backend 支持）。
        """
        touch((x, y), duration=duration)

    # ===== 同步 =====

    def wait_until(self, predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
        """轮询 predicate，满足返回 True；超时返回 False。默认 0.5s 间隔。

        每次循环前自动 screenshot() 刷新帧 —— 否则 state.texts 读的是旧帧缓存，
        永远等不到切屏（click/press_key 后必须刷帧才能看到新界面）。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.screenshot()  # 刷新帧 + invalidate state 缓存
            if predicate():
                return True
            time.sleep(0.5)
        self.screenshot()  # 最后一次也刷新
        return predicate()

    # ===== 状态 =====

    @property
    def state(self) -> LazyState:
        if self._state is None:
            self._state = _AirtestState(
                get_frame=self._get_current_frame, ocr=self._ocr
            )
        return self._state

    @property
    def dev(self) -> object | None:
        """底层 airtest 设备对象（录制器做坐标换算 + 前台窗口判断用）。

        connect() 前为 None。暴露给 flow 包的录制器用，不进 ExecutorBackend 协议。
        """
        return self._dev

    def _get_current_frame(self) -> NDArray:  # type: ignore[valid-type]
        """_AirtestState 的帧回调：懒截图（若无当前帧则现截一张）。"""
        if self._current_frame is None:
            self.screenshot()
        return self._current_frame  # type: ignore[return-value]


__all__ = ["AirtestBackend"]
