# macOS 兼容（MacBackend + SPD 脚本平台化）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 macOS 上跑真实 SPD 端到端测试：新增 MacBackend（Quartz + CGEvent），6 个直连真游戏的脚本平台化，Windows 行为不变。

**Architecture:** 不动 `ExecutorBackend` 协议。新增 `executor/backends/mac/`（backend/state/_quartz）实现同一协议；`executor/window.py` 跨平台窗口等待；`executor/backends/factory.py` 按 `sys.platform` 分发 AirtestBackend/MacBackend；脚本统一改调这两个入口。Spec: `docs/superpowers/specs/2026-07-20-macos-compat-design.md`。

**Tech Stack:** Python 3.13 venv（仓库根 `.venv`）、pyobjc-framework-Quartz、numpy、opencv-python-headless、rapidocr_onnxruntime、pytest。

## Global Constraints

- 开发用仓库根 `.venv`（`pip install -e ".[dev]"` 已装），所有命令用 `.venv/bin/python` / `.venv/bin/pytest`。
- docstring 中文 + Google 风格（Args/Returns/Raises）；模块/类/公共函数必须有（§11.5）。
- 公共 API 必须类型标注，文件顶部 `from __future__ import annotations`（§11.6）。
- import 分组：`__future__`→标准库→第三方→本项目→同包相对；重依赖（Quartz/cv2/numpy）函数内懒导入（§11.7）。Quartz 例外：`_quartz.py` 是 Quartz 专用封装，模块级 import 但带 ImportError 兜底（仿 airtest/backend.py 风格）。
- 每个 `__init__.py` 用 `__all__` 显式导出，`_` 前缀不导出（§11.4）。
- 私有边界：包外会 import 就不加 `_`（§11.3）。
- 错误处理：禁裸 `except:`；自定义异常 `Error` 后缀（§11.8）。
- 日志：`_LOGGER = logging.getLogger(__name__)`，用户进度用 `print`（§11.9）。
- lint：`ruff check src tests`（line-length=100）；类型：`mypy src`（非 strict）。
- 坐标契约：Backend 对外全部归一化 [0,1]，基准 = `screenshot()` 图像尺寸（G6）。
- JSON 读写强制 `encoding="utf-8"`。
- 测试文件 `tests/test_<被测模块>.py`；CI 默认 FakeBackend，不依赖真游戏。
- **git commit 需用户逐次确认**：本计划每个 Task 末尾的 commit 步骤，执行前先问用户。

---

### Task 1: pyproject `mac` extra + `_quartz.py`（窗口查找/截图/激活）

**Files:**
- Modify: `pyproject.toml`（`[project.optional-dependencies]` 段，找到 `ocr = [` 之前插入 `mac` extra）
- Create: `src/joker_test/executor/backends/mac/__init__.py`
- Create: `src/joker_test/executor/backends/mac/_quartz.py`
- Test: `tests/test_quartz_mac.py`

**Interfaces:**
- Produces（后续 Task 依赖）:
  - `_quartz.find_window(title_substr: str) -> tuple[int, tuple[float, float, float, float], int] | None` — 返回 (windowID, (x, y, w, h) point 坐标， ownerPID)
  - `_quartz.capture_window(window_id: int) -> numpy.ndarray` — BGR ndarray，失败抛 RuntimeError
  - `_quartz.activate_app(pid: int) -> None`

- [ ] **Step 1: pyproject 加 mac extra**

在 `pyproject.toml` 的 `[project.optional-dependencies]` 段（`ocr = [` 之前）插入：

```toml
mac = [
    # macOS 原生 backend（Quartz 窗口枚举/截图 + CGEvent 输入）
    "pyobjc-framework-Quartz>=10.0; sys_platform=='darwin'",
]
```

- [ ] **Step 2: 安装依赖**

```bash
.venv/bin/pip install -e ".[dev,mac,ocr]" opencv-python-headless
```

预期：`pyobjc-framework-Quartz`、`rapidocr_onnxruntime`、`opencv-python-headless` 安装成功。验证：`.venv/bin/python -c "import Quartz, cv2; print('ok')"` 输出 `ok`。

- [ ] **Step 3: 写失败测试 `tests/test_quartz_mac.py`**

测试策略：向 `sys.modules` 注入假 Quartz 模块，再 `importlib.reload` `_quartz`（模块级 `import Quartz` 会拿到假货）。

```python
"""_quartz 单元测试（假 Quartz 注入，不依赖真窗口）。"""
from __future__ import annotations

import importlib
import sys
import types

import numpy as np
import pytest


def _make_fake_quartz(windows: list[dict], image: dict | None = None) -> types.ModuleType:
    """构造假 Quartz 模块。windows 为 CGWindowListCopyWindowInfo 返回的窗口 dict 列表。

    image: 假 CGImage dict {"w": int, "h": int, "bpr": int, "data": bytes}，None 表示截图返回 None。
    """
    mod = types.ModuleType("Quartz")
    mod.kCGWindowListOptionOnScreenOnly = 1
    mod.kCGNullWindowID = 0
    mod.kCGWindowListOptionIncludingWindow = 1
    mod.kCGWindowImageBoundsIgnoreFraming = 1
    mod.kCGHIDEventTap = 0
    mod.kCGEventLeftMouseDown = 1
    mod.kCGEventLeftMouseUp = 2
    mod.kCGEventLeftMouseDragged = 6
    mod.kCGMouseButtonLeft = 0
    mod.CGRectNull = "CGRectNull"
    mod.CGWindowListCopyWindowInfo = lambda opts, rel: windows
    mod.CGWindowListCreateImage = (
        (lambda rect, opts, wid, img_opts: image) if image is not None
        else (lambda rect, opts, wid, img_opts: None)
    )
    mod.CGImageGetWidth = lambda img: img["w"]
    mod.CGImageGetHeight = lambda img: img["h"]
    mod.CGImageGetBytesPerRow = lambda img: img["bpr"]
    mod.CGImageGetDataProvider = lambda img: img
    mod.CGDataProviderCopyData = lambda p: p["data"]
    return mod


def _bgra_image(w: int, h: int) -> dict:
    """造一个 w×h 的 BGRA 假 CGImage（每个像素 B=10,G=20,R=30,A=255）。"""
    px = bytes([10, 20, 30, 255])
    return {"w": w, "h": h, "bpr": w * 4, "data": px * (w * h)}


@pytest.fixture
def quartz_mod(monkeypatch):
    """注入假 Quartz 并 reload _quartz，返回 (模块, 设置函数)。"""
    def _setup(windows: list[dict], image: dict | None = None):
        fake = _make_fake_quartz(windows, image)
        monkeypatch.setitem(sys.modules, "Quartz", fake)
        from joker_test.executor.backends.mac import _quartz
        importlib.reload(_quartz)
        return _quartz
    return _setup


_WIN = {
    "kCGWindowName": "Shattered Pixel Dungeon",
    "kCGWindowLayer": 0,
    "kCGWindowNumber": 42,
    "kCGWindowOwnerPID": 777,
    "kCGWindowBounds": {"X": 100.0, "Y": 50.0, "Width": 800.0, "Height": 600.0},
}


class TestFindWindow:
    def test_found(self, quartz_mod):
        q = quartz_mod([_WIN])
        assert q.find_window("Shattered") == (42, (100.0, 50.0, 800.0, 600.0), 777)

    def test_not_found(self, quartz_mod):
        q = quartz_mod([_WIN])
        assert q.find_window("OtherGame") is None

    def test_skip_nonzero_layer(self, quartz_mod):
        win = dict(_WIN, kCGWindowLayer=1)
        q = quartz_mod([win])
        assert q.find_window("Shattered") is None

    def test_empty_name_skipped(self, quartz_mod):
        win = dict(_WIN, kCGWindowName=None)
        q = quartz_mod([win])
        assert q.find_window("Shattered") is None


class TestCaptureWindow:
    def test_bgra_to_bgr(self, quartz_mod):
        q = quartz_mod([_WIN], image=_bgra_image(4, 3))
        frame = q.capture_window(42)
        assert frame.shape == (3, 4, 3)
        # BGRA(10,20,30,255) → BGR(10,20,30)
        assert frame[0, 0].tolist() == [10, 20, 30]

    def test_nil_image_raises(self, quartz_mod):
        q = quartz_mod([_WIN], image=None)
        with pytest.raises(RuntimeError, match="截图失败"):
            q.capture_window(42)
```

- [ ] **Step 4: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_quartz_mac.py -v
```

预期：collection error，`No module named 'joker_test.executor.backends.mac'`。

- [ ] **Step 5: 实现 `src/joker_test/executor/backends/mac/__init__.py`（占位，Task 4 补导出）**

```python
"""joker_test.executor.backends.mac —— macOS 原生 ExecutorBackend 实现。"""

__all__: list[str] = []
```

- [ ] **Step 6: 实现 `src/joker_test/executor/backends/mac/_quartz.py`**

```python
"""Quartz 封装 —— MacBackend 的窗口查找/截图/输入底层（包内私有）。

macOS 专属。Quartz 模块级 import + ImportError 兜底（仿 airtest/backend.py），
单元测试通过 sys.modules 注入假 Quartz 后 reload 本模块。

关键约束：
- CGWindowList 的 bounds 是 point（逻辑坐标），CGWindowListCreateImage 输出是 pixel
  （Retina 2x），scale 换算在 backend.py 做。
- 未授权"屏幕录制"时，其他 App 的 kCGWindowName 为空串（find_window 找不到）
  且截图内容全黑——调用方负责给出授权提示。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    import Quartz
except ImportError as e:
    raise ImportError(
        "MacBackend 需要 pyobjc-framework-Quartz。请装 mac extras: pip install -e .[mac]"
    ) from e

if TYPE_CHECKING:
    from numpy import ndarray

# (windowID, (x, y, w, h) point 坐标, ownerPID)
WindowInfo = tuple[int, tuple[float, float, float, float], int]


def find_window(title_substr: str) -> WindowInfo | None:
    """按标题子串找第一个 layer=0 的在屏窗口。

    Args:
        title_substr: 窗口标题子串（如 "Shattered"）

    Returns:
        (windowID, (x, y, w, h), ownerPID)；找不到返回 None。
    """
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
    )
    for w in wins:
        name = w.get("kCGWindowName") or ""
        if w.get("kCGWindowLayer", -1) == 0 and title_substr in name:
            b = w["kCGWindowBounds"]
            bounds = (float(b["X"]), float(b["Y"]), float(b["Width"]), float(b["Height"]))
            return int(w["kCGWindowNumber"]), bounds, int(w.get("kCGWindowOwnerPID", 0))
    return None


def capture_window(window_id: int) -> "ndarray":
    """截取指定窗口，返回 BGR ndarray。

    CGWindowListCreateImage 默认像素格式 BGRA（little-endian），取前三通道即 BGR。
    窗口被遮挡也能截（合成器里有完整内容）；未授权屏幕录制时内容全黑。

    Args:
        window_id: find_window 返回的 windowID

    Returns:
        BGR ndarray，形状 (h, w, 3)。

    Raises:
        RuntimeError: CGWindowListCreateImage 返回 None。
    """
    import numpy as np  # noqa: PLC0415

    img = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        window_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming,
    )
    if img is None:
        raise RuntimeError(
            "截图失败（CGWindowListCreateImage 返回 None）。窗口可能已关闭。"
        )
    w = Quartz.CGImageGetWidth(img)
    h = Quartz.CGImageGetHeight(img)
    bpr = Quartz.CGImageGetBytesPerRow(img)
    data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(img))
    buf = np.frombuffer(data, dtype=np.uint8)
    bgra = buf[: h * bpr].reshape(h, bpr)[:, : w * 4].reshape(h, w, 4)
    return bgra[:, :, :3].copy()


def activate_app(pid: int) -> None:
    """把指定 PID 的 App 窗口置前（点击事件发给最上层窗口，必须先置前）。

    Args:
        pid: find_window 返回的 ownerPID。
    """
    from AppKit import (  # noqa: PLC0415
        NSApplicationActivateIgnoringOtherApps,
        NSRunningApplication,
    )

    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is not None:
        app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)


__all__ = ["WindowInfo", "find_window", "capture_window", "activate_app"]
```

- [ ] **Step 7: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_quartz_mac.py -v
```

预期：6 passed。

- [ ] **Step 8: Commit（先问用户确认）**

```bash
git add pyproject.toml src/joker_test/executor/backends/mac/ tests/test_quartz_mac.py
git commit -m "feat(executor): mac backend 底层 _quartz（窗口查找/截图/激活）"
```

---

### Task 2: `executor/window.py` 跨平台窗口等待

**Files:**
- Create: `src/joker_test/executor/window.py`
- Test: `tests/test_window.py`

**Interfaces:**
- Consumes: `_quartz.find_window(title_substr)`（Task 1）
- Produces: `wait_for_window(title_substr: str, timeout: float = 10.0, interval: float = 1.0) -> bool`、`window_exists(title_substr: str) -> bool`（Task 6 的脚本用）

- [ ] **Step 1: 写失败测试 `tests/test_window.py`**

```python
"""executor/window.py 跨平台窗口等待测试。"""
from __future__ import annotations

import sys
import types

import pytest

from joker_test.executor import window as win_mod


@pytest.fixture
def as_darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    return monkeypatch


@pytest.fixture
def as_win32(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    return monkeypatch


def _fake_quartz_mod(found: bool) -> types.ModuleType:
    mod = types.ModuleType("joker_test.executor.backends.mac._quartz")
    mod.find_window = lambda title: (1, (0.0, 0.0, 100.0, 100.0), 1) if found else None
    return mod


def _fake_win32gui(visible_titles: list[str]) -> types.ModuleType:
    mod = types.ModuleType("win32gui")
    mod.IsWindowVisible = lambda h: True
    mod.GetWindowText = lambda h: visible_titles[h]
    def _enum(cb, _):
        for h in range(len(visible_titles)):
            cb(h, None)
    mod.EnumWindows = _enum
    return mod


class TestWindowExists:
    def test_darwin_found(self, as_darwin, monkeypatch):
        monkeypatch.setitem(
            sys.modules, "joker_test.executor.backends.mac._quartz", _fake_quartz_mod(True)
        )
        assert win_mod.window_exists("Shattered") is True

    def test_darwin_not_found(self, as_darwin, monkeypatch):
        monkeypatch.setitem(
            sys.modules, "joker_test.executor.backends.mac._quartz", _fake_quartz_mod(False)
        )
        assert win_mod.window_exists("Shattered") is False

    def test_win32_found(self, as_win32, monkeypatch):
        monkeypatch.setitem(sys.modules, "win32gui", _fake_win32gui(["Shattered Pixel Dungeon"]))
        assert win_mod.window_exists("Shattered") is True

    def test_win32_not_found(self, as_win32, monkeypatch):
        monkeypatch.setitem(sys.modules, "win32gui", _fake_win32gui(["其他窗口"]))
        assert win_mod.window_exists("Shattered") is False

    def test_unsupported_platform(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        with pytest.raises(RuntimeError, match="不支持的平台"):
            win_mod.window_exists("Shattered")


class TestWaitForWindow:
    def test_immediate(self, as_darwin, monkeypatch):
        monkeypatch.setitem(
            sys.modules, "joker_test.executor.backends.mac._quartz", _fake_quartz_mod(True)
        )
        assert win_mod.wait_for_window("Shattered", timeout=1.0, interval=0.05) is True

    def test_timeout(self, as_darwin, monkeypatch):
        monkeypatch.setitem(
            sys.modules, "joker_test.executor.backends.mac._quartz", _fake_quartz_mod(False)
        )
        assert win_mod.wait_for_window("Shattered", timeout=0.2, interval=0.05) is False
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_window.py -v
```

预期：`ModuleNotFoundError: No module named 'joker_test.executor.window'`。

- [ ] **Step 3: 实现 `src/joker_test/executor/window.py`**

```python
"""跨平台窗口等待 —— 脚本启动前等游戏窗口就绪（替代脚本里的 win32gui 轮询）。

按 sys.platform 分派：darwin → Quartz 枚举；win32 → win32gui。
两边均函数内懒导入，互不污染依赖。
"""
from __future__ import annotations

import sys
import time


def window_exists(title_substr: str) -> bool:
    """检查是否存在标题含 title_substr 的可见窗口。

    Args:
        title_substr: 窗口标题子串（如 "Shattered"）

    Returns:
        存在返回 True。

    Raises:
        RuntimeError: 不支持的平台（仅 win32/darwin）。
    """
    if sys.platform == "darwin":
        from joker_test.executor.backends.mac import _quartz  # noqa: PLC0415

        return _quartz.find_window(title_substr) is not None
    if sys.platform == "win32":
        import win32gui  # noqa: PLC0415

        found: list[int] = []

        def _cb(h: int, _: object) -> bool:
            if win32gui.IsWindowVisible(h) and title_substr in win32gui.GetWindowText(h):
                found.append(h)
            return True

        win32gui.EnumWindows(_cb, None)
        return bool(found)
    raise RuntimeError(f"不支持的平台: {sys.platform}（仅支持 Windows/macOS）")


def wait_for_window(title_substr: str, timeout: float = 10.0, interval: float = 1.0) -> bool:
    """轮询等窗口出现。

    Args:
        title_substr: 窗口标题子串
        timeout: 超时秒数
        interval: 轮询间隔秒数

    Returns:
        超时前出现返回 True，否则 False。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if window_exists(title_substr):
            return True
        time.sleep(interval)
    return window_exists(title_substr)


__all__ = ["wait_for_window", "window_exists"]
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

```bash
.venv/bin/python -m pytest tests/test_window.py -v
.venv/bin/python -m pytest -q
```

预期：7 passed；全量 236+ passed, 3 skipped。

- [ ] **Step 5: Commit（先问用户确认）**

```bash
git add src/joker_test/executor/window.py tests/test_window.py
git commit -m "feat(executor): 跨平台窗口等待 wait_for_window"
```

---

### Task 3: `_quartz.py` 输入事件（click/key/swipe/long_press + keycode map）

**Files:**
- Modify: `src/joker_test/executor/backends/mac/_quartz.py`
- Test: `tests/test_quartz_input.py`

**Interfaces:**
- Produces（Task 4 的 MacBackend 依赖）:
  - `_quartz.post_click(x: float, y: float) -> None`（全局 point 坐标）
  - `_quartz.post_key(keycode: int) -> None`
  - `_quartz.post_swipe(x1, y1, x2, y2, duration: float) -> None`
  - `_quartz.post_long_press(x, y, duration: float) -> None`
  - `_quartz.KEYCODES: dict[str, int]`（键名 → mac 虚拟键码）

- [ ] **Step 1: 写失败测试 `tests/test_quartz_input.py`**

假 Quartz 记录 CGEventCreateMouseEvent/CGEventCreateKeyboardEvent/CGEventPost 的调用参数。

```python
"""_quartz 输入事件测试（假 Quartz 记录事件构造参数）。"""
from __future__ import annotations

import importlib
import sys
import types

import pytest


class _Recorder:
    """记录事件构造与投递。"""

    def __init__(self) -> None:
        self.mouse_events: list[tuple] = []   # (src, event_type, point, button)
        self.key_events: list[tuple] = []     # (src, keycode, down)
        self.posted: list[tuple] = []         # (tap, event)


@pytest.fixture
def rec(monkeypatch):
    r = _Recorder()
    mod = types.ModuleType("Quartz")
    mod.kCGHIDEventTap = 0
    mod.kCGEventLeftMouseDown = 1
    mod.kCGEventLeftMouseUp = 2
    mod.kCGEventLeftMouseDragged = 6
    mod.kCGMouseButtonLeft = 0

    def _mk_mouse(src, etype, point, button):
        ev = ("mouse", etype, tuple(point), button)
        r.mouse_events.append((src, etype, tuple(point), button))
        return ev

    def _mk_key(src, keycode, down):
        ev = ("key", keycode, down)
        r.key_events.append((src, keycode, down))
        return ev

    mod.CGEventCreateMouseEvent = _mk_mouse
    mod.CGEventCreateKeyboardEvent = _mk_key
    mod.CGEventPost = lambda tap, ev: r.posted.append((tap, ev))
    monkeypatch.setitem(sys.modules, "Quartz", mod)
    from joker_test.executor.backends.mac import _quartz
    importlib.reload(_quartz)
    return r, _quartz


class TestPostClick:
    def test_down_up_at_point(self, rec):
        r, q = rec
        q.post_click(640.0, 360.0)
        types_seq = [e[1] for e in r.mouse_events]
        assert types_seq == [1, 2]  # down, up
        assert r.mouse_events[0][2] == (640.0, 360.0)
        assert len(r.posted) == 2


class TestPostKey:
    def test_down_up(self, rec):
        r, q = rec
        q.post_key(53)  # escape
        assert r.key_events == [(None, 53, True), (None, 53, False)]
        assert len(r.posted) == 2


class TestPostSwipe:
    def test_down_drag_up(self, rec):
        r, q = rec
        q.post_swipe(0.0, 0.0, 100.0, 0.0, duration=0.01)
        types_seq = [e[1] for e in r.mouse_events]
        assert types_seq[0] == 1 and types_seq[-1] == 2  # down ... up
        assert 6 in types_seq  # 中间有 dragged
        assert r.mouse_events[-1][2] == (100.0, 0.0)


class TestPostLongPress:
    def test_down_up_same_point(self, rec):
        r, q = rec
        q.post_long_press(50.0, 60.0, duration=0.01)
        types_seq = [e[1] for e in r.mouse_events]
        assert types_seq == [1, 2]
        assert r.mouse_events[0][2] == r.mouse_events[1][2] == (50.0, 60.0)


class TestKeycodes:
    def test_common_keys(self, rec):
        _, q = rec
        for name in ("escape", "enter", "space", "tab", "backspace",
                     "up", "down", "left", "right", "i", "a"):
            assert name in q.KEYCODES, f"缺键位映射: {name}"
        assert q.KEYCODES["escape"] == 53
        assert q.KEYCODES["enter"] == 36
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_quartz_input.py -v
```

预期：`AttributeError: module ... has no attribute 'post_click'`（reload 后仍无新函数）。

- [ ] **Step 3: 在 `_quartz.py` 追加输入事件实现**

在文件末尾 `__all__` 之前插入（并把新名字加进 `__all__`）：

```python
# ===== 输入事件（CGEvent，全局 point 坐标）=====

# mac 虚拟键码（kVK_*，来自 HIToolbox/Events.h）
KEYCODES: dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37,
    "j": 38, "k": 40, "n": 45, "m": 46,
    "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22,
    "7": 26, "8": 28, "9": 25, "0": 29,
    "enter": 36, "tab": 48, "space": 49, "backspace": 51, "escape": 53,
    "left": 123, "right": 124, "down": 125, "up": 126,
}


def post_click(x: float, y: float) -> None:
    """在全局 point 坐标 (x, y) 单击左键。"""
    point = (x, y)
    for etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        ev = Quartz.CGEventCreateMouseEvent(None, etype, point, Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def post_key(keycode: int) -> None:
    """按一下指定键码（down + up）。"""
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def post_swipe(x1: float, y1: float, x2: float, y2: float, duration: float = 0.5) -> None:
    """从 (x1, y1) 拖到 (x2, y2)（全局 point 坐标），duration 秒内完成。"""
    import time  # noqa: PLC0415

    steps = max(int(duration / 0.01), 2)
    down = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventLeftMouseDown, (x1, y1), Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    for i in range(1, steps + 1):
        xi = x1 + (x2 - x1) * i / steps
        yi = y1 + (y2 - y1) * i / steps
        ev = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseDragged, (xi, yi), Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(duration / steps)
    up = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventLeftMouseUp, (x2, y2), Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def post_long_press(x: float, y: float, duration: float = 2.0) -> None:
    """在 (x, y) 长按左键 duration 秒。"""
    import time  # noqa: PLC0415

    down = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventLeftMouseDown, (x, y), Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    time.sleep(duration)
    up = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventLeftMouseUp, (x, y), Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
```

同时把 `__all__` 改为：

```python
__all__ = [
    "WindowInfo", "find_window", "capture_window", "activate_app",
    "KEYCODES", "post_click", "post_key", "post_swipe", "post_long_press",
]
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m pytest tests/test_quartz_input.py tests/test_quartz_mac.py -v
```

预期：全部 passed（test_quartz_mac 的 reload 不受影响）。

- [ ] **Step 5: Commit（先问用户确认）**

```bash
git add src/joker_test/executor/backends/mac/_quartz.py tests/test_quartz_input.py
git commit -m "feat(executor): _quartz 输入事件（CGEvent 鼠标/键盘）"
```

---

### Task 4: MacBackend + state + 包导出

**Files:**
- Create: `src/joker_test/executor/backends/mac/state.py`
- Create: `src/joker_test/executor/backends/mac/backend.py`
- Modify: `src/joker_test/executor/backends/mac/__init__.py`
- Modify: `src/joker_test/executor/backends/__init__.py`
- Test: `tests/test_backends_mac.py`

**Interfaces:**
- Consumes: `_quartz.find_window/capture_window/activate_app/post_click/post_key/post_swipe/post_long_press/KEYCODES`（Task 1/3）；`analyze_screenshot`（`executor/coords.py`）
- Produces: `MacBackend(window_title: str, ocr_enabled: bool = True, ocr: OCRProvider | None = None)`，满足 `ExecutorBackend` 协议（Task 5 factory / conftest / Task 6 脚本用）。`type_text` 抛 NotImplementedError。

- [ ] **Step 1: 写失败测试 `tests/test_backends_mac.py`**

直接 monkeypatch `_quartz` 模块函数（不碰 Quartz 本体），帧用合成 ndarray。

```python
"""MacBackend 单元测试（monkeypatch _quartz，不依赖真窗口）。"""
from __future__ import annotations

import pytest

pytest.importorskip("Quartz", reason="需要 pyobjc（pip install -e .[mac]）")

import numpy as np

from joker_test.executor.backends.mac import _quartz
from joker_test.executor.backends.mac.backend import MacBackend

# 窗口 bounds (point)：x=100, y=50, w=400, h=300；截图 800×600 pixel → scale=2.0
_BOUNDS = (100.0, 50.0, 400.0, 300.0)
_FRAME = np.random.default_rng(0).integers(20, 230, (600, 800, 3), dtype=np.uint8)


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setattr(_quartz, "find_window", lambda t: (42, _BOUNDS, 777))
    monkeypatch.setattr(_quartz, "capture_window", lambda wid: _FRAME.copy())
    monkeypatch.setattr(_quartz, "activate_app", lambda pid: None)
    clicks: list[tuple] = []
    keys: list[int] = []
    monkeypatch.setattr(_quartz, "post_click", lambda x, y: clicks.append((x, y)))
    monkeypatch.setattr(_quartz, "post_key", lambda kc: keys.append(kc))
    b = MacBackend(window_title="Shattered")
    b._test_clicks = clicks  # 暴露断言入口
    b._test_keys = keys
    return b


class TestConnect:
    def test_connect_ok(self, backend):
        backend.connect()
        assert backend._scale == pytest.approx(2.0)

    def test_window_not_found(self, monkeypatch):
        monkeypatch.setattr(_quartz, "find_window", lambda t: None)
        with pytest.raises(RuntimeError, match="找不到窗口"):
            MacBackend(window_title="Shattered").connect()


class TestClickCoords:
    def test_normalized_to_global_point(self, backend):
        backend.connect()
        backend.click(0.5, 0.5)
        # 截图像素中心 (400, 300) → point (200, 150) → 全局 (100+200, 50+150)
        assert backend._test_clicks == [(300.0, 200.0)]

    def test_out_of_range(self, backend):
        backend.connect()
        with pytest.raises(ValueError, match="\\[0,1\\]"):
            backend.click(1.5, 0.0)


class TestPressKey:
    def test_named_key(self, backend):
        backend.connect()
        backend.press_key("escape")
        assert backend._test_keys == [53]

    def test_unknown_key(self, backend):
        backend.connect()
        with pytest.raises(ValueError, match="未知按键"):
            backend.press_key("f19")


class TestProtocol:
    def test_satisfies_protocol(self, backend):
        from joker_test.executor.base import ExecutorBackend
        assert isinstance(backend, ExecutorBackend)

    def test_type_text_not_implemented(self, backend):
        with pytest.raises(NotImplementedError):
            backend.type_text("hello")


class TestClickImage:
    def test_match_and_click(self, backend, tmp_path):
        import cv2
        # 模板 = 帧的一块区域，必能匹配上
        tpl = _FRAME[100:140, 200:260].copy()
        tpl_path = tmp_path / "tpl.png"
        cv2.imwrite(str(tpl_path), tpl)
        backend.connect()
        assert backend.click_image(str(tpl_path), threshold=0.95) is True
        # 中心点：((200+30)/800, (100+20)/600) → point → 全局
        assert len(backend._test_clicks) == 1

    def test_no_match(self, backend, tmp_path):
        import cv2
        tpl = np.zeros((40, 40, 3), dtype=np.uint8)  # 纯黑，帧里没有
        tpl[20:30, 20:30] = 255
        tpl_path = tmp_path / "tpl.png"
        cv2.imwrite(str(tpl_path), tpl)
        backend.connect()
        assert backend.click_image(str(tpl_path), threshold=0.999) is False
        assert backend._test_clicks == []
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_backends_mac.py -v
```

预期：`No module named 'joker_test.executor.backends.mac.backend'`。

- [ ] **Step 3: 实现 `src/joker_test/executor/backends/mac/state.py`**

与 airtest 的 state.py 同构（OCR 按需解析 + 帧缓存）：

```python
"""_MacState —— MacBackend 专有的 LazyState 实现（带 `_`，只给 mac backend 用）。

按需 OCR（OCRProvider），缓存到当前帧。Backend 每次 screenshot 后通过 invalidate() 清缓存。
逻辑与 airtest/state.py 的 _AirtestState 一致（两份是有意的：backend 各自自洽，§私有边界）。
"""
from __future__ import annotations

from typing import Any

from joker_test.executor.base import BBox
from joker_test.ocr.base import OCRProvider


class _MacState:
    """MacBackend 的状态代理。按需 OCR，缓存到当前帧。"""

    def __init__(self, get_frame: Any, ocr: OCRProvider | None = None) -> None:
        self._get_frame = get_frame  # 回调，返回当前帧 ndarray
        self._ocr = ocr  # None 时懒加载默认 RapidOCR
        self._ocr_results: list[dict[str, Any]] | None = None

    def _ensure_ocr_provider(self) -> OCRProvider:
        """懒加载默认 OCR（RapidOCR）。首次访问才加载模型。"""
        if self._ocr is None:
            from joker_test.ocr.providers.rapidocr import RapidOCRProvider  # noqa: PLC0415
            self._ocr = RapidOCRProvider()
        return self._ocr

    def _ensure_ocr(self) -> None:
        """首次访问触发 OCR，之后缓存。"""
        if self._ocr_results is not None:
            return
        ocr = self._ensure_ocr_provider()
        frame = self._get_frame()
        if frame is None or frame.size == 0:
            self._ocr_results = []
            return
        results = ocr.readtext(frame)
        self._ocr_results = [
            {"text": r.text, "bbox": BBox(r.bbox[0], r.bbox[1], r.bbox[2], r.bbox[3])}
            for r in results
        ]

    @property
    def texts(self) -> list[str]:
        self._ensure_ocr()
        return [r["text"] for r in self._ocr_results]  # type: ignore[union-attr]

    def find_text(self, text: str) -> BBox | None:
        """查找指定文本的位置。精确匹配优先，降级子串匹配。"""
        self._ensure_ocr()
        text_stripped = text.strip()
        for r in self._ocr_results:  # type: ignore[union-attr]
            if r["text"].strip() == text_stripped:
                return r["bbox"]  # type: ignore[index]
        for r in self._ocr_results:  # type: ignore[union-attr]
            if text in r["text"]:
                return r["bbox"]  # type: ignore[index]
        return None

    def invalidate(self) -> None:
        """新帧到达时清缓存（MacBackend.screenshot 后调用）。"""
        self._ocr_results = None
```

- [ ] **Step 4: 实现 `src/joker_test/executor/backends/mac/backend.py`**

```python
"""MacBackend —— macOS 原生 ExecutorBackend 实现（Quartz 截图 + CGEvent 输入）。

见 docs/superpowers/specs/2026-07-20-macos-compat-design.md。

关键约束：
- 坐标契约与协议一致：对外全部归一化 [0,1]，基准 = screenshot 图像尺寸（pixel）。
  Quartz bounds 是 point、截图是 pixel（Retina 2x），_scale 实测换算在内部消化。
- 前置权限：屏幕录制（截图）+ 辅助功能（CGEvent 输入），connect 时检测并提示。
- 点击发给最上层窗口：connect 时 activate_app 把游戏置前；游戏中途被别的窗口
  盖住时点击会落空（与 airtest 的 G7 限制同类），保持游戏窗口在前。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from joker_test.executor.backends.mac import _quartz
from joker_test.executor.backends.mac.state import _MacState
from joker_test.executor.base import LazyState, NDArray
from joker_test.executor.coords import analyze_screenshot

if TYPE_CHECKING:
    from joker_test.ocr.base import OCRProvider

_LOGGER = logging.getLogger(__name__)


class MacBackend:
    """macOS 原生 Backend。满足 ExecutorBackend 协议（结构性子类型）。

    Args:
        window_title: 目标窗口标题（子串匹配）
        ocr_enabled: 是否启用 OCR（click_text/state.texts 用）。默认 True
        ocr: OCRProvider（None 时 _MacState 懒加载默认 RapidOCR）
    """

    def __init__(
        self,
        window_title: str,
        ocr_enabled: bool = True,
        ocr: "OCRProvider | None" = None,
    ) -> None:
        self._window_title = window_title
        self._ocr_enabled = ocr_enabled
        self._ocr = ocr
        self._window_id: int | None = None
        self._bounds: tuple[float, float, float, float] | None = None  # point (x,y,w,h)
        self._scale: float = 1.0  # 截图像素宽 / bounds point 宽（Retina=2.0）
        self._current_frame: NDArray | None = None  # type: ignore[valid-type]
        self._state: _MacState | None = None

    # ===== 生命周期 =====

    def connect(self) -> None:
        """找窗口 + 置前 + 首帧截图（实测 scale）+ 健康检测。

        Raises:
            RuntimeError: 窗口找不到（游戏未启动，或未授权屏幕录制导致标题为空）。
        """
        found = _quartz.find_window(self._window_title)
        if found is None:
            raise RuntimeError(
                f"找不到窗口 '{self._window_title}'。请确认游戏已启动；"
                "若游戏已在运行仍找不到，检查 系统设置→隐私与安全性→屏幕录制 "
                "是否已授权当前终端（授权后需重启终端）。"
            )
        self._window_id, self._bounds, pid = found
        _quartz.activate_app(pid)
        time.sleep(0.5)  # 等窗口置前动画
        frame = self.screenshot()
        health = analyze_screenshot(frame)
        if "失败" in health or "异常" in health:
            _LOGGER.warning(
                "窗口截图异常: %s。可能未授权屏幕录制。请保持游戏窗口可见。", health
            )
        _LOGGER.info(
            "已连接窗口 '%s' (id=%d, bounds=%s, scale=%.2f)",
            self._window_title, self._window_id, self._bounds, self._scale,
        )

    def close(self) -> None:
        self._window_id = None
        self._bounds = None
        self._current_frame = None

    # ===== 感知 =====

    def screenshot(self) -> NDArray:  # type: ignore[valid-type]
        """截取游戏窗口，返回 BGR ndarray。每次调用刷新 bounds 与 scale。"""
        if self._window_id is None:
            raise RuntimeError("未 connect，先调用 connect()")
        found = _quartz.find_window(self._window_title)  # 窗口可能移动/缩放
        if found is not None:
            self._window_id, self._bounds, _ = found
        frame = _quartz.capture_window(self._window_id)
        if self._bounds is not None and self._bounds[2] > 0:
            self._scale = frame.shape[1] / self._bounds[2]
        self._current_frame = frame
        if self._state is not None:
            self._state.invalidate()
        return frame

    # ===== 操作（归一化坐标，基准=screenshot 图像尺寸）=====

    def _to_screen_point(self, x: float, y: float) -> tuple[float, float]:
        """归一化坐标 → 全局 point 坐标（CGEvent 用）。"""
        if self._current_frame is None or self._bounds is None:
            raise RuntimeError("无当前帧/窗口信息，先 screenshot()")
        h, w = self._current_frame.shape[:2]
        bx, by = self._bounds[0], self._bounds[1]
        return bx + x * w / self._scale, by + y * h / self._scale

    def click(self, x: float, y: float) -> None:
        """归一化坐标点击。"""
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            raise ValueError(f"click 坐标必须在 [0,1]，收到 ({x}, {y})")
        sx, sy = self._to_screen_point(x, y)
        _quartz.post_click(sx, sy)

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
        """cv2 模板匹配定位并点击（mac 无 airtest Template 可复用，简单实现）。

        Args:
            template: 图像文件路径。ndarray 不支持（与 AirtestBackend 对齐）。
            threshold: TM_CCOEFF_NORMED 匹配阈值 [0,1]，默认 0.8

        Returns:
            匹配成功并点击返回 True，找不到返回 False。
        """
        if not isinstance(template, str):
            raise ValueError("click_image 暂只支持文件路径模板。")
        import cv2  # noqa: PLC0415

        frame = self.screenshot()
        tpl = cv2.imread(template)
        if tpl is None:
            raise ValueError(f"模板图像读不到: {template}")
        res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val < threshold:
            return False
        th, tw = tpl.shape[:2]
        fh, fw = frame.shape[:2]
        self.click((max_loc[0] + tw / 2) / fw, (max_loc[1] + th / 2) / fh)
        return True

    def press_key(self, key: str) -> None:
        """按键。key 如 'escape'/'enter'/'i'（映射见 _quartz.KEYCODES）。

        Raises:
            ValueError: 未映射的键名。
        """
        keycode = _quartz.KEYCODES.get(key.lower())
        if keycode is None:
            raise ValueError(f"未知按键: '{key}'（已映射: {sorted(_quartz.KEYCODES)}）")
        _quartz.post_key(keycode)

    def type_text(self, text: str) -> None:
        raise NotImplementedError("MacBackend 初版不支持 type_text")

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration: float = 0.5) -> None:
        """滑动（归一化坐标 [0,1]）。"""
        sx1, sy1 = self._to_screen_point(x1, y1)
        sx2, sy2 = self._to_screen_point(x2, y2)
        _quartz.post_swipe(sx1, sy1, sx2, sy2, duration=duration)

    def long_press(self, x: float, y: float, duration: float = 2.0) -> None:
        """长按（归一化坐标 [0,1]）。"""
        sx, sy = self._to_screen_point(x, y)
        _quartz.post_long_press(sx, sy, duration=duration)

    # ===== 同步 =====

    def wait_until(self, predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
        """轮询 predicate（每次先 screenshot 刷帧），满足返回 True；超时 False。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.screenshot()
            if predicate():
                return True
            time.sleep(0.5)
        self.screenshot()
        return predicate()

    # ===== 状态 =====

    @property
    def state(self) -> LazyState:
        if self._state is None:
            self._state = _MacState(get_frame=self._get_current_frame, ocr=self._ocr)
        return self._state

    def _get_current_frame(self) -> NDArray:  # type: ignore[valid-type]
        """_MacState 的帧回调：懒截图（若无当前帧则现截一张）。"""
        if self._current_frame is None:
            self.screenshot()
        return self._current_frame  # type: ignore[return-value]


__all__ = ["MacBackend"]
```

- [ ] **Step 5: 更新 `src/joker_test/executor/backends/mac/__init__.py`**

```python
"""joker_test.executor.backends.mac —— macOS 原生 ExecutorBackend 实现。"""

from joker_test.executor.backends.mac.backend import MacBackend

__all__ = ["MacBackend"]
```

- [ ] **Step 6: 更新 `src/joker_test/executor/backends/__init__.py`，mac backend 延迟导出**

在文件末尾（AirtestBackend 的 try/except 块之后）追加：

```python
# MacBackend 依赖 pyobjc（[mac] extras，仅 macOS），延迟导入（导入失败不影响其他用户）
try:
    from joker_test.executor.backends.mac.backend import MacBackend  # noqa: F401
except ImportError:
    pass
else:
    __all__.append("MacBackend")
```

- [ ] **Step 7: 跑测试确认通过 + 全量回归 + lint**

```bash
.venv/bin/python -m pytest tests/test_backends_mac.py -v
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/mypy src
```

预期：9 passed；全量 passed；ruff/mypy 无新增错误。

- [ ] **Step 8: Commit（先问用户确认）**

```bash
git add src/joker_test/executor/backends/ tests/test_backends_mac.py
git commit -m "feat(executor): MacBackend（Quartz 截图 + CGEvent 输入，ExecutorBackend 协议）"
```

---

### Task 5: 平台分发工厂 + conftest `mac`/`native` + tests/real 放宽

**Files:**
- Create: `src/joker_test/executor/backends/factory.py`
- Modify: `tests/conftest.py:68-101`（backend fixture）
- Modify: `tests/real/test_launch_quit_real.py:22-26`（skipif）
- Test: `tests/test_factory.py`

**Interfaces:**
- Consumes: `MacBackend`（Task 4）
- Produces: `create_native_backend(window_title: str, ocr: OCRProvider | None = None) -> ExecutorBackend`（Task 6 脚本用）；conftest 支持 `JOKER_BACKEND=mac|native`

- [ ] **Step 1: 写失败测试 `tests/test_factory.py`**

```python
"""create_native_backend 平台分发测试。"""
from __future__ import annotations

import sys

import pytest

from joker_test.executor.backends.factory import create_native_backend


def test_darwin_returns_mac_backend(monkeypatch):
    pytest.importorskip("Quartz", reason="需要 pyobjc（pip install -e .[mac]）")
    monkeypatch.setattr(sys, "platform", "darwin")
    from joker_test.executor.backends.mac import MacBackend
    b = create_native_backend("Shattered")
    assert isinstance(b, MacBackend)


def test_win32_returns_airtest_backend(monkeypatch):
    pytest.importorskip("airtest", reason="需要 airtest（pip install -e .[airtest]）")
    monkeypatch.setattr(sys, "platform", "win32")
    from joker_test.executor.backends.airtest import AirtestBackend
    b = create_native_backend("Shattered")
    assert isinstance(b, AirtestBackend)


def test_unsupported_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="不支持的平台"):
        create_native_backend("Shattered")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest tests/test_factory.py -v
```

预期：`No module named 'joker_test.executor.backends.factory'`。

- [ ] **Step 3: 实现 `src/joker_test/executor/backends/factory.py`**

```python
"""平台分发工厂 —— 按当前平台创建原生桌面 Backend。

脚本不直接实例化 AirtestBackend/MacBackend，统一走 create_native_backend，
由工厂按 sys.platform 分发（win32 → AirtestBackend，darwin → MacBackend）。
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from joker_test.executor.base import ExecutorBackend
    from joker_test.ocr.base import OCRProvider


def create_native_backend(
    window_title: str, ocr: "OCRProvider | None" = None
) -> "ExecutorBackend":
    """按 sys.platform 创建原生桌面 backend。

    Args:
        window_title: 目标窗口标题（子串匹配）
        ocr: OCRProvider（None 时各 backend 懒加载默认 RapidOCR）

    Returns:
        win32 → AirtestBackend；darwin → MacBackend。

    Raises:
        RuntimeError: 不支持的平台。
    """
    if sys.platform == "win32":
        from joker_test.executor.backends.airtest import AirtestBackend  # noqa: PLC0415

        return AirtestBackend(window_title=window_title, ocr=ocr)
    if sys.platform == "darwin":
        from joker_test.executor.backends.mac import MacBackend  # noqa: PLC0415

        return MacBackend(window_title=window_title, ocr=ocr)
    raise RuntimeError(f"不支持的平台: {sys.platform}（仅支持 Windows/macOS 桌面）")


__all__ = ["create_native_backend"]
```

- [ ] **Step 4: 修改 `tests/conftest.py` 的 backend fixture**

把 `if backend_type == "airtest":` 分支（conftest.py:84-95）替换为下面三个分支（`else` 的 fake 分支不动）：

```python
    if backend_type == "airtest":
        try:
            from joker_test.executor.backends.airtest import AirtestBackend
            from joker_test.ocr.providers.rapidocr import RapidOCRProvider
        except ImportError:
            pytest.skip("airtest/rapidocr 未装（pip install -e .[airtest,ocr]）")

        backend = AirtestBackend(window_title=window_title, ocr=RapidOCRProvider())
        backend.connect()
        set_active_backend(backend)
        yield backend
        backend.close()
    elif backend_type == "mac":
        try:
            from joker_test.executor.backends.mac import MacBackend
            from joker_test.ocr.providers.rapidocr import RapidOCRProvider
        except ImportError:
            pytest.skip("pyobjc/rapidocr 未装（pip install -e .[mac,ocr]）")

        backend = MacBackend(window_title=window_title, ocr=RapidOCRProvider())
        backend.connect()
        set_active_backend(backend)
        yield backend
        backend.close()
    elif backend_type == "native":
        # 按当前平台自动选 AirtestBackend(Win)/MacBackend(Mac)
        from joker_test.executor.backends.factory import create_native_backend
        from joker_test.ocr.providers.rapidocr import RapidOCRProvider

        backend = create_native_backend(window_title=window_title, ocr=RapidOCRProvider())
        backend.connect()
        set_active_backend(backend)
        yield backend
        backend.close()
```

同时更新 fixture docstring（conftest.py:75-76）为：

```python
    JOKER_BACKEND=fake（默认）→ FakeBackend（CI，session scope）
    JOKER_BACKEND=airtest → AirtestBackend + RapidOCR 连真游戏（Windows 真执行）
    JOKER_BACKEND=mac → MacBackend + RapidOCR 连真游戏（macOS 真执行）
    JOKER_BACKEND=native → 按当前平台自动选 airtest/mac
```

- [ ] **Step 5: 修改 `tests/real/test_launch_quit_real.py` 的 skipif**

把 skipif 条件（test_launch_quit_real.py:22-26）改为：

```python
# 真机模式（airtest/mac/native）才运行；CI（fake）自动跳过
pytestmark = pytest.mark.skipif(
    os.environ.get("JOKER_BACKEND", "fake") not in ("airtest", "mac", "native"),
    reason="真游戏测试，需 JOKER_BACKEND=airtest|mac|native",
)
```

- [ ] **Step 6: 跑测试 + 全量回归**

```bash
.venv/bin/python -m pytest tests/test_factory.py -v
.venv/bin/python -m pytest -q
```

预期：factory 3 passed（本机 darwin 分支真走 MacBackend 构造）；全量 passed、real 仍 3 skipped（默认 fake）。

- [ ] **Step 7: Commit（先问用户确认）**

```bash
git add src/joker_test/executor/backends/factory.py tests/conftest.py tests/real/test_launch_quit_real.py tests/test_factory.py
git commit -m "feat(executor): create_native_backend 平台分发 + conftest JOKER_BACKEND=mac|native"
```

---

### Task 6: 6 个真游戏脚本平台化

**Files:**
- Modify: `scripts/e2e_spd_real.py:26-44, 58-62`
- Modify: `scripts/e2e_spd_full_real.py:21, 28-42, 46-50`
- Modify: `scripts/e2e_formal.py:20, 26-48`
- Modify: `scripts/e2e_traced.py:57-72, 135`
- Modify: `scripts/verify_spd_record_e2e.py:35-56, 89`
- Modify: `scripts/e2e_spd_explore_conversation.py:52-56`

**Interfaces:**
- Consumes: `wait_for_window`（Task 2）、`create_native_backend`（Task 5）

统一替换规则（每个脚本三处模式）：
1. `import win32gui` 轮询块 → `wait_for_window("Shattered", ...)`（`from joker_test.executor.window import wait_for_window`）
2. `AirtestBackend(window_title=..., ocr=...)` → `create_native_backend(...)`（`from joker_test.executor.backends.factory import create_native_backend`）
3. 硬编码 `os.environ["JOKER_BACKEND"] = "airtest"` → `os.environ.setdefault("JOKER_BACKEND", "native")`

- [ ] **Step 1: `scripts/e2e_spd_real.py`**

替换 26-44 行（`print("等待 SPD 窗口...")` 到 `sys.exit(1)` 的 win32gui 块）为：

```python
# 等游戏窗口就绪
print("等待 SPD 窗口...")
from joker_test.executor.window import wait_for_window  # noqa: E402

if not wait_for_window("Shattered", timeout=10.0):
    print("✗ SPD 未启动（Mac: java -jar ShatteredPD.jar；Win: .test-targets/SPD/*.exe）")
    sys.exit(1)
print("✓ SPD 窗口已就绪")
```

替换 58-62 行的 backend 构造：

```python
from joker_test.executor.backends.factory import create_native_backend  # noqa: E402
from joker_test.explorer import UIExplorer  # noqa: E402
from joker_test.ocr.providers.rapidocr import RapidOCRProvider  # noqa: E402

backend = create_native_backend(window_title="Shattered", ocr=RapidOCRProvider())
backend.connect()
```

（原脚本只构造未显式 connect，AirtestBackend 首次 screenshot 会惰连；create_native_backend 返回的 MacBackend 需要显式 connect。保留原 `backend = ...` 行号附近结构，接上 `backend.connect()`。）

- [ ] **Step 2: `scripts/e2e_spd_full_real.py`**

- 21 行：`os.environ["JOKER_BACKEND"] = "airtest"` → `os.environ.setdefault("JOKER_BACKEND", "native")`
- 28-42 行 win32gui 块 → 同 Step 1 的 wait_for_window 替换
- 46-50 行：`from joker_test.executor.backends.airtest import AirtestBackend` + `backend = AirtestBackend(...)` → `from joker_test.executor.backends.factory import create_native_backend` + `backend = create_native_backend(window_title="Shattered", ocr=RapidOCRProvider())` + `backend.connect()`
- 80 行 `run_tests(test_paths, backend_name="airtest", ...)` → `backend_name="native"`

- [ ] **Step 3: `scripts/e2e_formal.py`**

- 20 行：`os.environ["JOKER_BACKEND"] = "airtest"` → `os.environ.setdefault("JOKER_BACKEND", "native")`
- 26-36 行 win32gui 块 → wait_for_window 替换
- 41-48 行：`from joker_test.executor.backends.airtest import AirtestBackend` → `from joker_test.executor.backends.factory import create_native_backend`；`backend = AirtestBackend(window_title="Shattered", ocr=ocr)` → `backend = create_native_backend(window_title="Shattered", ocr=ocr)` + `backend.connect()`（原有 connect 行保留则去重）

- [ ] **Step 4: `scripts/e2e_traced.py`**

把 `wait_spd_window()`（57-72 行）的函数体替换为薄包装（保留函数名，调用点不动）：

```python
def wait_spd_window(timeout: float = 30.0) -> bool:
    """等 SPD 窗口就绪。"""
    from joker_test.executor.window import wait_for_window  # noqa: PLC0415

    return wait_for_window(WINDOW_TITLE, timeout=timeout)
```

135 行 `explore_backend = AirtestBackend(...)` → `explore_backend = create_native_backend(window_title=WINDOW_TITLE, ocr=RapidOCRProvider())`（import 同步替换）。

该脚本 203-236 行的"阶段 2.5 重置游戏状态"块也硬编码了 taskkill + win32gui（同 verify_spd_record_e2e 的 reset_spd 模式），替换为平台分支：

```python
with tracer.stage("reset_game"):
    import subprocess  # noqa: PLC0415
    import sys as _sys  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    from joker_test.executor.window import wait_for_window  # noqa: PLC0415

    try:
        if _sys.platform == "darwin":
            subprocess.run(["pkill", "-f", "ShatteredPD"], capture_output=True)
            _time.sleep(2)
            jar = str(REPO / ".test-targets" / "SPD-mac" / "ShatteredPD.jar")
            subprocess.Popen(["java", "-jar", jar], cwd=os.path.dirname(jar))
        else:
            subprocess.run(
                ["taskkill", "/F", "/IM", "Shattered Pixel Dungeon.exe"],
                capture_output=True,
            )
            _time.sleep(2)
            exe = str(REPO / ".test-targets" / "SPD" / "Shattered Pixel Dungeon.exe")
            subprocess.Popen([exe], cwd=os.path.dirname(exe))
        wait_for_window(WINDOW_TITLE, timeout=20.0)
        _time.sleep(3)  # 额外等 LibGDX 初始化
        tracer.log_event("game_reset", {"method": "restart"})
        print("✓ SPD 已重启回主菜单")
    except Exception as e:
        tracer.log_error(f"重置失败: {e}")
        print(f"⚠ 重置失败: {e}")
```

（jar 实际路径同样在 Task 7 解压后确认，不一致就同步改。）

- [ ] **Step 5: `scripts/verify_spd_record_e2e.py`**

`reset_spd()`（35-56 行）加平台分支：

```python
def reset_spd() -> None:
    """重启 SPD 回主菜单（确保从初始状态开始）。"""
    if sys.platform == "darwin":
        subprocess.run(["pkill", "-f", "ShatteredPD"], capture_output=True)
        time.sleep(2)
        jar = str(REPO / ".test-targets" / "SPD-mac" / "ShatteredPD.jar")
        subprocess.Popen(["java", "-jar", jar], cwd=os.path.dirname(jar))
    else:
        import win32gui  # noqa: PLC0415, F401

        subprocess.run(
            ["taskkill", "/F", "/IM", "Shattered Pixel Dungeon.exe"], capture_output=True
        )
        time.sleep(2)
        exe = str(REPO / ".test-targets" / "SPD" / "Shattered Pixel Dungeon.exe")
        subprocess.Popen([exe], cwd=os.path.dirname(exe))
    from joker_test.executor.window import wait_for_window  # noqa: PLC0415

    wait_for_window(WINDOW_TITLE, timeout=20.0)
    time.sleep(5)  # 等 LibGDX 标题画面过渡到主菜单
```

（注意：jar 实际路径在 Task 7 解压后确认，若不是 `.test-targets/SPD-mac/ShatteredPD.jar` 就同步改这里。）
89 行 `backend = AirtestBackend(...)` → `create_native_backend(window_title=WINDOW_TITLE, ocr=RapidOCRProvider())`（import 同步替换）。

- [ ] **Step 6: `scripts/e2e_spd_explore_conversation.py`**

52-56 行：`from joker_test.executor.backends.airtest import AirtestBackend` → `from joker_test.executor.backends.factory import create_native_backend`；`backend = AirtestBackend(window_title=window_title, ocr=ocr)` → `backend = create_native_backend(window_title=window_title, ocr=ocr)`（原有 `backend.connect()` 保留）。

- [ ] **Step 7: 验证：语法检查 + 全量回归**

```bash
.venv/bin/python -m py_compile scripts/e2e_spd_real.py scripts/e2e_spd_full_real.py scripts/e2e_formal.py scripts/e2e_traced.py scripts/verify_spd_record_e2e.py scripts/e2e_spd_explore_conversation.py
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
```

预期：无语法错误；全量 passed；ruff 无新增错误。

- [ ] **Step 8: Commit（先问用户确认）**

```bash
git add scripts/
git commit -m "refactor(scripts): 6 个真游戏脚本平台化（wait_for_window + create_native_backend）"
```

---

### Task 7: 游戏环境准备 + 真机端到端验证（手动/需权限）

**Files:**
- 无代码改动（环境准备 + 验证）；jar 路径若与 Task 6 Step 5 假设不同，改 `scripts/verify_spd_record_e2e.py`

- [ ] **Step 1: 装 Java（arm64 Temurin）**

```bash
brew install --cask temurin
java -version
```

预期：openjdk 21+（arm64）。若 libGDX 启动报 natives 错误，降级：装 x64 JRE 走 Rosetta。

- [ ] **Step 2: 下载并解压 SPD macOS 版**

```bash
mkdir -p .test-targets/SPD-mac
curl -L -o /tmp/spd-mac.zip https://github.com/00-Evan/shattered-pixel-dungeon/releases/download/v3.3.8/ShatteredPD-v3.3.8-macOS.zip
unzip -o /tmp/spd-mac.zip -d .test-targets/SPD-mac/
find .test-targets/SPD-mac -name "*.jar"
```

确认 jar 实际路径；若不是 `.test-targets/SPD-mac/ShatteredPD.jar`，改 `scripts/verify_spd_record_e2e.py` 的 jar 路径。

- [ ] **Step 3: 启动游戏**

```bash
java -jar .test-targets/SPD-mac/<实际jar名>.jar &
```

预期：SPD 窗口出现，标题 "Shattered Pixel Dungeon"。

- [ ] **Step 4: 授权终端"屏幕录制"和"辅助功能"（需用户手动操作）**

系统设置 → 隐私与安全性 → 屏幕录制 / 辅助功能，勾选运行本测试的终端 App（Terminal/iTerm 等），授权后**重启终端**。这一步无法脚本化。

- [ ] **Step 5: 验证窗口发现 + 截图**

```bash
.venv/bin/python -c "
from joker_test.executor.backends.mac import MacBackend
b = MacBackend(window_title='Shattered')
b.connect()
f = b.screenshot()
print('截图:', f.shape, 'scale:', b._scale)
import cv2; cv2.imwrite('/tmp/spd_mac_frame.png', f)
"
```

预期：截图尺寸非空、scale ≈ 2.0（Retina）或 1.0；`/tmp/spd_mac_frame.png` 内容是游戏画面（肉眼确认，可用 Read 工具看图）。

- [ ] **Step 6: 真机跑 tests/real**

```bash
JOKER_BACKEND=mac .venv/bin/python -m pytest tests/real/ -v
```

预期：3 个用例 passed（主菜单标题/进入地牢按钮等 OCR 断言）。

- [ ] **Step 7: 真机端到端（Kimi k3 + ConversationStrategy）**

```bash
.venv/bin/python scripts/e2e_spd_explore_conversation.py
```

预期：LLMExplorer 跑完探索并生成报告（reports/ 下），trace 时间线可见 Kimi k3 的决策轮次。如实记录结果（OCR 质量/决策质量导致的探索不完整属真实边界，不算失败）。

- [ ] **Step 8: Commit（若有 jar 路径修正，先问用户确认）**

```bash
git add scripts/verify_spd_record_e2e.py
git commit -m "fix(scripts): reset_spd jar 路径按实际解压结果修正"
```

---

### Task 8: 文档更新（AGENTS.md）

**Files:**
- Modify: `AGENTS.md`（仓库结构、footgun、常用命令）

- [ ] **Step 1: 更新 AGENTS.md 三处**

1. 仓库结构的 `executor/` 行改为：

```
  executor/                    # Backend 抽象（Protocol + 全局注册 set/get_active_backend + Fake/Airtest/Mac + coords + window + factory）
```

2. footgun 段追加一条（编号接现有 8）：

```markdown
9. **macOS 权限（MacBackend 专用）**：Mac 上跑真机需给终端授权"屏幕录制"（截图）+"辅助功能"（CGEvent 输入），授权后重启终端。未授权时 find_window 找不到游戏（标题为空串）或截图全黑。被测游戏：`brew install --cask temurin` + 官方 macOS.zip 解压到 `.test-targets/SPD-mac/`，`java -jar` 启动。
```

3. 常用命令段追加：

```bash
# macOS 真机测试（需先启动 SPD + 授权屏幕录制/辅助功能）
JOKER_BACKEND=mac pytest tests/real/          # 真机用例
python scripts/e2e_spd_explore_conversation.py  # LLM 探索端到端（自动走 MacBackend）
```

- [ ] **Step 2: README.md 补一句 macOS 真机前置**

在 README.md 安装/快速开始段（`source .venv/bin/activate` 附近，README.md:64）后追加一段：

```markdown
macOS 真机测试（可选）：`pip install -e ".[mac,ocr]"` + `brew install --cask temurin`，
下载官方 SPD macOS 版解压到 `.test-targets/SPD-mac/`，`java -jar` 启动；
终端需授权"屏幕录制"+"辅助功能"。然后 `JOKER_BACKEND=mac pytest tests/real/`。
```

- [ ] **Step 3: Commit（先问用户确认）**

```bash
git add AGENTS.md README.md
git commit -m "docs: AGENTS.md/README 补 MacBackend/macOS 权限/mac 真机命令"
```
