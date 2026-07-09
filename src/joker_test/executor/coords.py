"""Backend 公共工具（跨实现共享，无 `_` 前缀，DESIGN §11.3）。

复用 .test-targets/verify_*.py 的实测经验：
- analyze_screenshot：G7 截图健康度检测（来源 verify_bg_screenshot.py 的 analyze()）
- pixel_diff_ratio：像素差异比例（来源 verify_click_numpy.py）

注意：这里的图像类型用 numpy.ndarray（BGR，与 cv2/screenshot 约定一致）。
numpy 是主依赖（pyproject.toml 已声明），不是可选 extras。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from numpy import ndarray


def analyze_screenshot(img: ndarray) -> str:
    """检测截图健康度（G7：判断窗口是否可被正常截取）。

    复用 verify_bg_screenshot.py 的 analyze() 实测逻辑。

    Returns:
        "全黑（截图失败）" / "全白（截图失败）" / "疑似单色/异常（...）" / "真实画面（...）"
    """
    # 延迟导入 numpy（只在调用时需要，模块导入不依赖）
    import numpy as np  # noqa: PLC0415

    if img.mean() < 5:
        return "全黑（截图失败）"
    if img.mean() > 250:
        return "全白（截图失败）"
    std = img.std()
    unique_colors = len(np.unique(img.reshape(-1, 3), axis=0))
    if std < 5 or unique_colors < 100:
        return f"疑似单色/异常（std={std:.1f}, colors={unique_colors}）"
    return f"真实画面（mean={img.mean():.0f}, std={std:.1f}, colors={unique_colors}）"


def pixel_diff_ratio(
    a: ndarray, b: ndarray, threshold: int = 25
) -> float:
    """两张同尺寸图像的像素差异比例（来源 verify_click_numpy.py）。

    Args:
        a, b: BGR ndarray，尺寸必须相同
        threshold: 单通道灰度差超过此值算"变化"（默认 25，anti-aliasing 以下）

    Returns:
        变化像素占比 [0,1]。实测参考门限：
        - 0.1% (0.001) = 有任何变化
        - 0.5% (0.005) = 明确 UI 切换（如菜单弹出）
        - verify_click_menu 实测菜单点击 = 21.48%
    """
    import numpy as np  # noqa: PLC0415

    if a.shape != b.shape:
        raise ValueError(f"图像尺寸不一致: {a.shape} vs {b.shape}")
    diff = np.abs(a.astype(int) - b.astype(int))
    changed = int(np.sum(np.any(diff > threshold, axis=2)))
    total = int(a.shape[0] * a.shape[1])
    return changed / total


__all__ = ["analyze_screenshot", "pixel_diff_ratio"]
