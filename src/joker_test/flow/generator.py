"""RecordedFlowGenerator —— 把录制操作流 + 截图交给 LLM 生成 pytest test_case。

与 SmokeTestGenerator 的区别：
  - SmokeTestGenerator 输入是 StateMap（界面拓扑图，静态结构），输出是冒烟测试
  - RecordedFlowGenerator 输入是 RecordedFlow（操作时间序列 + 截图），输出是测试

两者共享下游链路：_parse_code_blocks + QualityChecker + write_tests_to_dir + run_tests。

流程：
  1. 对每个 click 步骤跑 semanticize_step（坐标 → OCR 文字/图像模板）
  2. 渲染 recorded_flow.md.j2（操作流 + 语义化结果 + 截图说明）
  3. 读截图为 base64 列表，create(prompt, [], images=[...], reasoning=16000)
  4. 复用 _parse_code_blocks + _pair_into_generated_tests 解析代码块
  5. QualityChecker.check 兜底（ruff + ast.parse）

设计要点：
- 状态自洽：持有 provider + quality_checker，不依赖外部
- LLM 克制：只调一次（生成），语义化是确定性算法不调 LLM
- 不含 I/O（不落盘）：generate 返回 list[GeneratedTest]，落盘由调用方做

用法::

    gen = RecordedFlowGenerator(provider=llm, ocr_provider=ocr)
    tests = gen.generate(flow, flow_dir, game_meta)
    for t in tests:
        print(t.test_filename, len(t.test_code))
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from joker_test.flow.semantics import _cv_imread, semanticize_step
from joker_test.flow.types import RecordedFlow, RecordedStep, SemanticResult
from joker_test.generator.generator import (
    _extract_text,  # 复用（跨包同项目，解析 LLM 回复文本）
    _pair_into_generated_tests,  # 复用（配对 test+spec 代码块）
    _parse_code_blocks,  # 复用（解析 ### filename + ```python 代码块）
)
from joker_test.generator.quality import QualityChecker, QualityError
from joker_test.generator.types import GeneratedTest
from joker_test.llm.base import build_user_message
from joker_test.prompts import render_recorded_flow_prompt
from joker_test.trace import trace_event, trace_stage

if TYPE_CHECKING:
    from joker_test.llm.base import LLMProvider
    from joker_test.ocr.base import OCRProvider
    from joker_test.reporters.base import TestSession

logger = logging.getLogger(__name__)


class RecordedFlowGenerator:
    """录制操作流 → pytest test_case 生成器。

    Args:
        provider: LLM provider（多模态，能看图）
        quality_checker: 质量检查器（默认 QualityChecker，可注入 mock 测试）
        ocr_provider: OCR 引擎（坐标语义化用，None 不做 OCR 匹配）

    用法::

        gen = RecordedFlowGenerator(provider=llm, ocr_provider=ocr)
        tests = gen.generate(flow, flow_dir, game_meta)
    """

    def __init__(
        self,
        provider: LLMProvider,
        quality_checker: QualityChecker | None = None,
        ocr_provider: OCRProvider | None = None,
    ) -> None:
        self._provider = provider
        self._quality = quality_checker or QualityChecker()
        self._ocr = ocr_provider

    def generate(
        self,
        flow: RecordedFlow,
        flow_dir: Path,
        game_meta: dict,
    ) -> list[GeneratedTest]:
        """从录制操作流生成 test_case。

        Args:
            flow: 录制的操作流
            flow_dir: 录制产物目录（含 screenshots/，语义化模板也写这里）
            game_meta: 游戏元数据

        Returns:
            GeneratedTest 列表

        Raises:
            QualityError: 生成的代码未通过质量检查
        """
        with trace_stage("generate"):
            # 1. 对每个 click 步骤做语义化
            semanticized_steps: list[tuple[RecordedStep, SemanticResult]] = []
            warnings: list[str] = []
            for step in flow.steps:
                result = semanticize_step(step, flow_dir, self._ocr)
                semanticized_steps.append((step, result))
                if result.warning:
                    warnings.append(f"step {step.action}: {result.warning}")

            # trace：语义化结果（每步的定位方式 + OCR 是否找到）
            trace_event("semanticized", {
                "step_count": len(semanticized_steps),
                "locators": [
                    {"step": i, "locator_type": sem.locator_type, "ocr_found": bool(sem.text)}
                    for i, (_, sem) in enumerate(semanticized_steps)
                ],
            })

            # 2. 构建操作流 JSON（含语义化结果）+ 收集截图
            flow_json = self._build_flow_json(flow, semanticized_steps)
            images = self._collect_images(flow, flow_dir)

            # 3. 渲染 prompt
            prompt = render_recorded_flow_prompt(flow_json, game_meta, warnings)
            logger.info("渲染录制流转 test_case prompt（%d 字符，%d 张图）",
                        len(prompt), len(images))

            # 4. 调 LLM（多模态，喂截图）
            msg = self._provider.create(messages=[build_user_message(prompt, images or None)])
            reply_text = _extract_text(msg)
            if not reply_text:
                raise QualityError("LLM 回复为空，无法解析代码")

            # 5. 解析代码块
            raw_blocks = _parse_code_blocks(reply_text)
            if not raw_blocks:
                raise QualityError(
                    f"LLM 回复中未找到代码块。回复前 200 字: {reply_text[:200]}"
                )
            logger.info("解析到 %d 个代码块", len(raw_blocks))

            # trace：代码解析结果
            trace_event("code_parsed", {"block_count": len(raw_blocks)})

            # 6. 配对 + 质量兜底
            tests = _pair_into_generated_tests(raw_blocks)
            for t in tests:
                self._quality.check(t)
                logger.info("质量检查通过: %s", t.test_filename)
                # trace：每个文件质检通过
                trace_event("quality_ok", {"filename": t.test_filename})

            return tests

    def rewrite_failed(
        self,
        tests: list[GeneratedTest],
        session: TestSession,
        flow: RecordedFlow,
        flow_dir: Path,
        game_meta: dict,
    ) -> list[GeneratedTest]:
        """试跑失败后回喂 LLM 重写整段失败函数（断言+操作都改）。

        Args:
            tests: 当前 test_case 列表
            session: 试跑结果（含失败的 TestResult）
            flow: 录制操作流（给 LLM 上下文）
            flow_dir: 录制产物目录
            game_meta: 游戏元数据

        Returns:
            重写后的 test_case 列表
        """
        # 收集失败信息
        failures: list[dict] = []
        for r in session.results:
            if r.status != "failed":
                continue
            failures.append({
                "test_name": r.test.name,
                "error": r.error or "未知错误",
            })

        if not failures:
            return tests

        # 构建回喂 prompt：失败信息 + 原始操作流（含真实 OCR）
        semanticized_steps: list[tuple[RecordedStep, SemanticResult]] = []
        for step in flow.steps:
            result = semanticize_step(step, flow_dir, self._ocr)
            semanticized_steps.append((step, result))
        flow_json = self._build_flow_json(flow, semanticized_steps)

        import json  # noqa: PLC0415

        failure_json = json.dumps(failures, ensure_ascii=False, indent=2)
        prompt = (
            "以下测试用例在真机上试跑失败了。请根据失败信息**修正代码**。\n\n"
            f"<失败信息>\n{failure_json}\n</失败信息>\n\n"
            f"<原始操作流（含真实 OCR 数据）>\n{flow_json}\n</原始操作流>\n\n"
            "**修正要求**：\n"
            "1. 断言关键词必须从操作流的 `ocr_before`/`ocr_after` 真实 OCR 文本里取\n"
            "2. 如果失败是 click_text 找不到文字，换一个 OCR 实际读到的文字\n"
            "3. 如果失败是 click(坐标) 点击无效，保持原坐标（坐标是探索时验证过的，不要改）\n"
            "4. 保持测试函数结构不变，只修正导致失败的行\n\n"
            f"以下是当前的测试代码（{len(tests)} 个文件）：\n\n"
        )
        for t in tests:
            prompt += f"### {t.test_filename}\n```python\n{t.test_code}\n```\n\n"

        prompt += (
            "\n请输出**修正后的完整代码**（只输出失败的文件的修正版，格式：### filename.py + ```python）。\n"
            "如果某个文件没有失败，不要输出它。"
        )

        msg = self._provider.create(messages=[build_user_message(prompt)])
        reply_text = _extract_text(msg)
        if not reply_text:
            logger.warning("回喂重写：LLM 回复为空，返回原版")
            return tests

        raw_blocks = _parse_code_blocks(reply_text)
        if not raw_blocks:
            logger.warning("回喂重写：LLM 回复无代码块，返回原版")
            return tests

        # 用重写的代码替换失败的 test（没重写的保持原版）
        rewritten_map: dict[str, str] = {}
        for filename, code in raw_blocks:
            rewritten_map[filename] = code

        rewritten_tests: list[GeneratedTest] = []
        for t in tests:
            if t.test_filename in rewritten_map:
                new_code = rewritten_map[t.test_filename]
                try:
                    rewritten = GeneratedTest(
                        system=t.system,
                        test_code=new_code,
                        spec_code=t.spec_code,
                        test_filename=t.test_filename,
                        spec_filename=t.spec_filename,
                    )
                    self._quality.check(rewritten)
                    rewritten_tests.append(rewritten)
                    logger.info("回喂重写成功: %s", t.test_filename)
                except Exception as e:  # noqa: BLE001
                    logger.warning("回喂重写质检失败 %s: %s，保留原版", t.test_filename, e)
                    rewritten_tests.append(t)
            else:
                rewritten_tests.append(t)  # 没重写的保持原版

        # trace：回喂重写结果（重写了哪些文件）
        trace_event("rewritten", {
            "files": [t.test_filename for t in rewritten_tests
                      if t.test_filename in rewritten_map],
        })

        return rewritten_tests

    def _build_flow_json(
        self,
        flow: RecordedFlow,
        semanticized_steps: list[tuple[RecordedStep, SemanticResult]],
    ) -> str:
        """构建操作流 JSON 字符串（含语义化结果，给 prompt 用）。"""
        import json  # noqa: PLC0415

        steps_data: list[dict] = []
        for step, sem in semanticized_steps:
            entry: dict = {
                "action": step.action,
                "note": step.note,
                "key": step.key,
                "text": step.text,
            }
            # 语义化结果（告诉 LLM 这个坐标应该用什么定位）
            if sem.locator_type == "click_text":
                entry["locator"] = f'click_text("{sem.text}")'
            elif sem.locator_type == "click_coord":
                entry["locator"] = f"click_coord({sem.x:.3f}, {sem.y:.3f})"
            # 附带真实 OCR 文本（断言必须从这里取关键词，不准猜）
            if step.ocr_texts_before:
                entry["ocr_before"] = step.ocr_texts_before  # 操作前界面 OCR（完整）
            if step.ocr_texts_after:
                entry["ocr_after"] = step.ocr_texts_after  # 操作后界面 OCR（完整）
            steps_data.append(entry)

        return json.dumps(
            {
                "name": flow.name,
                "description": flow.description,
                "step_count": len(flow.steps),
                "steps": steps_data,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _collect_images(self, flow: RecordedFlow, flow_dir: Path) -> list[str]:
        """收集操作后截图转 base64（每步一张，上限 8 张省 token）。"""
        import base64  # noqa: PLC0415

        import cv2  # noqa: PLC0415

        images: list[str] = []
        max_images = 8
        for step in flow.steps:
            if len(images) >= max_images:
                break
            shot_ref = step.screenshot_after
            if not shot_ref:
                continue
            shot_path = Path(shot_ref)
            if not shot_path.is_absolute():
                shot_path = flow_dir / shot_ref
            frame = _cv_imread(shot_path)
            if frame is None:
                continue
            success, buf = cv2.imencode(".png", frame)
            if success:
                images.append(base64.b64encode(buf.tobytes()).decode("utf-8"))
        return images


__all__ = ["RecordedFlowGenerator"]
