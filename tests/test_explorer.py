"""界面探索器测试（M2 核心，多屏 FakeBackend 驱动）。

测试 DFS 探索循环：发现屏幕、记录 exits、去重、回退、max_depth、StateMap 序列化。
这是 M2 的完成标志（roadmap: "FakeBackend 预设几张界面验证探索覆盖"）。
"""

from __future__ import annotations

import json

import pytest

from joker_test.executor.backends.fake import FakeBackend, ScreenCfg
from joker_test.executor.base import BBox
from joker_test.explorer import StateMap, UIExplorer
from joker_test.explorer.detection import compute_fingerprint
from joker_test.explorer.types import UIElement

# ============== 多屏 FakeBackend fixture ==============

@pytest.fixture
def fake_3screen() -> FakeBackend:
    """构造 3 界面 FakeBackend：
    root ──"设置"──► settings ──"返回"──► root
    root ──"新游戏"──► new_game ──"返回"──► root
    每个屏用差异大的 bg_pixel（每通道差 > 25，让 pixel_diff_ratio 能检测到切屏）。
    """
    return FakeBackend(
        width=80,
        height=80,
        screens={
            "root": ScreenCfg(
                texts_map={"设置": BBox(0.7, 0.1, 0.1, 0.05),
                           "新游戏": BBox(0.4, 0.5, 0.2, 0.1)},
                bg_pixel=(10, 20, 30),
            ),
            "settings": ScreenCfg(
                texts_map={"返回": BBox(0.1, 0.9, 0.1, 0.05),
                           "音量": BBox(0.5, 0.5, 0.2, 0.1)},
                bg_pixel=(60, 80, 100),
            ),
            "new_game": ScreenCfg(
                texts_map={"继续": BBox(0.4, 0.5, 0.2, 0.1),
                           "返回": BBox(0.1, 0.9, 0.1, 0.05)},
                bg_pixel=(100, 40, 60),
            ),
        },
        transitions={
            ("root", "设置"): "settings",
            ("settings", "返回"): "root",
            ("root", "新游戏"): "new_game",
            ("new_game", "返回"): "root",
        },
        initial_screen="root",
    )


# ============== 基础探索 ==============

def test_explore_returns_state_map(fake_3screen: FakeBackend) -> None:
    """探索器应返回非空 StateMap。"""
    explorer = UIExplorer(fake_3screen, max_depth=3, screen_change_timeout=0.5)
    state_map = explorer.explore()
    assert isinstance(state_map, StateMap)
    assert len(state_map.screens) >= 1
    assert state_map.root_screen_id == "root"


def test_explore_discovers_all_3_screens(fake_3screen: FakeBackend) -> None:
    """DFS 应发现所有 3 个屏幕（root + settings + new_game）。"""
    explorer = UIExplorer(fake_3screen, max_depth=3, screen_change_timeout=0.5)
    state_map = explorer.explore()
    screen_ids = {s.id for s in state_map.screens}
    # root + 2 个子界面
    assert len(state_map.screens) == 3
    assert "root" in screen_ids


def test_explore_records_exits_with_evidence(fake_3screen: FakeBackend) -> None:
    """root 的 exits 应包含到 settings/new_game 的边，带 pixel_diff_ratio 证据。"""
    explorer = UIExplorer(fake_3screen, max_depth=3, screen_change_timeout=0.5)
    state_map = explorer.explore()
    root = next(s for s in state_map.screens if s.id == "root")
    # 应有 2 个出边（到 settings 和 new_game）
    assert len(root.exits) == 2
    # 每个 exit 有切屏证据
    for exit_edge in root.exits:
        assert "pixel_diff_ratio" in exit_edge.evidence
        assert exit_edge.evidence["pixel_diff_ratio"] > 0.005
        assert exit_edge.action == "click_text"


def test_explore_exit_to_screen_is_known(fake_3screen: FakeBackend) -> None:
    """exit 的 to_screen 应指向已发现的界面 id。"""
    explorer = UIExplorer(fake_3screen, max_depth=3, screen_change_timeout=0.5)
    state_map = explorer.explore()
    all_ids = {s.id for s in state_map.screens}
    for screen in state_map.screens:
        for exit_edge in screen.exits:
            assert exit_edge.to_screen in all_ids, (
                f"exit 指向未知界面 {exit_edge.to_screen}")


# ============== 去重 ==============

def test_explore_dedup_identical_screens() -> None:
    """两个指纹相同的界面应被去重（只记一个）。

    构造：root 和 second 屏的文本元素相同 → 指纹相同 → 只记一个。
    """
    fb = FakeBackend(
        screens={
            "root": ScreenCfg(
                texts_map={"相同文本": BBox(0.5, 0.5, 0.2, 0.1)},
                bg_pixel=(10, 20, 30),
            ),
            "second": ScreenCfg(
                texts_map={"相同文本": BBox(0.5, 0.5, 0.2, 0.1)},  # 相同文本 → 相同指纹
                bg_pixel=(80, 100, 120),  # 差异 > 25 让 pixel_diff 检测到切屏
            ),
        },
        transitions={("root", "相同文本"): "second", ("second", "key@escape"): "root"},
        initial_screen="root",
    )
    explorer = UIExplorer(fb, max_depth=3, screen_change_timeout=0.5)
    state_map = explorer.explore()
    # 只记 1 个界面（去重）
    assert len(state_map.screens) == 1


# ============== max_depth 限制 ==============

def test_explore_respects_max_depth() -> None:
    """max_depth=1 时只探索根界面（不深入）。"""
    # 用 4 层链式界面验证深度控制
    fb = FakeBackend(
        screens={
            "s0": ScreenCfg(texts_map={"next": BBox(0.5, 0.5, 0.1, 0.1)}, bg_pixel=(10, 10, 10)),
            "s1": ScreenCfg(texts_map={"next": BBox(0.5, 0.5, 0.1, 0.1)}, bg_pixel=(60, 60, 60)),
            "s2": ScreenCfg(texts_map={"next": BBox(0.5, 0.5, 0.1, 0.1)}, bg_pixel=(110, 110, 110)),
        },
        transitions={
            ("s0", "next"): "s1", ("s1", "key@escape"): "s0",
            ("s1", "next"): "s2", ("s2", "key@escape"): "s1",
        },
        initial_screen="s0",
    )
    explorer = UIExplorer(fb, max_depth=1, screen_change_timeout=0.5)
    state_map = explorer.explore()
    # max_depth=1 → 深度 0 是根界面（id="root"），深度 1 的界面会被捕获但不再深入
    screen_ids = {s.id for s in state_map.screens}
    assert "root" in screen_ids
    # max_depth=1 时，深度 1 的 s1 会被发现（点击 next 后捕获），但 s2 不应被发现（深度 2 超限）
    # 注意：s0/s1/s2 是 FakeBackend 的内部 screen_id，explorer 用 root/screen_N 命名
    # 这里验证"至少发现 root"，且总界面数受限（不会发现 s2 那层）
    assert len(state_map.screens) <= 2  # root + 最多 1 个深度 1 的界面


# ============== 回退正确性 ==============

def test_explore_backtracks_correctly(fake_3screen: FakeBackend) -> None:
    """探索完子界面后应回退到 root 继续探索兄弟界面。

    验证：root 的两个 exit 都被记录（说明回退后继续点了第二个按钮）。
    若回退失败，只会记一个 exit（探索器卡在子界面）。
    """
    explorer = UIExplorer(fake_3screen, max_depth=3, screen_change_timeout=0.5)
    state_map = explorer.explore()
    root = next(s for s in state_map.screens if s.id == "root")
    # 关键：回退成功才能发现两个子界面的 exit
    exit_texts = {e.element_text for e in root.exits}
    assert exit_texts == {"设置", "新游戏"}


def test_explore_presses_escape_for_backtrack(fake_3screen: FakeBackend) -> None:
    """回退时应调用 press_key('escape')。"""
    explorer = UIExplorer(fake_3screen, max_depth=3, screen_change_timeout=0.5)
    explorer.explore()
    # 应该有 escape 按键记录（每个子界面探索完后回退一次）
    assert "escape" in fake_3screen.key_history


# ============== StateMap 序列化 ==============

def test_state_map_serializes_to_json(fake_3screen: FakeBackend) -> None:
    """StateMap 应能序列化为合法 JSON（编排层会消费这个 JSON）。"""
    explorer = UIExplorer(fake_3screen, max_depth=3, screen_change_timeout=0.5)
    state_map = explorer.explore()
    json_str = state_map.model_dump_json()
    # 能解析回 dict
    data = json.loads(json_str)
    assert "screens" in data
    assert "root_screen_id" in data
    assert len(data["screens"]) == 3


# ============== 指纹工具 ==============

def test_compute_fingerprint_stable() -> None:
    """相同元素集合（不同顺序）应产生相同指纹。"""
    elems_a = [
        UIElement(type="button", text="B", bbox=(0, 0, 0.1, 0.1)),
        UIElement(type="button", text="A", bbox=(0.5, 0.5, 0.1, 0.1)),
    ]
    elems_b = [
        UIElement(type="button", text="A", bbox=(0.5, 0.5, 0.1, 0.1)),
        UIElement(type="button", text="B", bbox=(0, 0, 0.1, 0.1)),
    ]
    assert compute_fingerprint(elems_a) == compute_fingerprint(elems_b)


def test_compute_fingerprint_different_elements() -> None:
    """不同文本应产生不同指纹。"""
    elems_a = [UIElement(type="button", text="A", bbox=(0, 0, 0.1, 0.1))]
    elems_b = [UIElement(type="button", text="B", bbox=(0, 0, 0.1, 0.1))]
    assert compute_fingerprint(elems_a) != compute_fingerprint(elems_b)


def test_compute_fingerprint_empty() -> None:
    """空元素列表应返回 'empty'。"""
    assert compute_fingerprint([]) == "empty"


# ============== 单屏 FakeBackend 仍能探索（M1 兼容）==============

def test_explore_single_screen_fake() -> None:
    """单屏 FakeBackend（M1 风格）也能探索，产出 1 个界面的 StateMap。"""
    fb = FakeBackend(texts_map={"开始": BBox(0.5, 0.5, 0.2, 0.1)})
    explorer = UIExplorer(fb, max_depth=2, screen_change_timeout=0.5)
    state_map = explorer.explore()
    assert len(state_map.screens) == 1
    assert state_map.screens[0].id == "root"
    # 点击"开始"无切屏（同屏），不产生 exit
    assert len(state_map.screens[0].exits) == 0
