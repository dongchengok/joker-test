"""ImageMatcher —— backend 无关的图像匹配引擎。

组合 _aircv 抄来的算法（TemplateMatching + MultiScaleTemplateMatching），
对外提供简洁的 match_best / match_all API，输出归一化坐标（与 BBox 契约对齐）。

设计要点：
- 状态自洽：threshold / rgb / strategy / scale_max / scale_step 都是自身状态，
  不依赖任何 backend（这是从 airtest 抄一份而非直接 import 的原因 ——
  perception 层不能耦合具体 backend）
- 算法级联（仿 airtest CVSTRATEGY）：strategy=("mstpl","tpl") 时先试多尺度，
  失败降级到纯模板。每个算法自带 RGB 三通道 HSV 校验，挡颜色误匹配
- 输出归一化：bbox=(x,y,w,h) ∈ [0,1]，基准=frame 尺寸，与 ExecutorBackend.click 契约一致

用法::

    matcher = ImageMatcher(threshold=0.8, rgb=True)
    hits = matcher.match_all(frame, {"enter_btn": "enter.png", "close": "close.png"})
    # hits = [MatchResult(name="enter_btn", bbox=(0.5,0.6,0.1,0.05), confidence=0.92)]

    best = matcher.match_best(frame, {"enter_btn": "enter.png"})
    # best = MatchResult(...) 或 None
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel

from ._aircv._errors import BaseError, TemplateInputError
from ._aircv._multiscale_matching import MultiScaleTemplateMatching
from ._aircv._template_matching import TemplateMatching

_LOGGER = logging.getLogger(__name__)

# 算法名 → 类（仿 airtest MATCHING_METHODS，只保留 mstpl/tpl 两档）
_MATCHING_METHODS = {
    "mstpl": MultiScaleTemplateMatching,
    "tpl": TemplateMatching,
}


class MatchResult(BaseModel):
    """单次模板匹配的归一化结果。"""

    name: str  # 模板名（templates dict 的 key）
    bbox: tuple[float, float, float, float]  # 归一化 (x,y,w,h) ∈ [0,1]，基准=frame 尺寸
    confidence: float  # 置信度 [0,1]

    __test__ = False  # pytest 不要把这个当测试类收集


class ImageMatcher:
    """backend 无关的图像匹配引擎（抄 airtest aircv 算法，去耦版）。

    Args:
        threshold: 匹配阈值，默认 0.8。越高越严格
        rgb: 是否做 RGB 三通道 HSV 校验（True 挡颜色误匹配），默认 True
        strategy: 算法级联顺序，默认 ("mstpl","tpl")。前者失败降级后者
        scale_max: 多尺度搜索时截屏长边上限，默认 800
        scale_step: 多尺度缩放步长，默认 0.005（越小越精细越慢）

    状态自洽：所有配置都是自己的属性，不依赖任何 backend 或全局状态。
    """

    def __init__(
        self,
        threshold: float = 0.8,
        rgb: bool = True,
        strategy: tuple[str, ...] = ("mstpl", "tpl"),
        scale_max: int = 800,
        scale_step: float = 0.005,
    ) -> None:
        self.threshold = threshold
        self.rgb = rgb
        self.strategy = strategy
        self.scale_max = scale_max
        self.scale_step = scale_step

    def match_best(
        self,
        frame: np.ndarray,
        templates: dict[str, str | Path | np.ndarray],
    ) -> MatchResult | None:
        """对每个模板找最佳匹配，返回所有模板里置信度最高的那一个。

        Args:
            frame: 截图（BGR ndarray）
            templates: {模板名: 模板来源}，来源可以是文件路径或 BGR ndarray

        Returns:
            置信度最高的 MatchResult，或 None（全部未达阈值）。
        """
        all_hits = self.match_all(frame, templates)
        if not all_hits:
            return None
        return max(all_hits, key=lambda r: r.confidence)

    def match_all(
        self,
        frame: np.ndarray,
        templates: dict[str, str | Path | np.ndarray],
    ) -> list[MatchResult]:
        """对每个模板找最佳匹配，返回所有命中结果（每个模板至多一条）。

        Args:
            frame: 截图（BGR ndarray）
            templates: {模板名: 模板来源}，来源可以是文件路径或 BGR ndarray

        Returns:
            命中的 MatchResult 列表（未达阈值的模板不出现）。
        """
        if frame is None or frame.size == 0:
            return []

        results: list[MatchResult] = []
        for name, src in templates.items():
            tpl = self._load_template(src)
            if tpl is None:
                _LOGGER.warning("模板加载失败，跳过: %s", name)
                continue
            hit = self._match_one(frame, tpl)
            if hit is not None:
                results.append(
                    MatchResult(
                        name=name,
                        bbox=self._normalize_bbox(hit, frame, tpl),
                        confidence=hit["confidence"],
                    )
                )
        return results

    def _match_one(self, frame: np.ndarray, tpl: np.ndarray) -> dict | None:
        """对单个模板跑算法级联（仿 airtest _cv_match）。

        strategy 里逐个试，命中即返回。每个算法的异常被隔离（降级到下一个）。
        """
        for method_name in self.strategy:
            cls = _MATCHING_METHODS.get(method_name)
            if cls is None:
                _LOGGER.warning("未知匹配算法，跳过: %s", method_name)
                continue
            try:
                if method_name == "mstpl":
                    matcher = cls(
                        tpl,
                        frame,
                        threshold=self.threshold,
                        rgb=self.rgb,
                        scale_max=self.scale_max,
                        scale_step=self.scale_step,
                    )
                else:  # tpl
                    matcher = cls(tpl, frame, threshold=self.threshold, rgb=self.rgb)
            except (TemplateInputError, BaseError) as e:
                _LOGGER.debug("[%s] 输入异常，降级: %r", method_name, e)
                continue

            try:
                ret = matcher.find_best_result()
            except (TemplateInputError, BaseError) as e:
                _LOGGER.debug("[%s] 匹配异常，降级: %r", method_name, e)
                continue

            if ret:
                _LOGGER.debug("[%s] 命中: %s", method_name, ret)
                return ret
        return None

    @staticmethod
    def _load_template(src: str | Path | np.ndarray) -> np.ndarray | None:
        """加载模板：ndarray 直接用，路径走 cv2.imread。失败返回 None。"""
        if isinstance(src, np.ndarray):
            return src
        tpl = cv2.imread(str(src))
        if tpl is None:
            _LOGGER.warning("cv2.imread 失败: %s", src)
        return tpl

    @staticmethod
    def _normalize_bbox(
        hit: dict, frame: np.ndarray, tpl: np.ndarray
    ) -> tuple[float, float, float, float]:
        """把像素坐标结果转成归一化 bbox (x,y,w,h) ∈ [0,1]，基准=frame 尺寸。

        hit["rectangle"] = (左上, 左下, 右下, 右上) 像素坐标。
        归一化与 ExecutorBackend.click 契约一致（G6：基准=screenshot 图像尺寸）。
        """
        fh, fw = frame.shape[:2]
        left_top, _, right_bottom, _ = hit["rectangle"]
        x_min, y_min = left_top
        x_max, y_max = right_bottom
        return (x_min / fw, y_min / fh, (x_max - x_min) / fw, (y_max - y_min) / fh)


__all__ = ["ImageMatcher", "MatchResult"]
