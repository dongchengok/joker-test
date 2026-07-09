"""GlobalRecorder —— 操作录制器（两种输入源统一走 record_action）。

核心设计：录制核心与输入源解耦，提供统一的 record_action() 入口。
  - pynput 模式（生产用）：全局监听人类操作，回调零阻塞只入队，消费线程调 record_action
  - 程序化模式（自动化验证/agent 编排）：脚本用 backend 执行操作，主动调 record_action

pynput 模式的关键技术点（调研验证通过）：
  1. 回调同步冻结几何：GetAncestor(WindowFromPoint((x,y)), GA_ROOT=2) → GetWindowRect +
     GetWindowText + pid，一次性冻结成 WindowInfo。不能延迟换算（窗口动了/关了会错）
  2. pid 自排除：GetWindowThreadProcessId 拿 pid，对比 os.getpid() + 父进程链，
     命中录制器自己的终端 → 跳过（否则终端里 Ctrl+C 也被录成操作）
  3. 截图用 mss 全屏（airtest 内部就是用 mss），不绑窗口
  4. 截图动态间隔：操作触发重置到 0.5，斐波那契式递增到 15 秒封顶

程序化模式：不启动 pynput/mss，截图用传入的 backend.screenshot()，调用方主动控
制 before/after 时序。

设计要点：
- 状态自洽：只持有自己的状态（steps/queue/listeners/计时器），不依赖外部
- 回调零阻塞：pynput 回调只 queue.put（<1ms），否则 Windows 会静默摘钩
- LLM 克制：录制阶段不调 LLM（LLM 起名/生成 test_case 在下游 namer/generator）

用法（pynput 模式）::

    recorder = GlobalRecorder(output_dir="flows/.tmp_xxx", pynput_mode=True)
    recorder.start()
    # ... 用户手动操作游戏 ...
    flow = recorder.stop()  # 停止监听，返回 RecordedFlow

用法（程序化模式）::

    recorder = GlobalRecorder(output_dir=tmp, backend=backend)
    shot_before = backend.screenshot()
    backend.click_text("开始")
    recorder.record_action("click_text", text="开始",
        x=0.5, y=0.3, screenshot_before=shot_before, screenshot_after=backend.screenshot())
    flow = recorder.stop()
"""

from __future__ import annotations

import datetime
import logging
import os
import queue
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2

from joker_test.flow.types import (
    FlowAction,
    RecordedFlow,
    RecordedStep,
    WindowInfo,
)

if TYPE_CHECKING:
    from joker_test.executor.base import ExecutorBackend

logger = logging.getLogger(__name__)

# 截图动态间隔（斐波那契式递增，操作触发重置到第一个值）
# 0.5, 1, 1.5, 2.5, 4, 6.5, 10.5 → 封顶到 BASE_INTERVAL（15）
_DEFAULT_INTERVALS = (0.5, 1.0, 1.5, 2.5, 4.0, 6.5, 10.5)
_BASE_INTERVAL = 15.0


def _fibonacci_intervals(
    max_interval: float = _BASE_INTERVAL, unit: float = 0.5
) -> Iterator[float]:
    """生成操作后的递增截图间隔序列（斐波那契式，封顶 max_interval）。

    序列 = unit × 斐波那契数：unit×1, unit×2, unit×3, unit×5, unit×8, unit×13...
    默认 unit=0.5：0.5, 1.0, 1.5, 2.5, 4.0, 6.5, 10.5 → 封顶到 max_interval（15）。
    """
    a, b = unit, 2 * unit  # F1=unit, F2=2*unit（标准斐波那契 F1=1,F2=2）
    while True:
        yield min(a, max_interval)
        if a >= max_interval:
            continue  # 已封顶，无限 yield max_interval
        a, b = b, a + b


class GlobalRecorder:
    """操作录制器（统一 record_action 入口 + 可插拔输入源）。

    Args:
        output_dir: 录制产物父目录（实际写进它的一个子目录）
        backend: 程序化模式传入（截图用 backend.screenshot）；pynput 模式传 None
        pynput_mode: True 启动 pynput 全局监听 + mss 全屏截图（人类操作录制）
        screenshot_intervals: pynput 模式的操作后递增截图间隔序列
        base_interval: pynput 模式静止时的兜底截图间隔（秒）

    状态自洽：所有状态（steps/queue/listeners/计时器）都是自身属性。
    """

    def __init__(
        self,
        output_dir: str | Path,
        *,
        backend: ExecutorBackend | None = None,
        pynput_mode: bool = False,
        screenshot_intervals: tuple[float, ...] = _DEFAULT_INTERVALS,
        base_interval: float = _BASE_INTERVAL,
    ) -> None:
        self._backend = backend
        self._pynput_mode = pynput_mode
        self._intervals = screenshot_intervals
        self._base_interval = base_interval

        # 临时子目录（录制中），由调用方/stop 后决定最终名字
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._dir = Path(output_dir) / f".tmp_{ts}"
        self._screenshots_dir = self._dir / "screenshots"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)

        self._steps: list[RecordedStep] = []
        self._start_time = time.monotonic()
        self._running = False

        # pynput 模式专用
        self._event_queue: queue.Queue[tuple] = queue.Queue()
        self._mouse_listener: Any = None
        self._key_listener: Any = None
        self._consumer_thread: threading.Thread | None = None
        self._screenshot_thread: threading.Thread | None = None
        self._key_buffer: list[str] = []  # 键盘聚合缓冲
        self._self_pids: set[int] = set()  # 录制器自己的进程链 pid（自排除用）
        self._shot_counter = 0

    @property
    def flow_dir(self) -> Path:
        """录制产物目录（录制中是 .tmp_，stop 后由调用方 rename）。"""
        return self._dir

    # ===== 统一录制入口（两种输入源都走这里）=====

    def record_action(
        self,
        action: FlowAction,
        *,
        x: float | None = None,
        y: float | None = None,
        screen_x: int = 0,
        screen_y: int = 0,
        window: WindowInfo | None = None,
        key: str | None = None,
        text: str | None = None,
        screenshot_before: str | None = None,
        screenshot_after: str | None = None,
        ocr_before: list[str] | None = None,
        ocr_after: list[str] | None = None,
        note: str = "",
    ) -> None:
        """统一录制入口。pynput 回调 & 程序化调用都走这里。

        Args:
            action: 动作类型（click/click_text/click_image/press_key/type_text）
            x, y: 归一化坐标（程序化模式直接给）
            screen_x, screen_y: 屏幕像素坐标（pynput 模式冻结）
            window: 窗口几何快照（pynput 模式）
            key: press_key 的 keyname
            text: click_text 的目标文字 / type_text 的完整文本
            screenshot_before/after: 操作前后截图路径
            ocr_before/after: 操作前后 OCR 文本列表
            note: 备注
        """
        step = RecordedStep(
            action=action,
            x=x,
            y=y,
            screen_x=screen_x,
            screen_y=screen_y,
            window=window,
            key=key,
            text=text,
            ocr_texts_before=ocr_before or [],
            ocr_texts_after=ocr_after or [],
            screenshot_before=screenshot_before,
            screenshot_after=screenshot_after,
            note=note,
            elapsed_s=round(time.monotonic() - self._start_time, 2),
        )
        self._steps.append(step)
        logger.info("录制 %s: %s", action, note or text or key or f"({x},{y})")

    # ===== 生命周期 =====

    def start(self) -> None:
        """启动录制。pynput 模式启动全局监听 + mss 截图线程；程序化模式是空操作。"""
        if not self._pynput_mode:
            logger.info("程序化录制模式（无 pynput 监听）")
            return

        # 设 DPI awareness（拿物理像素，整个进程受益）
        try:
            import ctypes  # noqa: PLC0415

            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        except (AttributeError, OSError):
            pass  # 非 Windows 或已设过

        # 记录自己的进程链 pid（自排除用）
        self._self_pids = self._collect_self_pids()
        logger.info("自排除 pid 集合: %s", self._self_pids)

        self._running = True
        # 消费线程（从 queue 取事件，做几何冻结/键盘聚合）
        self._consumer_thread = threading.Thread(
            target=self._consume_loop, daemon=True, name="recorder-consumer"
        )
        self._consumer_thread.start()
        # mss 截图线程（动态间隔）
        self._screenshot_thread = threading.Thread(
            target=self._screenshot_loop, daemon=True, name="recorder-screenshot"
        )
        self._screenshot_thread.start()

        # 启动 pynput 监听
        try:
            from pynput import keyboard, mouse  # noqa: PLC0415
        except ImportError as e:
            raise ImportError(
                "pynput 模式需要 pynput。pip install pynput"
            ) from e

        self._mouse_listener = mouse.Listener(on_click=self._on_click)
        self._key_listener = keyboard.Listener(on_press=self._on_key)
        self._mouse_listener.start()
        self._key_listener.start()
        logger.info("pynput 监听已启动（鼠标 + 键盘）")

    def stop(self) -> RecordedFlow:
        """停止录制，返回 RecordedFlow。"""
        self._running = False

        if self._pynput_mode:
            # 停止监听
            if self._mouse_listener:
                self._mouse_listener.stop()
            if self._key_listener:
                self._key_listener.stop()
            # flush 剩余键盘缓冲
            self._flush_key_buffer()
            # 等消费线程退出
            if self._consumer_thread:
                self._event_queue.put(("__stop__",))
                self._consumer_thread.join(timeout=3.0)
            if self._screenshot_thread:
                self._screenshot_thread.join(timeout=3.0)

        flow = RecordedFlow(
            name=datetime.datetime.now().strftime("%Y%m%d_%H%M%S"),  # 临时名，namer 会换
            steps=self._steps,
            recorded_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            screenshots_dir="screenshots",
        )
        logger.info("录制结束：%d 步", len(self._steps))
        return flow

    def save_flow_yaml(self, flow: RecordedFlow) -> Path:
        """把 RecordedFlow 写成 flow.yaml（yaml 格式，人可读可编辑）。"""
        import yaml  # noqa: PLC0415

        path = self._dir / "flow.yaml"
        data = flow.model_dump()
        path.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        return path

    # ===== pynput 模式：回调（零阻塞，只入队）=====

    def _on_click(self, x: float, y: float, button: Any, pressed: bool) -> None:
        """鼠标点击回调。零阻塞：只入队。只记 pressed=True 那次（按下）。"""
        if pressed:
            self._event_queue.put(("click", int(x), int(y)))

    def _on_key(self, key: Any) -> None:
        """键盘按下回调。零阻塞：只入队。"""
        self._event_queue.put(("key", key))

    # ===== pynput 模式：消费线程（几何冻结/键盘聚合/pid排除）=====

    def _consume_loop(self) -> None:
        """消费线程：从 queue 取事件，做几何冻结/键盘聚合/pid 自排除。"""
        while self._running:
            try:
                event = self._event_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if event[0] == "__stop__":
                break
            if event[0] == "click":
                self._handle_click(event[1], event[2])
            elif event[0] == "key":
                self._handle_key(event[1])

    def _handle_click(self, screen_x: int, screen_y: int) -> None:
        """处理点击事件：冻结窗口几何 + pid 排除 + 换算归一化坐标。"""
        win = self._freeze_window_info(screen_x, screen_y)
        if win is None:
            return  # pid 自排除命中（点了录制器自己）

        # 换算归一化坐标（纯算术，用冻结的 rect）
        if win.width > 0 and win.height > 0:
            x = (screen_x - win.rect[0]) / win.width
            y = (screen_y - win.rect[1]) / win.height
        else:
            x, y = None, None

        # flush 键盘缓冲（点击会打断文本输入）
        self._flush_key_buffer()

        self.record_action(
            "click",
            x=x,
            y=y,
            screen_x=screen_x,
            screen_y=screen_y,
            window=win,
            note=f"点击 {win.title}",
        )

    def _handle_key(self, key: Any) -> None:
        """处理键盘事件：可打印字符入缓冲，非可打印 flush+记 press_key。"""
        char = getattr(key, "char", None)
        name = getattr(key, "name", None)

        if char is not None and len(char) == 1 and char.isprintable():
            # 可打印字符 → 入缓冲（聚合）
            self._key_buffer.append(char)
        else:
            # 非可打印（Enter/Escape/方向键等）→ flush 缓冲 + 记 press_key
            self._flush_key_buffer()
            keyname = name or str(key)
            self.record_action("press_key", key=keyname, note=f"按键 {keyname}")

    def _flush_key_buffer(self) -> None:
        """把键盘缓冲拼成 type_text step（有内容才 flush）。"""
        if self._key_buffer:
            text = "".join(self._key_buffer)
            self._key_buffer.clear()
            self.record_action("type_text", text=text, note=f"输入 {text[:20]}")

    def _freeze_window_info(self, screen_x: int, screen_y: int) -> WindowInfo | None:
        """冻结点击位置的窗口几何快照 + pid 自排除。

        返回 None 表示 pid 自排除命中（点了录制器自己），应跳过。
        """
        try:
            import win32gui  # noqa: PLC0415
            import win32process  # noqa: PLC0415
        except ImportError:
            return None

        try:
            # WindowFromPoint 拿点击位置下的窗口，GetAncestor 取顶层（避免子控件 rect）
            hwnd = win32gui.WindowFromPoint((screen_x, screen_y))
            if not hwnd:
                return None
            # GA_ROOT = 2：取顶层窗口
            top_hwnd = win32gui.GetAncestor(hwnd, 2)
            if top_hwnd:
                hwnd = top_hwnd

            # pid 自排除
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in self._self_pids:
                return None

            # 冻结几何
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            title = win32gui.GetWindowText(hwnd)
            return WindowInfo(
                hwnd=hwnd,
                title=title,
                rect=(left, top, right, bottom),
                width=right - left,
                height=bottom - top,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("冻结窗口几何失败: %s", e)
            return None

    def _collect_self_pids(self) -> set[int]:
        """收集录制器自己的进程链 pid（os.getpid + 父进程）。

        用于排除录制器终端窗口（否则终端里 Ctrl+C 也被录成操作）。
        """
        pids = {os.getpid()}
        try:
            pids.add(os.getppid())
            # 尝试往上走一层（父进程的父进程，应对 shell 嵌套）
            import psutil  # noqa: PLC0415

            try:
                parent = psutil.Process(os.getppid())
                if parent.parent():
                    pids.add(parent.parent().pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        except (AttributeError, OSError):
            pass
        return pids

    # ===== pynput 模式：mss 截图线程（动态间隔）=====

    def _screenshot_loop(self) -> None:
        """mss 全屏截图线程。动态间隔：操作触发重置到序列首值，之后斐波那契递增。"""
        interval_iter = _fibonacci_intervals(self._base_interval)
        # 取第一个间隔作为初始（有操作后会重置）
        current_interval = self._base_interval

        while self._running:
            time.sleep(current_interval)
            if not self._running:
                break

            # 截全屏
            shot_path = self._take_mss_screenshot()
            if shot_path is None:
                continue

            # 判断最近是否有操作事件（有则重置间隔到序列首值，否则递增）
            recent = self._had_recent_action()
            if recent:
                interval_iter = _fibonacci_intervals(self._base_interval)
                current_interval = next(interval_iter)
            else:
                next_val = next(interval_iter)
                current_interval = min(next_val, self._base_interval)

    def _take_mss_screenshot(self) -> Path | None:
        """用 mss 截全屏（主屏 monitors[1]，避免多屏拼接坐标混淆）。"""
        try:
            import mss  # noqa: PLC0415
            import numpy as np  # noqa: PLC0415
        except ImportError:
            return None

        try:
            with mss.mss() as sct:
                # monitors[1] = 主屏；monitors[0] = 跨所有屏的虚拟屏
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                shot = sct.grab(monitor)
                # BGRA → BGR（cv2 写盘格式）
                frame = np.array(shot, dtype=np.uint8)[..., :3]

                self._shot_counter += 1
                path = self._screenshots_dir / f"shot_{self._shot_counter:04d}.png"
                cv2.imwrite(str(path), frame)
                return path
        except Exception as e:  # noqa: BLE001
            logger.warning("mss 截图失败: %s", e)
            return None

    def _had_recent_action(self) -> bool:
        """判断最近（1 秒内）是否有操作事件。"""
        if not self._steps:
            # 启动后第一次截图视为"有操作"（加速开始采样）
            return self._shot_counter == 0
        return (time.monotonic() - self._start_time) - self._steps[-1].elapsed_s < 1.0


__all__ = ["GlobalRecorder"]
