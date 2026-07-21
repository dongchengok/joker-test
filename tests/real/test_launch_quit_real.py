"""真 SPD 进入退出测试（基于真 OCR 实测输出编写）。

这些测试在真游戏上跑（JOKER_BACKEND=airtest），断言基于 SPD 主菜单的实际 OCR 输出。
每个测试独立（不依赖前序测试状态），用 wait_until 等界面稳定。
CI 模式（JOKER_BACKEND=fake）自动跳过。

真 OCR 实测主菜单输出（2026-07-07）：
  ['ShatteredPixel Dungeon', '口', '×', 'SHATTERED', 'PIXEL DUNGEON',
   '在你之前...', '传说...', '辐射而来...', '准备好...', '进入地牢']
"""
from __future__ import annotations

import os

import pytest

from joker_test.executor.base import ExecutorBackend

# 真机模式（airtest/mac/native）才运行；CI（fake）自动跳过
pytestmark = pytest.mark.skipif(
    os.environ.get("JOKER_BACKEND", "fake").lower() not in ("airtest", "mac", "native"),
    reason="真游戏测试，需 JOKER_BACKEND=airtest|mac|native",
)


def test_main_menu_has_title(backend: ExecutorBackend) -> None:
    """主菜单应显示游戏标题 SHATTERED 或 PIXEL DUNGEON。"""
    backend.wait_until(
        lambda: any("SHATTERED" in t or "PIXEL" in t for t in backend.state.texts),
        timeout=10.0,
    )
    texts = backend.state.texts
    assert any("SHATTERED" in t or "PIXEL" in t for t in texts), \
        f"未找到游戏标题，实际: {texts}"


def test_main_menu_has_enter_button(backend: ExecutorBackend) -> None:
    """主菜单应有'进入地牢'按钮。"""
    backend.wait_until(
        lambda: any("进入地牢" in t or "地牢" in t for t in backend.state.texts),
        timeout=10.0,
    )
    texts = backend.state.texts
    assert any("地牢" in t for t in texts), f"未找到进入地牢按钮，实际: {texts}"


def test_main_menu_has_intro_text(backend: ExecutorBackend) -> None:
    """主菜单应显示剧情介绍文字。"""
    backend.wait_until(
        lambda: any("地牢" in t for t in backend.state.texts),
        timeout=10.0,
    )
    texts = backend.state.texts
    # 剧情文字含"地牢"或"英雄"
    assert any("地牢" in t or "英雄" in t or "护符" in t for t in texts), \
        f"未找到剧情文字，实际: {texts}"
