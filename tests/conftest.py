"""tests 根级 conftest。

提供 backend fixture：
- JOKER_BACKEND=fake（默认）：FakeBackend 多屏模拟（CI，不依赖真游戏）
- JOKER_BACKEND=airtest：AirtestBackend + RapidOCR 连真游戏（真执行）
  需先启动游戏（如 SPD），窗口标题用 JOKER_WINDOW 环境变量（默认 "Shattered"）
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from joker_test.executor.backends.fake import FakeBackend, ScreenCfg
from joker_test.executor.base import BBox, ExecutorBackend

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def examples_dir() -> Path:
    return REPO_ROOT / "examples"


@pytest.fixture
def targets_file(examples_dir: Path) -> Path:
    return examples_dir / "targets.json"


@pytest.fixture
def game_meta_file(examples_dir: Path) -> Path:
    return examples_dir / "game_metadata.json"


def _make_fake_backend() -> FakeBackend:
    """多屏 FakeBackend：主菜单 ↔ 背包（CI 友好）。"""
    return FakeBackend(
        screens={
            "main": ScreenCfg(
                texts_map={
                    "背包": BBox(0.5, 0.5, 0.2, 0.1),
                    "设置": BBox(0.7, 0.1, 0.1, 0.05),
                },
                bg_pixel=(10, 20, 30),
            ),
            "inventory": ScreenCfg(
                texts_map={
                    "关闭": BBox(0.9, 0.9, 0.1, 0.05),
                    "物品": BBox(0.5, 0.5, 0.2, 0.1),
                },
                bg_pixel=(40, 60, 80),
            ),
        },
        transitions={
            ("main", "背包"): "inventory",
            ("inventory", "关闭"): "main",
            ("inventory", "key@escape"): "main",
            ("main", "key@escape"): "main",
        },
        initial_screen="main",
    )


@pytest.fixture(scope="session")
def backend() -> Iterator[ExecutorBackend]:
    """提供测试用 Backend。

    返回类型标注为 ExecutorBackend 协议 —— IDE/mypy 据此提示协议全部方法
    （click/click_text/click_image/wait_until/state 等），测试代码有完整补全。

    JOKER_BACKEND=fake（默认）→ FakeBackend（CI，session scope）
    JOKER_BACKEND=airtest → AirtestBackend + RapidOCR 连真游戏（真执行）

    airtest 模式需先启动游戏，窗口标题用 JOKER_WINDOW（默认 "Shattered"）。
    FakeBackend 和 AirtestBackend 都满足 ExecutorBackend 协议（结构化子类型）。
    """
    backend_type = os.environ.get("JOKER_BACKEND", "fake").lower()
    window_title = os.environ.get("JOKER_WINDOW", "Shattered")

    if backend_type == "airtest":
        try:
            from joker_test.executor.backends.airtest import AirtestBackend
            from joker_test.ocr.providers.rapidocr import RapidOCRProvider
        except ImportError:
            pytest.skip("airtest/rapidocr 未装（pip install -e .[airtest,ocr]）")

        backend = AirtestBackend(window_title=window_title, ocr=RapidOCRProvider())
        backend.connect()
        yield backend
        backend.close()
    else:
        fb = _make_fake_backend()
        fb.connect()
        yield fb
        fb.close()
