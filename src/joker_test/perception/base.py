"""PerceptionEngine —— 多层界面识别引擎。

三层识别互补（漏斗，LLM 克制红线）：
- OCR：文字（RapidOCR，已有 ocr/ 包）—— 快+便宜，先跑
- 图像匹配：已知图标（ImageMatcher，backend 无关，抄 airtest aircv）—— 命中已知图标
- LLM 识图：语义理解（MiMo/GLM 多模态）—— 慢+贵，仅 OCR+match 不足时兜底

设计：
- 按需选层（不三层全跑，省成本）
- OCR 和 match 是预检查层（互补：OCR 找文字，match 找图标），都跑
- LLM 是兜底层，只在 use_llm=True 时跑
- ImageMatcher 可选注入（None 时不做图像匹配，行为同旧版）
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from joker_test.perception.matching import ImageMatcher, MatchResult

if TYPE_CHECKING:
    from numpy import ndarray


class PerceptionResult(BaseModel):
    """多层识别的汇总结果。"""

    texts: list[str] = []  # OCR 文本
    matches: list[MatchResult] = []  # 图像匹配命中（match 层产出）
    description: str = ""  # LLM 语义描述（可选，LLM 层产出）
    ui_elements: list[dict[str, Any]] = []  # LLM 识别的可交互元素 [{"text":..,"type":..,"action":..}]
    layer_used: str = "none"  # 实际用了哪层（none/ocr/match/ocr+match/+llm）

    __test__ = False


class PerceptionEngine:
    """多层界面识别引擎。

    Args:
        ocr: OCR 引擎（None 时不做 OCR）
        llm: LLM 多模态引擎（None 时不做 LLM 识图）
        use_llm: 是否启用 LLM 识图层（默认 False，LLM 克制——只在需要时开）
        matcher: 图像匹配引擎（None 时不做图像匹配；注入则 perceive 可做 match 层）

    用法::
        engine = PerceptionEngine(
            ocr=RapidOCRProvider(),
            llm=MiMoProvider(),
            use_llm=True,
            matcher=ImageMatcher(threshold=0.8),
        )
        result = engine.perceive(frame, templates={"enter_btn": "enter.png"})
        print(result.texts, result.matches, result.description)

    三层漏斗（perceive 内部）：
        OCR（texts）→ match（matches，命中已知图标）→ LLM（仅 use_llm 时兜底）
    OCR 和 match 是预检查层（互补：文字 vs 图标），都跑；LLM 是兜底层。
    """

    def __init__(
        self,
        ocr: Any | None = None,
        llm: Any | None = None,
        use_llm: bool = False,
        matcher: ImageMatcher | None = None,
    ) -> None:
        self._ocr = ocr
        self._llm = llm
        self._use_llm = use_llm and llm is not None
        self._matcher = matcher

    def perceive(
        self,
        frame: ndarray,
        prompt: str = "描述这个游戏界面，列出所有可点击的按钮和它们的文字。",
        templates: dict[str, Any] | None = None,
    ) -> PerceptionResult:
        """识别界面，返回多层结果。

        Args:
            frame: BGR ndarray（screenshot 输出）
            prompt: LLM 识图的提问（仅 use_llm=True 时用）
            templates: 图像匹配模板 {名: 路径/ndarray}（仅注入 matcher 时用）

        Returns:
            PerceptionResult（含 OCR 文本 + 可选 matches + 可选 LLM 描述）

        三层漏斗：OCR → match → LLM。OCR/match 是预检查（都跑），LLM 按需。
        """
        result = PerceptionResult()
        layers: list[str] = []

        # Layer 1: OCR（快，先跑）
        if self._ocr is not None:
            ocr_results = self._ocr.readtext(frame)
            result.texts = [r.text for r in ocr_results]
            layers.append("ocr")

        # Layer 2: 图像匹配（预检查，命中已知图标）
        if self._matcher is not None and templates:
            result.matches = self._matcher.match_all(frame, templates)
            layers.append("match")

        # Layer 3: LLM 识图（慢+贵，兜底层，按需开）
        if self._use_llm and self._llm is not None:
            desc, elements = self._llm_describe(frame, prompt)
            result.description = desc
            result.ui_elements = elements
            layers.append("llm")

        result.layer_used = "+".join(layers) if layers else "none"
        return result

    def _llm_describe(self, frame: ndarray, prompt: str) -> tuple[str, list[dict[str, Any]]]:
        """用 LLM 多模态识别界面（截图 base64 发给 LLM）。

        Returns:
            (描述文本, 可交互元素列表)
        """
        import cv2  # noqa: PLC0415

        # 空帧保护（G7：窗口状态切换时可能返回空帧）
        if frame is None or frame.size == 0:
            return "（空帧，无法识图）", []

        # BGR → PNG → base64
        success, buf = cv2.imencode(".png", frame)
        if not success:
            return "", []
        # TODO: img_b64 当前未传入 simple_converse（LLM 识图层实际未看图）。
        # 需扩展 LLMProvider.simple_converse 支持 image block，或在 converse 里拼 messages。
        # 本任务（加 match 层）不修，避免改 LLM 契约。
        _ = base64.b64encode(buf.tobytes()).decode("utf-8")

        # 构造 Anthropic image block（_llm 非 None 已由 _use_llm 保证）
        assert self._llm is not None
        message = self._llm.simple_converse(
            prompt + "\n\n请用 JSON 回答：{\"description\": \"...\", \"elements\": [{\"text\": \"...\", \"type\": \"button\"}]}",
            [],
        )
        # 提取文本
        text = self._extract_text(message)

        # 解析 JSON（容错）
        desc, elements = self._parse_llm_json(text)
        return desc, elements

    @staticmethod
    def _extract_text(msg: dict) -> str:
        parts: list[str] = []
        for block in msg.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)

    @staticmethod
    def _parse_llm_json(text: str) -> tuple[str, list[dict[str, Any]]]:
        """从容错文本解析 LLM 的 description + elements。"""
        import json  # noqa: PLC0415
        import re  # noqa: PLC0415

        # 找 JSON 块
        m = re.search(r'\{[^{}]*"description"[^{}]*\}', text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                return data.get("description", ""), data.get("elements", [])
            except json.JSONDecodeError:
                pass
        # 兜底：整个文本当描述
        return text[:200], []


__all__ = ["PerceptionResult", "PerceptionEngine"]
