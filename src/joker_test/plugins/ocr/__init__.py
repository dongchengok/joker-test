"""OCRPlugin —— OCR 文字+坐标识别插件（内置）。

把 OCR 相关的感知知识集中在一个插件里（ADR-013），避免散落在 explorer/策略代码中：
- inject_step：提取 OCR 文字+坐标，过滤噪声后注入每轮对话
- validate：用 OCR 文本变化判断操作是否有效（像素 diff 无法区分装饰动画噪声
  和真实界面变化，OCR 文本变化才是可靠的语义信号）

噪声过滤规则（_filter_noise）是感知层知识：窗口标题、单字符符号、OCR 乱码、
版本号等帧间不稳定的文本。这些规则集中在本插件，不污染 explorer 执行循环。
"""
from __future__ import annotations

import re
from typing import Any

# OCR 噪声过滤规则（感知层知识，集中管理）
# 单字符符号（窗口装饰 ×口□ 等）
_MAX_NOISE_LEN = 1
# 窗口标题/游戏名前缀（帧间常驻但无语义价值）
_WINDOW_TITLE_PREFIXES = ("Shattered", "PIXEL", "DUNGEON")
# OCR 乱码：含特殊符号且短（如 "B'E'EA"）
_GARBAGE_MAX_LEN = 4
_GARBAGE_CHARS = ("'", '"', "`")
# 版本号/装饰数字（V3.3.8、￥3.3.8、3.3.8 等）
_VERSION_PATTERN = re.compile(r"^[V￥$]\d|^\d+\.\d+\.\d+$")


class OCRPlugin:
    """OCR 文字+坐标识别插件。

    从 backend.state 提取 OCR 文字及其中心坐标，注入每轮对话。
    同时在 validate 中用 OCR 文本变化判断操作有效性。

    状态自洽：_last_texts 持有上一步的文本签名，用于 validate 比较。
    """

    def __init__(self) -> None:
        self._last_texts: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return "ocr"

    def inject_system_prompt(self) -> str:
        """告诉 LLM OCR 坐标是辅助参考（文字位置，非控件手柄位置）。"""
        from joker_test.prompts.loader import load_ocr_format_prompt  # noqa: PLC0415

        return load_ocr_format_prompt()

    def inject_step(self, screenshot: Any, backend: Any, ctx: Any) -> str:
        """从 backend.state 提取 OCR 文字+坐标（过滤噪声后），返回格式化文本。"""
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

        # 更新上一步文本签名（用于 validate 比较，只含有意义的文本）
        self._last_texts = _filter_noise_texts(texts)

        if not text_elements:
            # 降级：只有文字无坐标
            clean = _filter_noise_texts(texts)
            return "OCR: " + ", ".join(clean[:15]) if clean else ""

        lines = [f"  {e['text']}@({e['x']:.2f},{e['y']:.2f})" for e in text_elements[:15]]
        return "界面元素(文字@坐标):\n" + "\n".join(lines)

    def inject_action_hint(self, screenshot: Any, backend: Any, ctx: Any) -> str:
        """OCR 插件不做动作建议。"""
        return ""

    def validate(self, decision: Any, result: Any, backend: Any = None) -> str | None:
        """OCR plugin does not make semantic judgments.

        Following Open-AutoGLM/OpenCode: the system collects data (inject_step
        provides OCR text+coordinates), the LLM reasons from the data.
        """
        return None


def _filter_noise_texts(texts: list[str]) -> tuple[str, ...]:
    """过滤 OCR 噪声文本，返回排序后的有意义文本元组。

    噪声特征：单字符符号、窗口标题前缀、OCR 乱码（含特殊符号的短串）、版本号。
    这些文本帧间不稳定（装饰动画/版本号闪烁），不过滤会导致变化检测误报。
    """
    meaningful = sorted(
        t.strip() for t in texts if _is_meaningful_text(t)
    )
    return tuple(meaningful)


def _is_meaningful_text(text: str) -> bool:
    """判断 OCR 文本是否有意义（过滤噪声）。

    Args:
        text: OCR 识别的原始文本

    Returns:
        True = 有意义（UI 标签/按钮/数值），False = 噪声（窗口标题/符号/乱码/版本号）
    """
    t = text.strip()
    if len(t) <= _MAX_NOISE_LEN:
        return False  # 单字符符号（×口□）
    # 窗口标题/游戏名（帧间常驻但无语义价值）
    if any(t.startswith(p) for p in _WINDOW_TITLE_PREFIXES):
        return False
    # OCR 乱码：含特殊符号且短（如 "B'E'EA"）
    if len(t) <= _GARBAGE_MAX_LEN and any(c in t for c in _GARBAGE_CHARS):
        return False
    # 版本号/装饰数字（V3.3.8、￥3.3.8 等）
    if _VERSION_PATTERN.match(t):
        return False
    return True


__all__ = ["OCRPlugin"]
