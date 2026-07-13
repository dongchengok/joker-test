"""ExploreStage 测试：命中检查 + 三模式分发。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from joker_test.executor.backends.fake import FakeBackend, ScreenCfg
from joker_test.llm.providers.mock import MockProvider
from joker_test.pipeline.stages.explore import ExploreStage, scan_solidified_assets

# ===== 命中检查 =====

def test_reuse_explicit_skips_exploration() -> None:
    """config.reuse 显式指定 → 直接命中，0 LLM。"""
    cfg = __import__(
        "joker_test.pipeline.types", fromlist=["ExploreConfig"]
    ).ExploreConfig(intent="x", reuse="tests/generated_smoke/test_a.py")
    stage = ExploreStage(provider=MagicMock(), backend=FakeBackend())
    result = stage.run(cfg)
    assert result.skipped is True
    assert result.reused_test_paths == ["tests/generated_smoke/test_a.py"]


def test_check_reuse_disabled_no_llm() -> None:
    """check_reuse=False → 直接未命中，0 LLM。"""
    cfg = __import__(
        "joker_test.pipeline.types", fromlist=["ExploreConfig"]
    ).ExploreConfig(intent="x", check_reuse=False, solidify=False, execute=False)
    mock_provider = MagicMock()
    mock_provider.create.side_effect = AssertionError("不该调 LLM")
    stage = ExploreStage(provider=mock_provider, backend=FakeBackend())
    result = stage.run(cfg)
    assert result.skipped is False


def test_scan_assets_extracts_docstrings(tmp_path: Path) -> None:
    """scan_solidified_assets 能提取 docstring。"""
    gen_dir = tmp_path / "generated_smoke"
    gen_dir.mkdir()
    (gen_dir / "test_login.py").write_text(
        '"""测试登录流程。"""\n\ndef test_login():\n    pass\n',
        encoding="utf-8",
    )
    (gen_dir / "test_logout.py").write_text(
        '"""测试退出登录。"""\n\ndef test_logout():\n    pass\n',
        encoding="utf-8",
    )
    assets = scan_solidified_assets(gen_dir)
    assert len(assets) == 2
    names = {a["name"] for a in assets}
    assert names == {"test_login.py", "test_logout.py"}
    docs = {a["name"]: a["docstring"] for a in assets}
    assert "登录" in docs["test_login.py"]


def test_llm_match_hit_skips_exploration(tmp_path: Path) -> None:
    """LLM 命中检查返回命中 → skipped=True。"""
    gen_dir = tmp_path / "generated_smoke"
    gen_dir.mkdir()
    (gen_dir / "test_login.py").write_text(
        '"""测试登录。"""\n\ndef test_login():\n    pass\n', encoding="utf-8"
    )
    mock_provider = MagicMock()
    mock_provider.create.return_value = {
        "content": [{"type": "text", "text": '{"hit": true, "path": "test_login.py", "reason": "意图匹配"}'}]
    }
    stage = ExploreStage(provider=mock_provider, backend=FakeBackend(), gen_dir=gen_dir)
    cfg = __import__(
        "joker_test.pipeline.types", fromlist=["ExploreConfig"]
    ).ExploreConfig(intent="登录", check_reuse=True, solidify=False, execute=False)
    result = stage.run(cfg)
    assert result.skipped is True
    assert "test_login.py" in result.reused_test_paths[0]
    assert result.match_reason is not None


def test_llm_match_miss_proceeds_to_explore(tmp_path: Path) -> None:
    """LLM 命中检查返回未命中 → 继续探索。"""
    mock_provider = MagicMock()
    mock_provider.create.return_value = {
        "content": [{"type": "text", "text": '{"hit": false, "reason": "无匹配"}'}]
    }
    backend = FakeBackend(
        screens={"root": ScreenCfg(texts_map={})}, initial_screen="root"
    )
    stage = ExploreStage(
        provider=mock_provider, backend=backend, gen_dir=tmp_path / "empty_gen"
    )
    cfg = __import__(
        "joker_test.pipeline.types", fromlist=["ExploreConfig"]
    ).ExploreConfig(
        intent="未知功能", mode="dfs", check_reuse=True,
        solidify=False, execute=False,
    )
    result = stage.run(cfg)
    assert result.skipped is False
    assert result.match_reason is not None


# ===== 三模式分发 =====

def test_dfs_mode_produces_state_map(tmp_path: Path) -> None:
    """dfs 模式调用 UIExplorer 产 StateMap。"""
    from joker_test.executor.base import BBox

    backend = FakeBackend(
        screens={
            "root": ScreenCfg(texts_map={"按钮": BBox(0.4, 0.4, 0.2, 0.1)}),
            "next": ScreenCfg(texts_map={"返回": BBox(0.4, 0.4, 0.2, 0.1)}),
        },
        transitions={
            ("root", "按钮"): "next",
            ("next", "返回"): "root",
            ("next", "key@escape"): "root",
        },
        initial_screen="root",
    )
    stage = ExploreStage(
        provider=MockProvider(), backend=backend,
        gen_dir=tmp_path / "gen", flow_dir=tmp_path / "flows",
    )
    cfg = __import__(
        "joker_test.pipeline.types", fromlist=["ExploreConfig"]
    ).ExploreConfig(
        intent="探索", mode="dfs", check_reuse=False,
        solidify=False, execute=False,
    )
    result = stage.run(cfg)
    assert result.skipped is False
    assert result.state_map is not None


def test_llm_mode_runs_without_crash(tmp_path: Path) -> None:
    """llm 模式调用 LLMExplorer，不崩溃。"""
    mock_provider = MagicMock()
    mock_provider.create.return_value = {
        "content": [{"type": "text", "text": '{"screen_name": "主页", "elements": []}'}]
    }
    backend = FakeBackend(
        screens={"root": ScreenCfg(texts_map={})}, initial_screen="root"
    )
    stage = ExploreStage(
        provider=mock_provider, backend=backend,
        gen_dir=tmp_path / "gen", flow_dir=tmp_path / "flows",
    )
    cfg = __import__(
        "joker_test.pipeline.types", fromlist=["ExploreConfig"]
    ).ExploreConfig(
        intent="探索", mode="llm", check_reuse=False,
        max_explore_steps=2, solidify=False, execute=False,
    )
    result = stage.run(cfg)
    assert result.skipped is False
