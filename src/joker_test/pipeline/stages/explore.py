"""探索阶段：智能入口（固化命中检查）+ 三模式探索。

manual → GlobalRecorder(pynput)
dfs    → UIExplorer（确定性，产 StateMap）
llm    → LLMExplorer（agentic loop，产 StateMap + 操作轨迹）

三种模式统一产 RecordedFlow，下游不区分来源。
"""
from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from typing import Any

from joker_test.executor.base import ExecutorBackend
from joker_test.explorer.explorer import UIExplorer
from joker_test.explorer.llm_explorer import LLMExplorer
from joker_test.explorer.types import StateMap
from joker_test.flow.recorder import GlobalRecorder
from joker_test.flow.types import RecordedFlow
from joker_test.llm.base import (
    LLMProvider,
    build_user_message,  # noqa: PLC0415
)
from joker_test.pipeline.types import ExploreConfig, ExploreResult

_LOGGER = logging.getLogger(__name__)


def scan_solidified_assets(gen_dir: str | Path) -> list[dict[str, str]]:
    """扫描已固化资产，提取文件名 + docstring。

    Args:
        gen_dir: tests/generated_smoke 目录

    Returns:
        [{"name": "test_xxx.py", "docstring": "..."}]
    """
    gen_path = Path(gen_dir)
    if not gen_path.exists():
        return []
    assets: list[dict[str, str]] = []
    for py in sorted(gen_path.glob("test_*.py")):
        docstring = ""
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
            if (
                tree.body
                and isinstance(tree.body[0], ast.Expr)
                and isinstance(tree.body[0].value, ast.Constant)
                and isinstance(tree.body[0].value.value, str)
            ):
                docstring = tree.body[0].value.value.strip()
        except SyntaxError:
            docstring = ""
        assets.append({"name": py.name, "docstring": docstring})
    return assets


class ExploreStage:
    """探索阶段：固化命中检查 + 三模式探索。"""

    def __init__(
        self,
        provider: LLMProvider,
        backend: ExecutorBackend,
        gen_dir: str | Path = "tests/generated_smoke",
        flow_dir: str | Path = "flows",
        plugin_manager: Any = None,
    ) -> None:
        self._provider = provider
        self._backend = backend
        self._gen_dir = Path(gen_dir)
        self._flow_dir = Path(flow_dir)
        self._plugin_manager = plugin_manager

    def run(self, config: ExploreConfig) -> ExploreResult:
        log: list[str] = []

        # 1. 显式 reuse → 直接命中，0 LLM
        if config.reuse is not None:
            return ExploreResult(
                skipped=True,
                reused_test_paths=[config.reuse],
                match_reason="显式指定复用资产",
                explore_log=log,
            )

        # 2. check_reuse → LLM 比对
        match_reason: str | None = None
        if config.check_reuse:
            hit = self._check_solidified(config.intent)
            if hit is not None:
                return ExploreResult(
                    skipped=True,
                    reused_test_paths=[str(self._gen_dir / hit["path"])],
                    match_reason=hit["reason"],
                    explore_log=log,
                )
            match_reason = "未命中已固化资产"

        # 3. 未命中 → 按 mode 探索
        flow, state_map = self._explore(config, log)
        return ExploreResult(
            flow=flow,
            flow_dir=str(self._flow_dir),
            state_map=state_map,
            match_reason=match_reason,
            explore_log=log,
        )

    def _check_solidified(self, intent: str) -> dict[str, str] | None:
        """扫资产 + LLM 比对，返回命中 dict 或 None。"""
        assets = scan_solidified_assets(self._gen_dir)
        if not assets:
            return None
        prompt = (
            "<测试意图>\n"
            f"{intent}\n"
            "</测试意图>\n\n"
            "<已固化资产>\n"
            + json.dumps(assets, ensure_ascii=False, indent=2)
            + "\n</已固化资产>\n\n"
            "判断测试意图是否已被某个已固化资产覆盖。"
            "请只回答 JSON："
            '{"hit": true/false, "path": "文件名", "reason": "..."}'
        )
        try:
            msg = self._provider.create(messages=[build_user_message(prompt)])
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("命中检查 LLM 调用失败：%s，降级为未命中", e)
            return None
        text = _extract_text(msg)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if data.get("hit") is True and data.get("path"):
            return {"path": data["path"], "reason": data.get("reason", "LLM 命中")}
        return None

    def _explore(
        self, config: ExploreConfig, log: list[str]
    ) -> tuple[RecordedFlow | None, StateMap | None]:
        """按 mode 探索，返回 (flow, state_map)。"""
        if config.mode == "manual":
            return self._explore_manual(config, log), None
        if config.mode == "dfs":
            return self._explore_dfs(config, log)
        return self._explore_llm(config, log)

    def _explore_manual(
        self, config: ExploreConfig, log: list[str]
    ) -> RecordedFlow:
        """pynput 全局监听人类操作。"""
        self._flow_dir.mkdir(parents=True, exist_ok=True)
        recorder = GlobalRecorder(output_dir=self._flow_dir, pynput_mode=True)
        recorder.start()
        flow = recorder.stop()
        recorder.save_flow_yaml(flow)
        log.append(f"manual 录制 {len(flow.steps)} 步")
        return flow

    def _explore_dfs(
        self, config: ExploreConfig, log: list[str]
    ) -> tuple[RecordedFlow | None, StateMap]:
        """UIExplorer 确定性 DFS，产 StateMap + 程序化录制。"""
        recorder = GlobalRecorder(
            output_dir=self._flow_dir, backend=self._backend, pynput_mode=False
        )
        recorder.start()
        explorer = UIExplorer(
            self._backend,
            max_depth=min(config.max_explore_steps, 5),
            screen_change_timeout=1.0,
        )
        state_map = explorer.explore()
        flow = recorder.stop()
        if flow.steps:
            recorder.save_flow_yaml(flow)
        log.append(f"dfs 探索 {len(state_map.screens)} 屏")
        return flow if flow.steps else None, state_map

    def _explore_llm(
        self, config: ExploreConfig, log: list[str]
    ) -> tuple[RecordedFlow | None, StateMap]:
        """LLMExplorer agentic loop，产 StateMap + 程序化录制。"""
        recorder = GlobalRecorder(
            output_dir=self._flow_dir, backend=self._backend, pynput_mode=False
        )
        recorder.start()

        from joker_test.explorer.conversation_strategy import ConversationStrategy  # noqa: PLC0415
        from joker_test.explorer.react_strategy import ReactStateStrategy  # noqa: PLC0415
        from joker_test.explorer.strategy import ExploreStrategy  # noqa: PLC0415

        pm = self._plugin_manager
        strategy: ExploreStrategy
        if config.explore_strategy == "conversation":
            strategy = ConversationStrategy(
                llm=self._provider, intent=config.intent, plugin_manager=pm,
            )
        else:
            strategy = ReactStateStrategy(
                llm=self._provider, intent=config.intent, plugin_manager=pm,
            )

        explorer = LLMExplorer(
            self._backend,
            self._provider,
            strategy=strategy,
            max_steps=config.max_explore_steps,
            recorder=recorder,
            plugin_manager=pm,
        )
        state_map = explorer.explore()
        flow = recorder.stop()
        if flow.steps:
            recorder.save_flow_yaml(flow)
        log.append(
            f"llm 探索 {len(state_map.screens)} 屏（{config.explore_strategy}）"
        )
        return flow if flow.steps else None, state_map


def _extract_text(msg: dict[str, Any]) -> str:
    """从 LLM Message 提取纯文本。"""
    for block in msg.get("content", []):
        if isinstance(block, dict) and "text" in block:
            return block["text"]
    return ""
