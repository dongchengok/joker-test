"""多尺度模板匹配（抄自 airtest 1.4.3 aircv/multiscale_template_matching.py，去耦版）。

MultiScaleTemplateMatching：从 ratio_min 到 ratio_max 按步长缩放模板，
每个尺度做一次 matchTemplate，取全局最优。解决"模板录制分辨率 ≠ 运行分辨率"。

去耦点（相对 airtest 原版 MultiScaleTemplateMatchingPre）：
- 去掉 record_pos / resolution / _get_area_scope（手写坐标预测，我们用 OCR+LLM 不需要）
- 去掉 @print_run_time 装饰器
- 去掉 airtest logger
只保留纯多尺度搜索逻辑（multi_scale_search），等同 airtest 的 MultiScaleTemplateMatching（gmstpl）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence

import cv2
import numpy as np

from ._confidence import cal_ccoeff_confidence, cal_rgb_confidence
from ._utils import check_source_larger_than_search, generate_result, img_mat_rgb_2_gray

_LOGGER = logging.getLogger(__name__)


class MultiScaleTemplateMatching:
    """多尺度模板匹配（抄 aircv.MultiScaleTemplateMatching，等同 airtest 的 gmstpl）。

    Args:
        im_search: 模板图（BGR ndarray）
        im_source: 截图（BGR ndarray）
        threshold: 筛选阈值，默认 0.8
        rgb: 是否做 RGB 校验，默认 True
        scale_max: 截屏最大尺寸限制（长边），默认 800
        scale_step: 缩放步长，默认 0.005（越小越精细越慢）
    """

    METHOD_NAME = "MSTemplate"

    def __init__(
        self,
        im_search: np.ndarray,
        im_source: np.ndarray,
        threshold: float = 0.8,
        rgb: bool = True,
        scale_max: int = 800,
        scale_step: float = 0.005,
    ) -> None:
        self.im_source = im_source
        self.im_search = im_search
        self.threshold = threshold
        self.rgb = rgb
        self.scale_max = scale_max
        self.scale_step = scale_step

    def find_best_result(self) -> dict | None:
        """多尺度搜索找最优匹配（抄 aircv.MultiScaleTemplateMatching.find_best_result）。"""
        check_source_larger_than_search(self.im_source, self.im_search)
        s_gray = img_mat_rgb_2_gray(self.im_search)
        i_gray = img_mat_rgb_2_gray(self.im_source)
        confidence, max_loc, w, h, _ = self.multi_scale_search(
            i_gray,
            s_gray,
            ratio_min=0.01,
            ratio_max=0.99,
            src_max=self.scale_max,
            step=self.scale_step,
            threshold=self.threshold,
        )

        middle_point, rectangle = self._get_target_rectangle(max_loc, w, h)
        best_match = generate_result(middle_point, rectangle, confidence)
        _LOGGER.debug("[%s] threshold=%s, result=%s", self.METHOD_NAME, self.threshold, best_match)
        return best_match if confidence >= self.threshold else None

    def _get_confidence_from_matrix(
        self, max_loc: tuple[int, int], w: int, h: int
    ) -> float:
        """多尺度命中后，把候选区缩回模板原始尺寸再做 RGB 校验（抄 aircv）。"""
        sch_h, sch_w = self.im_search.shape[0], self.im_search.shape[1]
        if self.rgb:
            img_crop = self.im_source[max_loc[1] : max_loc[1] + h, max_loc[0] : max_loc[0] + w]
            return cal_rgb_confidence(cv2.resize(img_crop, (sch_w, sch_h)), self.im_search)
        img_crop = self.im_source[max_loc[1] : max_loc[1] + h, max_loc[0] : max_loc[0] + w]
        return cal_ccoeff_confidence(cv2.resize(img_crop, (sch_w, sch_h)), self.im_search)

    def _get_target_rectangle(
        self, left_top_pos: tuple[int, int], w: int, h: int
    ) -> tuple[tuple[int, int], tuple]:
        x_min, y_min = left_top_pos
        x_middle, y_middle = int(x_min + w / 2), int(y_min + h / 2)
        left_bottom = (x_min, y_min + h)
        right_bottom = (x_min + w, y_min + h)
        right_top = (x_min + w, y_min)
        return (x_middle, y_middle), (left_top_pos, left_bottom, right_bottom, right_top)

    @staticmethod
    def _resize_by_ratio(
        src: np.ndarray,
        templ: np.ndarray,
        ratio: float = 1.0,
        templ_min: int = 10,
        src_max: int = 800,
    ) -> tuple[np.ndarray, np.ndarray, float, float]:
        """按比例缩放屏幕和模板（抄 aircv._resize_by_ratio）。"""
        sr = min(src_max / max(src.shape), 1.0)
        src = cv2.resize(src, (int(src.shape[1] * sr), int(src.shape[0] * sr)))
        h, w = src.shape[0], src.shape[1]
        th, tw = templ.shape[0], templ.shape[1]
        tr = (h * ratio) / th if th / h >= tw / w else (w * ratio) / tw
        templ = cv2.resize(templ, (max(int(tw * tr), 1), max(int(th * tr), 1)))
        return src, templ, tr, sr

    @staticmethod
    def _org_size(
        max_loc: Sequence[int], w: int, h: int, tr: float, sr: float
    ) -> tuple[tuple[int, int], int, int]:
        """还原到原始比例的框（抄 aircv._org_size）。"""
        max_loc = (int(max_loc[0] / sr), int(max_loc[1] / sr))
        w, h = int(w / sr), int(h / sr)
        return max_loc, w, h

    def multi_scale_search(
        self,
        org_src: np.ndarray,
        org_templ: np.ndarray,
        templ_min: int = 10,
        src_max: int = 800,
        ratio_min: float = 0.01,
        ratio_max: float = 0.99,
        step: float = 0.01,
        threshold: float = 0.8,
        time_out: float = 3.0,
    ) -> tuple[float, tuple[int, int], int, int, float]:
        """多尺度模板匹配核心循环（抄 aircv.multi_scale_search）。

        从 ratio_min 到 ratio_max 按 step 滑动，每个尺度 matchTemplate 一次，
        超时且已达阈值可提前返回。
        """
        mmax_val = 0.0
        max_info: tuple | None = None
        r = ratio_min
        t = time.time()
        while r <= ratio_max:
            src, templ, tr, sr = self._resize_by_ratio(
                org_src.copy(), org_templ.copy(), r, src_max=src_max
            )
            if min(templ.shape) > templ_min:
                src[0, 0] = templ[0, 0] = 0
                src[0, 1] = templ[0, 1] = 255
                result = cv2.matchTemplate(src, templ, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                h, w = templ.shape
                if mmax_val < max_val:
                    mmax_val = max_val
                    max_info = (r, max_val, max_loc, w, h, tr, sr)
                if time.time() - t > time_out and max_val >= threshold:
                    omax_loc, ow, oh = self._org_size(max_loc, w, h, tr, sr)
                    confidence = self._get_confidence_from_matrix(omax_loc, ow, oh)
                    if confidence >= threshold:
                        return confidence, omax_loc, ow, oh, r
            r += step
        if max_info is None:
            return 0.0, (0, 0), 0, 0, 0.0
        max_r, max_val, max_loc, w, h, tr, sr = max_info
        omax_loc, ow, oh = self._org_size(max_loc, w, h, tr, sr)
        confidence = self._get_confidence_from_matrix(omax_loc, ow, oh)
        return confidence, omax_loc, ow, oh, max_r


__all__ = ["MultiScaleTemplateMatching"]
