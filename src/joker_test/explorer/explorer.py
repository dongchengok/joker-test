"""UIExplorer —— 界面探索器核心（DFS 遍历游戏界面，产出 UIMap）。

M2 的主体（roadmap：对应用户阶段1前半）。自动遍历界面，产出结构化界面地图 JSON。
是编排架构里"探查类命令"的核心（D10）——所有编排的第一步都是理解被测系统长什么样。

算法：DFS + 回退（栈式深度优先）。
- 每个新界面：检测元素 → 对每个 button 元素尝试点击 → 等切屏 → 捕获新界面 → 去重（指纹）→ 递归
- 回退：press_key("escape") + 短 wait（或点已知的"返回"元素）
- 防失控：max_depth（防无限深入）+ max_screens（防探索失控）

设计要点：
- 状态自洽：UIExplorer 持有自己的 _screens/_visited，不依赖外部状态
- 复用 M1 Backend：感知用 screenshot/state，操作用 click_text/press_key，同步用 wait_until
- 不用 sleep：用 wait_until + pixel_diff_ratio 做正式同步（FakeBackend 轮询 0.01s，测试快）
- G7 防御：每次 _capture_screen 用 analyze_screenshot 检测截图健康度
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from joker_test.executor.coords import analyze_screenshot
from joker_test.explorer.detection import (
    compute_fingerprint,
    detect_elements,
    has_screen_changed,
)
from joker_test.explorer.types import Exit, Screen, UIMap

if TYPE_CHECKING:
    from numpy import ndarray

    from joker_test.executor.base import ExecutorBackend

logger = logging.getLogger(__name__)

# 切屏等待超时（秒）。Fake 很快，真实游戏动画可能要 1-2s
_SCREEN_CHANGE_TIMEOUT = 3.0
# 回退后等动画稳定的时间
_BACKTRACK_WAIT = 0.3


class UIExplorer:
    """界面探索器。DFS 遍历游戏界面，产出 UIMap。

    Args:
        backend: 已配置好的 ExecutorBackend（如 FakeBackend 或 AirtestBackend）
        max_depth: 最大探索深度（防无限深入，默认 5）
        max_screens: 最多记录多少个界面（防探索失控，默认 20）
        screenshot_dir: 截图保存目录（None 不保存，测试用 None 加速）

    用法::
        explorer = UIExplorer(backend=fb, max_depth=3)
        uimap = explorer.explore()
        print(uimap.model_dump_json())
    """

    def __init__(
        self,
        backend: ExecutorBackend,
        max_depth: int = 5,
        max_screens: int = 20,
        screenshot_dir: str | Path | None = None,
        screen_change_timeout: float = _SCREEN_CHANGE_TIMEOUT,
    ) -> None:
        self._backend = backend
        self._max_depth = max_depth
        self._max_screens = max_screens
        self._screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        self._screen_change_timeout = screen_change_timeout

        # 探索状态（状态自洽）
        self._screens: dict[str, Screen] = {}  # screen_id → Screen
        self._fingerprint_to_id: dict[str, str] = {}  # 指纹 → screen_id（去重）
        self._screen_counter = 0

    def explore(self) -> UIMap:
        """执行 DFS 探索，返回完整界面地图。"""
        self._backend.connect()
        try:
            # 捕获根界面
            root = self._capture_screen(depth=0, entry=None, screen_id="root")
            self._screens[root.id] = root
            self._fingerprint_to_id[root.fingerprint] = root.id

            logger.info("探索起始：根界面 '%s'，%d 个元素", root.id, len(root.elements))

            # DFS 探索
            self._dfs(root, depth=0)

            return UIMap(
                screens=list(self._screens.values()),
                root_screen_id=root.id,
                explored_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                backend_info={"type": type(self._backend).__name__},
            )
        finally:
            self._backend.close()

    def _dfs(self, current: Screen, depth: int) -> None:
        """从 current 界面出发，深度优先探索。"""
        if depth >= self._max_depth:
            logger.debug("达到 max_depth=%d，停止深入", self._max_depth)
            return
        if len(self._screens) >= self._max_screens:
            logger.debug("达到 max_screens=%d，停止探索", self._max_screens)
            return

        # 遍历当前界面的 button 元素，逐个尝试点击
        for element in list(current.elements):  # copy（exits 可能被修改）
            if element.type != "button" or element.text is None:
                continue

            before = self._backend.screenshot()
            clicked = self._backend.click_text(element.text)
            if not clicked:
                continue

            # 等切屏（正式同步，不用 sleep）
            switched, ratio = self._wait_for_screen_change(before)
            if not switched:
                logger.debug("点击 '%s' 无切屏（ratio=%.4f），跳过", element.text, ratio)
                continue

            # 捕获新界面
            target_id = self._peek_new_screen(depth=depth + 1, from_screen=current.id,
                                              via_text=element.text, ratio=ratio)
            current.exits.append(
                Exit(
                    from_screen=current.id,
                    element_text=element.text,
                    action="click_text",
                    to_screen=target_id,
                    evidence={"pixel_diff_ratio": round(ratio, 4)},
                )
            )

            # 回退到当前界面，继续探索下一个元素
            self._backtrack()

    def _wait_for_screen_change(self, before: ndarray) -> tuple[bool, float]:
        """用 wait_until + pixel_diff_ratio 等待切屏发生。

        Returns:
            (是否切屏, 最终变化比例)
        """
        ratio_holder = {"v": 0.0}

        def changed() -> bool:
            after = self._backend.screenshot()
            try:
                ok, ratio_holder["v"] = has_screen_changed(before, after)
                return ok
            except ValueError:
                # 尺寸不一致（理论上不应发生），保守认为未切屏
                return False

        ok = self._backend.wait_until(changed, timeout=self._screen_change_timeout)
        return ok, ratio_holder["v"]

    def _peek_new_screen(
        self,
        depth: int,
        from_screen: str,
        via_text: str,
        ratio: float,
    ) -> str:
        """捕获点击后的新界面，返回它的 screen_id（去重或新建）。

        如果指纹已见过 → 返回已有 screen_id（不重复探索）。
        如果是新界面 → 记录到 _screens，并递归探索它。
        """
        entry = {
            "from": from_screen,
            "via": {"action": "click_text", "target": via_text, "ratio": round(ratio, 4)},
        }
        new_screen = self._capture_screen(depth=depth, entry=entry)

        # 去重：指纹是否见过？
        existing_id = self._fingerprint_to_id.get(new_screen.fingerprint)
        if existing_id is not None:
            logger.info("界面指纹已见过（=%s），不重复探索", existing_id)
            return existing_id

        # 新界面，登记并递归探索
        self._screens[new_screen.id] = new_screen
        self._fingerprint_to_id[new_screen.fingerprint] = new_screen.id
        logger.info("发现新界面 '%s'，%d 个元素（深度 %d）",
                    new_screen.id, len(new_screen.elements), depth)
        self._dfs(new_screen, depth=depth)
        return new_screen.id

    def _backtrack(self) -> None:
        """回退到探索前的界面（press_key escape + 短 wait）。

        FakeBackend 多屏模式：press_key 的 transition 由配置决定（如 key@escape → root）。
        AirtestBackend：escape 是多数游戏的"返回"键。
        """
        self._backend.press_key("escape")
        # 短暂等待界面稳定（不用 wait_until，因为回退后的状态不一定要严格验证）
        self._backend.wait_until(lambda: True, timeout=_BACKTRACK_WAIT)

    def _capture_screen(
        self,
        depth: int,
        entry: Mapping[str, object] | None,
        screen_id: str | None = None,
    ) -> Screen:
        """捕获当前界面为一个 Screen 对象。

        流程：screenshot → analyze 健康检测 → detect_elements → fingerprint → 保存截图（可选）
        """
        frame = self._backend.screenshot()
        health = analyze_screenshot(frame)
        if "失败" in health:
            raise RuntimeError(f"截图失败（G7）: {health}。可能窗口被遮挡或已最小化。")

        elements = detect_elements(self._backend)
        fingerprint = compute_fingerprint(elements)

        # 分配 screen_id
        if screen_id is None:
            self._screen_counter += 1
            screen_id = f"screen_{self._screen_counter}"

        # 可选保存截图（测试通常不保存加速）
        screenshot_ref: str | None = None
        if self._screenshot_dir is not None:
            self._screenshot_dir.mkdir(parents=True, exist_ok=True)
            import cv2  # noqa: PLC0415
            path = self._screenshot_dir / f"{screen_id}.png"
            cv2.imwrite(str(path), frame)
            screenshot_ref = str(path)

        return Screen(
            id=screen_id,
            elements=elements,
            entry=dict(entry) if entry else None,
            fingerprint=fingerprint,
            screenshot_ref=screenshot_ref,
        )


__all__ = ["UIExplorer"]
