"""元素检测、去重指纹、切屏判断（M2 探索器的"感知"组件）。

复用 M1 的 ExecutorBackend（state.texts/find_text）+ coords（pixel_diff_ratio）。
模板匹配（icon 检测）用 cv2.matchTemplate（M2 新增能力）。

设计要点：
- detect_elements 只做"当前帧有什么元素"，不做探索逻辑（那是 explorer.py 的事）
- compute_fingerprint 用元素文本签名 hash，判断两个界面是否相同（去重）
- has_screen_changed 复用 coords.pixel_diff_ratio，>0.005 = 切屏（实测阈值）
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from joker_test.executor.coords import pixel_diff_ratio
from joker_test.explorer.types import UIElement

if TYPE_CHECKING:
    from numpy import ndarray

    from joker_test.executor.base import ExecutorBackend


def detect_elements(backend: ExecutorBackend) -> list[UIElement]:
    """检测当前界面的 UI 元素。

    M2 实现：
    - OCR 文本 → button（复用 backend.state.texts + find_text）
    - cv2 模板匹配 → icon（可选，M2 简化：暂不预设模板，返回空，留给后续配置）

    Args:
        backend: 已 connect 的 ExecutorBackend（state 指向当前帧）

    Returns:
        元素列表（每个含归一化 bbox）。同一帧内多次调用 state 只触发一次 OCR（LazyState 缓存）。
    """
    elements: list[UIElement] = []

    # (a) OCR 文本 → button 元素
    texts = backend.state.texts
    for text in texts:
        bbox = backend.state.find_text(text)
        if bbox is not None:
            elements.append(
                UIElement(
                    type="button",
                    text=text,
                    bbox=(bbox.x, bbox.y, bbox.w, bbox.h),
                )
            )

    # (b) cv2 模板匹配 → icon 元素（M2 简化：不预设模板，留接口）
    # TODO(M3): 支持从配置加载模板列表，用 cv2.matchTemplate 检测无文本图标

    return elements


def compute_fingerprint(elements: list[UIElement]) -> str:
    """计算界面的去重指纹（元素文本签名 md5）。

    指纹 = 排序后的"有文本元素"的文本拼接的 md5。
    两个界面如果包含相同的文本元素集合，指纹相同（视为同一界面）。
    忽略 icon 元素（无文本，且模板匹配结果不稳定）。

    Args:
        elements: 界面的元素列表

    Returns:
        32 字符 hex md5 指纹。空界面返回固定值（"empty"）。
    """
    texts = sorted(e.text for e in elements if e.text)
    if not texts:
        return "empty"
    signature = "|".join(texts)
    return hashlib.md5(signature.encode("utf-8")).hexdigest()  # noqa: S324


def has_screen_changed(before: ndarray, after: ndarray, threshold: float = 0.005) -> tuple[bool, float]:
    """用像素差异比例判断是否发生切屏。

    复用 coords.pixel_diff_ratio。实测阈值（来自 verify_click_menu.py）：
    - 0.001 (0.1%) = 有任何变化
    - 0.005 (0.5%) = 明确 UI 切换（菜单弹出等）

    Args:
        before, after: 切屏前后的 BGR ndarray
        threshold: 变化比例阈值，默认 0.005

    Returns:
        (是否切屏, 变化比例)
    """
    ratio = pixel_diff_ratio(before, after)
    return ratio > threshold, ratio


__all__ = ["detect_elements", "compute_fingerprint", "has_screen_changed"]
