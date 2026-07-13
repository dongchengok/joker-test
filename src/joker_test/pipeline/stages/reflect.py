"""反思阶段：可信度评分 + 风险提示。

复用 reflection.detect_stuck_loop（确定性卡死检测）+ review_failure（误报审查）。
新增 LLM 可信度评分（断言强度/覆盖率/探索充分度）+ 风险提示。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from joker_test.llm.base import (
    LLMProvider,
    build_user_message,  # noqa: PLC0415
)
from joker_test.pipeline.types import (
    ExecuteResult,
    ExploreConfig,
    ExploreResult,
    ReflectResult,
    ReportResult,
    Risk,
)
from joker_test.reflection import detect_stuck_loop

_LOGGER = logging.getLogger(__name__)


class ReflectStage:
    """反思阶段：可信度 + 风险。"""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def run(
        self,
        explore: ExploreResult,
        execute: ExecuteResult | None,
        report: ReportResult,
        config: ExploreConfig,
    ) -> ReflectResult:
        stuck_warnings = self._detect_stuck(explore)
        false_positives = self._review_failures(execute)

        try:
            confidence, risks, reasoning = self._llm_reflect(explore, execute, report)
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning("LLM 反思失败：%s，降级为低可信度", e)
            return ReflectResult(
                confidence_score=0.3,
                stuck_warnings=stuck_warnings,
                false_positives=false_positives,
                risks=[],
                reflect_reasoning="LLM 反思失败，降级为低可信度",
            )

        return ReflectResult(
            confidence_score=confidence,
            false_positives=false_positives,
            stuck_warnings=stuck_warnings,
            risks=risks,
            reflect_reasoning=reasoning,
        )

    def _detect_stuck(self, explore: ExploreResult) -> list[str]:
        """确定性卡死检测。"""
        if explore.explore_log and detect_stuck_loop(explore.explore_log):
            return ["探索操作信号连续重复，疑似卡死"]
        return []

    def _review_failures(
        self, execute: ExecuteResult | None
    ) -> list[dict[str, Any]]:
        """对失败用例调误报审查。"""
        if execute is None:
            return []
        from joker_test.reflection import review_failure

        fps: list[dict[str, Any]] = []
        for result in execute.session.results:
            if result.status != "failed":
                continue
            try:
                is_fp, reason = review_failure(self._provider, result)
            except Exception as e:  # noqa: BLE001
                _LOGGER.warning("误报审查失败：%s", e)
                is_fp, reason = False, "审查失败"
            fps.append(
                {"test": result.test.name, "is_false_positive": is_fp, "reason": reason}
            )
        return fps

    def _llm_reflect(
        self,
        explore: ExploreResult,
        execute: ExecuteResult | None,
        report: ReportResult,
    ) -> tuple[float, list[Risk], str]:
        """LLM 综合可信度评分 + 风险提示。"""
        parts = [
            f"探索方式: {'; '.join(explore.explore_log) or '未知'}",
            f"命中跳过: {explore.skipped}",
        ]
        if execute is not None:
            parts.append(
                f"执行结果: passed={execute.session.passed}, "
                f"failed={execute.session.failed}"
            )
        parts.append(f"阶段覆盖: {report.stage_coverage}")

        prompt = (
            "<本次测试运行摘要>\n"
            + "\n".join(parts)
            + "\n</本次测试运行摘要>\n\n"
            "请评估：\n"
            "1. 综合可信度（0.0-1.0，考虑断言强度/覆盖率/探索充分度）\n"
            "2. 风险项（未覆盖区域/探索中断/低可信等）\n"
            "请只回答 JSON："
            '{"confidence": 0.0, "risks": '
            '[{"category": "", "description": "", "severity": "P2"}], '
            '"reasoning": "..."}'
        )
        msg = self._provider.create(messages=[build_user_message(prompt)])
        text = _extract_text(msg)
        data = json.loads(text)
        confidence = float(data.get("confidence", 0.5))
        risks = [
            Risk(
                category=r.get("category", "unknown"),
                description=r.get("description", ""),
                severity=r.get("severity", "P2"),
            )
            for r in data.get("risks", [])
        ]
        return confidence, risks, data.get("reasoning", "")


def _extract_text(msg: dict[str, Any]) -> str:
    """从 LLM Message 提取纯文本。"""
    for block in msg.get("content", []):
        if isinstance(block, dict) and "text" in block:
            return block["text"]
    return ""
