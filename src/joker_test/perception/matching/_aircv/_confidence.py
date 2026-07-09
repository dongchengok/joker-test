"""可信度计算（抄自 airtest 1.4.3 aircv/cal_confidence.py，去耦版）。

核心价值：cal_rgb_confidence 在 HSV 空间分通道校验，能挡住
"灰度相似但颜色完全不同"的误匹配（纯灰度 matchTemplate 的主要缺陷）。
"""

from __future__ import annotations

import cv2
import numpy as np

from ._utils import img_mat_rgb_2_gray


def cal_ccoeff_confidence(im_source: np.ndarray, im_search: np.ndarray) -> float:
    """两张同尺寸图的可信度，用 TM_CCOEFF_NORMED（抄 aircv.cal_ccoeff_confidence）。

    边缘扩展 + 加入取值范围干扰，防止算法放大微小差异。
    """
    im_source = cv2.copyMakeBorder(im_source, 10, 10, 10, 10, cv2.BORDER_REPLICATE)
    im_source[0, 0] = 0
    im_source[0, 1] = 255

    im_source, im_search = img_mat_rgb_2_gray(im_source), img_mat_rgb_2_gray(im_search)
    res = cv2.matchTemplate(im_source, im_search, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(res)
    return float(max_val)


def cal_rgb_confidence(img_src_rgb: np.ndarray, img_sch_rgb: np.ndarray) -> float:
    """同尺寸彩图相似度（抄 aircv.cal_rgb_confidence）。

    HSV 空间 BGR 三通道分别 matchTemplate，取 min。
    HSV 变换强化颜色差异，clip(10,245) 减少极值对角度计算的干扰。

    这是挡"灰度匹配但颜色不同"误匹配的关键 —— 例如黑白背景 vs 彩色按钮。
    """
    img_src_rgb = np.clip(img_src_rgb, 10, 245)
    img_sch_rgb = np.clip(img_sch_rgb, 10, 245)
    img_src_rgb = cv2.cvtColor(img_src_rgb, cv2.COLOR_BGR2HSV)
    img_sch_rgb = cv2.cvtColor(img_sch_rgb, cv2.COLOR_BGR2HSV)

    img_src_rgb = cv2.copyMakeBorder(img_src_rgb, 10, 10, 10, 10, cv2.BORDER_REPLICATE)
    img_src_rgb[0, 0] = 0
    img_src_rgb[0, 1] = 255

    src_bgr, sch_bgr = cv2.split(img_src_rgb), cv2.split(img_sch_rgb)
    bgr_confidence = [0.0, 0.0, 0.0]
    for i in range(3):
        res_temp = cv2.matchTemplate(src_bgr[i], sch_bgr[i], cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res_temp)
        bgr_confidence[i] = float(max_val)

    return min(bgr_confidence)


__all__ = ["cal_ccoeff_confidence", "cal_rgb_confidence"]
