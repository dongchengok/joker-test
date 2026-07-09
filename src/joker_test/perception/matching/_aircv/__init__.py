"""perception.matching._aircv —— 从 airtest aircv 抄来的图像匹配算法（私有）。

下划线前缀 = 包外不直接 import（§11.3）。对外 API 是 perception.matching.ImageMatcher。

抄自 airtest 1.4.3 的 aircv 模块，做了以下去耦（让算法 backend 无关）：
- 去掉 airtest 的 logger/logwrap/G 全局状态，改用标准 logging
- 去掉 record_pos / resolution（手写坐标预测），只保留纯算法
- 保留核心能力：灰度 matchTemplate + RGB 三通道 HSV 校验 + 多尺度搜索 + 多目标
- 不抄 keypoint 匹配（SIFT/SURF/KAZE 等，游戏 UI 场景过度工程 + cv2 5.0 检测器缺失）

算法出处（airtest 1.4.3）：
- _template_matching.TemplateMatching     ← aircv/template_matching.py
- _multiscale_matching.MultiScaleTemplateMatching ← aircv/multiscale_template_matching.py
- _confidence.cal_rgb_confidence 等       ← aircv/cal_confidence.py
- _utils.generate_result 等               ← aircv/utils.py
"""
