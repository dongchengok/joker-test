"""OCRPlugin —— OCR 文字+坐标识别插件（内置）。

把现有 LLMExplorer._perceive + ConversationStrategy._build_step_text 的 OCR 逻辑
搬成自包含插件。通过 4 个注入点向探索流程提供信息。
"""
from __future__ import annotations

from typing import Any


class OCRPlugin:
    """OCR 文字+坐标识别插件。

    从 backend.state 提取 OCR 文字及其中心坐标，注入每轮对话。
    """

    @property
    def name(self) -> str:
        return "ocr"

    def inject_system_prompt(self) -> str:
        """告诉 LLM 界面元素的坐标格式。"""
        return (
            "界面元素格式：文字@(x,y)，x/y 是归一化坐标[0,1]"
            "（左0右1，上0下1），表示该文字在屏幕上的中心位置。\n"
            "点击有文字的按钮时，直接用它的坐标作为 click 的 x/y，不需要自己估算。"
        )

    def inject_step(self, screenshot: Any, backend: Any, ctx: Any) -> str:
        """从 backend.state 提取 OCR 文字+坐标，返回格式化文本。"""
        try:
            state = backend.state
            texts = list(state.texts)
            if not texts:
                return ""
        except Exception:  # noqa: BLE001
            return ""

        # 提取文字 + 中心坐标（支持 AirtestBackend._ocr_results / FakeBackend.text_elements）
        text_elements: list[dict[str, Any]] = []
        raw = getattr(state, "_ocr_results", None) or getattr(state, "text_elements", None)
        if raw:
            for r in raw:
                bbox = r.get("bbox")
                if bbox:
                    cx = round(bbox.x + bbox.w / 2, 3)
                    cy = round(bbox.y + bbox.h / 2, 3)
                    text_elements.append({"text": r["text"], "x": cx, "y": cy})

        if not text_elements:
            # 降级：只有文字无坐标
            return "OCR: " + ", ".join(texts[:15]) if texts else ""

        lines = [f"  {e['text']}@({e['x']:.2f},{e['y']:.2f})" for e in text_elements[:15]]
        return "界面元素(文字@坐标):\n" + "\n".join(lines)

    def inject_action_hint(self, screenshot: Any, backend: Any, ctx: Any) -> str:
        """OCR 插件不做动作建议。"""
        return ""

    def validate(self, decision: Any, result: Any) -> str | None:
        """OCR 插件不做校验。"""
        return None


__all__ = ["OCRPlugin"]
