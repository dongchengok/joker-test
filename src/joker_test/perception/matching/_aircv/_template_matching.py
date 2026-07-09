"""模板匹配（抄自 airtest 1.4.3 aircv/template_matching.py，去耦版）。

TemplateMatching：灰度 matchTemplate + 可选 RGB 三通道校验。
- find_best_result：只取最优匹配
- find_all_results：用 floodFill-style 屏蔽已命中区域，循环找多个目标

去耦点（相对 airtest 原版）：
- 去掉 @print_run_time 装饰器（用标准 logging.debug 替代）
- 去掉 airtest logger
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import cv2
import numpy as np

from ._confidence import cal_rgb_confidence
from ._utils import check_source_larger_than_search, generate_result, img_mat_rgb_2_gray

_LOGGER = logging.getLogger(__name__)


class TemplateMatching:
    """模板匹配（抄 aircv.TemplateMatching）。

    Args:
        im_search: 模板图（BGR ndarray）
        im_source: 截图（BGR ndarray）
        threshold: 筛选阈值，默认 0.8
        rgb: 是否做 RGB 三通道校验（True 能挡颜色误匹配，默认 True）
    """

    METHOD_NAME = "Template"
    MAX_RESULT_COUNT = 10

    def __init__(
        self,
        im_search: np.ndarray,
        im_source: np.ndarray,
        threshold: float = 0.8,
        rgb: bool = True,
    ) -> None:
        self.im_source = im_source
        self.im_search = im_search
        self.threshold = threshold
        self.rgb = rgb

    def find_all_results(self) -> list[dict] | None:
        """查找所有匹配区域（抄 aircv.TemplateMatching.find_all_results）。

        用 cv2.rectangle 屏蔽已命中最优区域，循环找下一个，直到低于阈值或达上限。
        解决"同一图标出现多次"（如背包里多个血瓶）的场景。

        Returns:
            结果列表 [{"result","rectangle","confidence"}]，无匹配返回 None。
        """
        check_source_larger_than_search(self.im_source, self.im_search)
        res = self._get_template_result_matrix()

        result: list[dict] = []
        h, w = self.im_search.shape[:2]

        while True:
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            confidence = self._get_confidence_from_matrix(max_loc, max_val, w, h)

            if confidence < self.threshold or len(result) > self.MAX_RESULT_COUNT:
                break

            middle_point, rectangle = self._get_target_rectangle(max_loc, w, h)
            result.append(generate_result(middle_point, rectangle, confidence))

            # 屏蔽已命中最优区域（原值刷 0），进入下轮寻找
            cv2.rectangle(
                res,
                (int(max_loc[0] - w / 2), int(max_loc[1] - h / 2)),
                (int(max_loc[0] + w / 2), int(max_loc[1] + h / 2)),
                (0, 0, 0),
                -1,
            )

        _LOGGER.debug("[%s] threshold=%s, found=%d", self.METHOD_NAME, self.threshold, len(result))
        return result if result else None

    def find_best_result(self) -> dict | None:
        """只取最优匹配（抄 aircv.TemplateMatching.find_best_result）。

        Returns:
            {"result","rectangle","confidence"} 或 None（未达阈值）。
        """
        check_source_larger_than_search(self.im_source, self.im_search)
        res = self._get_template_result_matrix()
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        h, w = self.im_search.shape[:2]

        confidence = self._get_confidence_from_matrix(max_loc, max_val, w, h)
        middle_point, rectangle = self._get_target_rectangle(max_loc, w, h)
        best_match = generate_result(middle_point, rectangle, confidence)

        _LOGGER.debug("[%s] threshold=%s, result=%s", self.METHOD_NAME, self.threshold, best_match)
        return best_match if confidence >= self.threshold else None

    def _get_confidence_from_matrix(
        self, max_loc: Sequence[int], max_val: float, w: int, h: int
    ) -> float:
        """根据结果矩阵求 confidence（抄 aircv）。

        rgb=True 时裁出候选区做 HSV 三通道校验（挡颜色误匹配）；
        rgb=False 时直接用灰度 max_val。
        """
        if self.rgb:
            img_crop = self.im_source[max_loc[1] : max_loc[1] + h, max_loc[0] : max_loc[0] + w]
            return cal_rgb_confidence(img_crop, self.im_search)
        return float(max_val)

    def _get_template_result_matrix(self) -> Any:
        """灰度 matchTemplate（抄 aircv）。

        cv2.matchTemplate 只吃灰度，BGR 先转灰度。
        """
        s_gray = img_mat_rgb_2_gray(self.im_search)
        i_gray = img_mat_rgb_2_gray(self.im_source)
        return cv2.matchTemplate(i_gray, s_gray, cv2.TM_CCOEFF_NORMED)

    def _get_target_rectangle(
        self, left_top_pos: Sequence[int], w: int, h: int
    ) -> tuple[tuple[int, int], tuple]:
        """由左上角 + 宽高算出中心点 + 四角点（抄 aircv）。"""
        x_min, y_min = left_top_pos
        x_middle, y_middle = int(x_min + w / 2), int(y_min + h / 2)
        left_bottom = (x_min, y_min + h)
        right_bottom = (x_min + w, y_min + h)
        right_top = (x_min + w, y_min)
        return (x_middle, y_middle), (left_top_pos, left_bottom, right_bottom, right_top)


__all__ = ["TemplateMatching"]
