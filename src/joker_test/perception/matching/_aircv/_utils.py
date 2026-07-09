"""aircv 工具函数（抄自 airtest 1.4.3 aircv/utils.py，去耦版）。

只保留图像匹配必需的工具：结果格式化 + 输入校验 + 灰度转换。
去掉了 PIL 转换、图片压缩、旋转、标记点等无关功能。
"""

from __future__ import annotations

import cv2
import numpy as np

from ._errors import TemplateInputError


def generate_result(middle_point: tuple[int, int], pypts: tuple, confi: float) -> dict:
    """格式化图像识别结果（抄 aircv.utils.generate_result）。

    Returns:
        {"result": (x,y) 中心点, "rectangle": 四角点, "confidence": 置信度}
    """
    return {"result": middle_point, "rectangle": pypts, "confidence": confi}


def check_image_valid(im_source: np.ndarray, im_search: np.ndarray) -> bool:
    """检查输入图像是否有效（抄 aircv.utils.check_image_valid）。"""
    return bool(
        im_source is not None
        and im_source.any()
        and im_search is not None
        and im_search.any()
    )


def check_source_larger_than_search(im_source: np.ndarray, im_search: np.ndarray) -> None:
    """校验截图宽高 ≥ 模板宽高（抄 aircv.utils.check_source_larger_than_search）。

    Raises:
        TemplateInputError: 模板比截图还大时。
    """
    h_search, w_search = im_search.shape[:2]
    h_source, w_source = im_source.shape[:2]
    if h_search > h_source or w_search > w_source:
        raise TemplateInputError("template match: im_search bigger than im_source.")


def img_mat_rgb_2_gray(img_mat: np.ndarray) -> np.ndarray:
    """BGR → 灰度（抄 aircv.utils.img_mat_rgb_2_gray）。

    cv2.matchTemplate 只处理灰度图，这是统一预处理。
    """
    return cv2.cvtColor(img_mat, cv2.COLOR_BGR2GRAY)


__all__ = [
    "generate_result",
    "check_image_valid",
    "check_source_larger_than_search",
    "img_mat_rgb_2_gray",
]
