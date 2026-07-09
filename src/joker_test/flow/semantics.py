"""坐标语义化 —— 把录制的原始坐标转换成稳定的语义定位。

两路策略（按界面能否 OCR 分）：
  1. OCR 匹配：在操作截图的 OCR 结果里，找离点击坐标最近的文字 → click_text
  2. OCR 无结果（纯图标界面）→ click_coord（归一化坐标，固定窗口+分辨率下稳定）

为什么需要语义化：录制的绝对坐标（屏幕像素）换分辨率/换机器/窗口位置变就废了。
OCR 文字是最稳定的锚点（click_text 回放时重新定位）；纯图标界面 OCR 读不到文字，
退而求其次用归一化坐标（固定窗口场景下坐标稳定）。

为什么不用图像模板：纯图标界面的截图必然含动态内容（等级数字/血条/时间），
第二次跑模板匹配必然失败。模板这条路在纯图标界面两头不讨好——有文字用 click_text
就够，无文字用坐标更可靠，故砍掉模板匹配。

设计要点：
- 状态自洽：纯函数 + provider 注入，不持有状态
- 不调 LLM：语义化是确定性算法（OCR 找最近），不调 LLM
- OCR 找最近文字用 bbox 中心点到点击坐标的欧氏距离，取最近的

用法::

    result = semanticize_step(step, flow_dir, ocr_provider)
    # 有文字：result.locator_type = "click_text"，result.text = "进入地牢"
    # 无文字：result.locator_type = "click_coord"，result.x = 0.15, result.y = 0.35
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

from joker_test.flow.types import RecordedStep, SemanticResult

if TYPE_CHECKING:
    from joker_test.ocr.base import OCRProvider

logger = logging.getLogger(__name__)

# OCR 文字到点击坐标的最大归一化距离（超过则认为"不相关"，不匹配）
_MAX_OCR_DISTANCE = 0.15


def _cv_imread(path: Path) -> Any:
    """读图（兼容 Windows 中文路径，cv2.imread 不支持中文）。

    返回 ndarray（BGR）或 None。用 Any 类型避免 numpy 重依赖注解。
    """
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except (OSError, ValueError):
        return None


def semanticize_step(
    step: RecordedStep,
    flow_dir: Path,
    ocr_provider: OCRProvider | None = None,
) -> SemanticResult:
    """把单个 RecordedStep 的坐标语义化。

    策略：
      - click 类步骤：先 OCR 找最近文字（→click_text）；找不到文字用归一化坐标（→click_coord）
      - press_key/type_text：直接 none（无需语义化）
      - 已语义化的（click_text/click_image）：直接返回 high

    Args:
        step: 录制的步骤（需有 x/y 坐标 + screenshot_after）
        flow_dir: 录制产物目录（读截图用）
        ocr_provider: OCR 引擎（None 不做 OCR 匹配，纯图标界面直接 click_coord）

    Returns:
        SemanticResult（locator_type/confidence/text/x/y/warning）
    """
    if step.action not in ("click", "click_text", "click_image", "click_coord"):
        return SemanticResult(locator_type="none", confidence="none")

    # 程序化模式可能已经是 click_text（直接有文字），无需再语义化
    if step.action == "click_text" and step.text:
        return SemanticResult(
            locator_type="click_text", text=step.text, confidence="high"
        )
    # 程序化模式可能已经是 click_coord（直接有归一化坐标），直接用
    if step.action == "click_coord" and step.x is not None and step.y is not None:
        return SemanticResult(
            locator_type="click_coord", x=step.x, y=step.y, confidence="high"
        )

    # 拿到点击的归一化坐标（pynput 模式可能要从 screen 换算，但 recorder 已换算到 x/y）
    x, y = step.x, step.y
    if x is None or y is None:
        return SemanticResult(
            locator_type="none", confidence="none", warning="无归一化坐标"
        )

    # 1. OCR 匹配：找离点击坐标最近的文字（有文字 → click_text）
    screenshot_after = step.screenshot_after
    if screenshot_after:
        shot_after_path = Path(screenshot_after)
        if not shot_after_path.is_absolute():
            shot_after_path = flow_dir / shot_after_path
        ocr_text = _find_nearest_ocr_text(x, y, shot_after_path, ocr_provider)
        if ocr_text:
            return SemanticResult(
                locator_type="click_text", text=ocr_text, confidence="high"
            )

    # 2. OCR 无结果（纯图标界面）→ click_coord（归一化坐标）
    # 固定窗口+固定分辨率下坐标稳定，比含动态数字的模板可靠
    return SemanticResult(
        locator_type="click_coord", x=x, y=y, confidence="high"
    )


def _find_nearest_ocr_text(
    x: float,
    y: float,
    screenshot_path: Path,
    ocr_provider: OCRProvider | None,
) -> str | None:
    """在截图的 OCR 结果里找离 (x,y) 最近的文字。

    Args:
        x, y: 归一化点击坐标 [0,1]
        screenshot_path: 截图路径
        ocr_provider: OCR 引擎

    Returns:
        最近的文字（找不到返回 None）
    """
    if ocr_provider is None:
        return None

    try:
        frame = _cv_imread(screenshot_path)
        if frame is None:
            return None

        results = ocr_provider.readtext(frame)
        if not results:
            return None

        # OCR 返回的 bbox 是归一化 [x,y,w,h]，算中心点到点击坐标的距离
        best_text = None
        best_dist = float("inf")
        for r in results:
            # bbox 中心
            cx = r.bbox[0] + r.bbox[2] / 2
            cy = r.bbox[1] + r.bbox[3] / 2
            dist = math.sqrt((cx - x) ** 2 + (cy - y) ** 2)
            if dist < best_dist and dist < _MAX_OCR_DISTANCE:
                best_dist = dist
                best_text = r.text

        return best_text
    except Exception as e:  # noqa: BLE001
        logger.warning("OCR 查找最近文字失败: %s", e)
        return None


__all__ = ["semanticize_step"]
