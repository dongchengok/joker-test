"""手写冒烟测试（M3b 验证用）。

验证 backend fixture + run_tests + Reporter 全链路。
这个文件也会被 run_tests 收集执行。
"""

from __future__ import annotations


def test_backend_provides_texts(backend) -> None:  # noqa: ANN001
    """backend fixture 应提供 state.texts。"""
    texts = backend.state.texts
    assert isinstance(texts, list)
    assert len(texts) > 0


def test_backend_click_navigates(backend) -> None:  # noqa: ANN001
    """点击按钮应能导航到子界面。"""
    clicked = backend.click_text("背包")
    assert clicked is True
    # 多屏 FakeBackend：点"背包"切到 inventory 屏
    assert "物品" in backend.state.texts


def test_backend_escape_backtracks(backend) -> None:  # noqa: ANN001
    """escape 应能回退（或保持当前界面）。"""
    backend.press_key("escape")
    # 不抛异常即通过


def test_always_pass() -> None:
    """一个必过的测试（验证 passed 计数）。"""
    assert True
