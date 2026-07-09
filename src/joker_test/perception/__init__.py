"""joker_test.perception —— 多层界面识别（OCR + 图像匹配 + LLM 识图）。

三层识别，漏斗互补（LLM 克制）：
- OCR（RapidOCR）：文字识别，快，但对无文字图标无效
- 图像匹配（ImageMatcher，backend 无关，抄 airtest aircv）：已知图标定位，
  毫秒级，自带 RGB 三通道 HSV 校验挡误匹配。不依赖任何具体 backend
- LLM 识图（MiMo/GLM 多模态）：语义理解，能描述任意界面，但慢且贵，仅兜底

PerceptionEngine 统一调度三层，按需选择（OCR/match 预检查都跑，LLM 兜底）。
"""

from joker_test.perception.base import PerceptionEngine, PerceptionResult
from joker_test.perception.matching import ImageMatcher, MatchResult

__all__ = ["PerceptionResult", "PerceptionEngine", "ImageMatcher", "MatchResult"]
