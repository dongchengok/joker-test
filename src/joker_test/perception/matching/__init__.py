"""perception.matching —— backend 无关的图像匹配。

ImageMatcher 组合从 airtest aircv 抄来的模板匹配算法（去耦版），
不依赖任何具体 backend，任何 ExecutorBackend 的截图都能喂给它。

对外 API：ImageMatcher（匹配引擎）+ MatchResult（归一化结果）。
"""

from joker_test.perception.matching.image_matcher import ImageMatcher, MatchResult

__all__ = ["ImageMatcher", "MatchResult"]
