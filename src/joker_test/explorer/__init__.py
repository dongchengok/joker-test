"""joker_test.explorer —— 界面探索器（M2）。

自动遍历游戏界面，产出结构化状态地图（StateMap）。
是编排架构里"探查类命令"的核心（D10）。
"""

from joker_test.explorer.detection import compute_fingerprint, detect_elements, has_screen_changed
from joker_test.explorer.explorer import UIExplorer
from joker_test.explorer.types import Exit, Screen, StateMap, UIElement

__all__ = [
    "UIExplorer",
    "StateMap",
    "Screen",
    "UIElement",
    "Exit",
    "detect_elements",
    "compute_fingerprint",
    "has_screen_changed",
]
