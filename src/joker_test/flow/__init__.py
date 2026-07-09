"""joker_test.flow —— 操作录制 + LLM 语义化 + test_case 生成。

完整闭环：录制操作（人类手操/程序化）→ LLM 起名 rename → 坐标语义化 →
LLM 生成 pytest test_case → 现有 runner 执行。

模块组成：
- types.py：数据结构（RecordedFlow/Step/WindowInfo/SemanticResult）
- recorder.py：GlobalRecorder（统一 record_action 入口 + pynput/mss 可插拔）
- namer.py：FlowNamer（LLM 起中文名 rename 目录）
- semantics.py：坐标语义化（OCR + 图像模板双重匹配）
- generator.py：RecordedFlowGenerator（操作流+截图 → LLM → test_case）
"""

from joker_test.flow.generator import RecordedFlowGenerator
from joker_test.flow.namer import FlowNamer
from joker_test.flow.recorder import GlobalRecorder
from joker_test.flow.semantics import semanticize_step
from joker_test.flow.types import (
    FlowAction,
    FlowGenResult,
    RecordedFlow,
    RecordedStep,
    SemanticConfidence,
    SemanticResult,
    WindowInfo,
)
from joker_test.flow.verifier import TestCaseVerifier

__all__ = [
    "FlowAction",
    "FlowGenResult",
    "FlowNamer",
    "GlobalRecorder",
    "RecordedFlow",
    "RecordedFlowGenerator",
    "RecordedStep",
    "SemanticConfidence",
    "SemanticResult",
    "TestCaseVerifier",
    "WindowInfo",
    "semanticize_step",
]
