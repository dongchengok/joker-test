"""全局 trace 机制的单元测试。

测：
- 惰性初始化（不 set 时首次打点自动建 Tracer）
- set_tracer(None) 关闭（NoOp 空操作不写文件）
- atexit 自动收尾（写 events.jsonl/trace.html/summary.json）
- 显式 trace_finalize 返回 summary
- TracingProvider 用全局 trace_llm 自动记录 LLM 调用
- 三个 orchestrator（LLMExplorer/generator/verifier）自动 trace

全局状态注意：每个测试用 _reset_global_tracer() 清理全局状态，避免相互污染。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import joker_test.trace as trace_mod


def _reset_global_tracer() -> None:
    """重置全局 tracer 状态（避免测试间污染）。

    注意：_atexit_registered 故意不重置——重复注册 atexit 是幂等的（CPython 去重），
    且已注册的回调在进程退出时读的是模块当前的 _global_tracer（非闭包捕获），
    所以重置它反而没必要。这里只清 _global_tracer + _initialized 让下次 get_tracer
    能重新惰性建或返回 NoOp。
    """
    trace_mod._global_tracer = None
    trace_mod._initialized = False


# ============== 惰性初始化 + NoOp ==============


def test_default_lazy_creates_tracer(tmp_path: Path, monkeypatch) -> None:
    """不 set 时首次打点惰性建真 Tracer（非 NoOp）。"""
    _reset_global_tracer()
    monkeypatch.chdir(tmp_path)
    trace_mod.trace_event("test_event", {"k": "v"})
    t = trace_mod.get_tracer()
    assert not isinstance(t, trace_mod._NoOpTracer)
    assert isinstance(t, trace_mod.Tracer)
    _reset_global_tracer()


def test_noop_when_set_none(tmp_path: Path, monkeypatch) -> None:
    """set_tracer(None) → get_tracer 返回 NoOp，打点空操作不写文件。"""
    _reset_global_tracer()
    monkeypatch.chdir(tmp_path)
    trace_mod.set_tracer(None)
    trace_mod.trace_event("should_not_record", {})
    t = trace_mod.get_tracer()
    assert isinstance(t, trace_mod._NoOpTracer)
    # NoOp 不写任何文件
    assert not list(tmp_path.glob("traces/**"))
    _reset_global_tracer()


def test_set_custom_tracer_uses_it(tmp_path: Path) -> None:
    """set_tracer(自定义) → get_tracer 返回同一个。"""
    _reset_global_tracer()
    custom = trace_mod.Tracer(tmp_path / "traces", name="custom", auto_timestamp=False)
    trace_mod.set_tracer(custom)
    assert trace_mod.get_tracer() is custom
    _reset_global_tracer()


# ============== 产物落盘 ==============


def test_cleanup_does_not_delete_non_trace_dirs(tmp_path: Path) -> None:
    """清理只删 <日期>_<时分>_<name>/ 格式的 trace 目录，不误删其他目录。"""
    _reset_global_tracer()
    traces_root = tmp_path / "traces"
    traces_root.mkdir()
    # 造 25 个 trace 目录（超过 keep=20，触发清理）
    for i in range(25):
        d = traces_root / f"2026-07-01_10{i:02d}_run"
        d.mkdir()
    # 造非 trace 目录（不应被删）
    (traces_root / "archive").mkdir()
    (traces_root / "README.d").mkdir()
    (traces_root / ".hidden").mkdir()

    from joker_test.trace import clean_traces  # noqa: PLC0415

    deleted = clean_traces(traces_root, keep=20)
    assert deleted == 5  # 只删 5 个最旧的 trace 目录
    # 非 trace 目录都还在
    assert (traces_root / "archive").exists()
    assert (traces_root / "README.d").exists()
    assert (traces_root / ".hidden").exists()
    # 剩 20 个 trace 目录
    remaining = [p for p in traces_root.iterdir() if p.name.startswith("2026")]
    assert len(remaining) == 20
    _reset_global_tracer()


def test_stage_clean_exit_true_with_with(tmp_path: Path) -> None:
    """正常 with 退出 → stage_end 事件 clean_exit=True。"""
    import json  # noqa: PLC0415

    _reset_global_tracer()
    t = trace_mod.Tracer(tmp_path / "traces", name="se", auto_timestamp=False)
    trace_mod.set_tracer(t)
    with trace_mod.trace_stage("explore"):
        pass
    trace_mod.trace_finalize()
    lines = (tmp_path / "traces" / "se" / "events.jsonl").read_text(encoding="utf-8").strip().split("\n")
    end_events = [json.loads(ln) for ln in lines
                  if json.loads(ln)["type"] == "stage_end"]
    assert len(end_events) == 1
    assert end_events[0]["data"]["clean_exit"] is True
    _reset_global_tracer()


def test_stage_dirty_exit_when_not_consumed(tmp_path: Path) -> None:
    """漏 with（next 了没消费）→ 被 GC 时 stage_end clean_exit=False + warning。

    模拟：手动 next() 推进到 yield（执行 setup），然后丢弃生成器对象触发 GC。
    """
    import gc  # noqa: PLC0415
    import json  # noqa: PLC0415
    import logging  # noqa: PLC0415

    _reset_global_tracer()
    t = trace_mod.Tracer(tmp_path / "traces", name="dirty", auto_timestamp=False)
    trace_mod.set_tracer(t)

    # 捕获 warning 日志
    handler_records: list[logging.LogRecord] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            handler_records.append(record)

    cap = _CaptureHandler(level=logging.WARNING)
    trace_mod._LOGGER.addHandler(cap)  # noqa: SLF001
    try:
        cm = trace_mod.trace_stage("explore")
        cm.__enter__()    # 进 with（setup 执行，stage_start 打了）
        del cm            # 丢弃 → GC 触发 close() → GeneratorExit → finally
        gc.collect()      # 强制 GC（确保 __del__/close 执行）
    finally:
        trace_mod._LOGGER.removeHandler(cap)  # noqa: SLF001

    trace_mod.trace_finalize()
    lines = (tmp_path / "traces" / "dirty" / "events.jsonl").read_text(encoding="utf-8").strip().split("\n")
    end_events = [json.loads(ln) for ln in lines
                  if json.loads(ln)["type"] == "stage_end"]
    assert len(end_events) == 1
    assert end_events[0]["data"]["clean_exit"] is False
    # 有 warning 日志
    assert any("explore" in r.getMessage() and "未正常退出" in r.getMessage()
               for r in handler_records)
    _reset_global_tracer()


def test_explicit_finalize_writes_files(tmp_path: Path) -> None:
    """显式 trace_finalize() 写 events.jsonl/trace.html/summary.json。"""
    _reset_global_tracer()
    t = trace_mod.Tracer(tmp_path / "traces", name="t", auto_timestamp=False)
    trace_mod.set_tracer(t)
    trace_mod.trace_event("e1", {"a": 1})
    summary = trace_mod.trace_finalize()
    assert summary["trace_html"]
    assert summary["events_jsonl"]
    # 三个产物文件都在
    assert (tmp_path / "traces" / "t" / "events.jsonl").exists()
    assert (tmp_path / "traces" / "t" / "trace.html").exists()
    assert (tmp_path / "traces" / "t" / "summary.json").exists()
    _reset_global_tracer()


def test_finalize_summary_has_fields(tmp_path: Path) -> None:
    """summary 含 trace_html/events_jsonl/llm_call_count 等字段。"""
    _reset_global_tracer()
    t = trace_mod.Tracer(tmp_path / "traces", name="s", auto_timestamp=False)
    trace_mod.set_tracer(t)
    trace_mod.trace_event("e1", {})
    trace_mod.trace_llm("p", "r", 1.2)
    summary = trace_mod.trace_finalize()
    assert "trace_html" in summary
    assert "events_jsonl" in summary
    assert summary["llm_call_count"] == 1
    assert summary["event_count"] >= 2
    _reset_global_tracer()


def test_events_jsonl_contains_typed_events(tmp_path: Path) -> None:
    """events.jsonl 含打点的 event（按 type 可 grep）。"""
    import json  # noqa: PLC0415

    _reset_global_tracer()
    t = trace_mod.Tracer(tmp_path / "traces", name="e", auto_timestamp=False)
    trace_mod.set_tracer(t)
    trace_mod.trace_event("explore_step", {"step": 3, "screen": "角色选择"})
    trace_mod.trace_event("explore_end", {"reason": "stale_limit"})
    trace_mod.trace_finalize()
    lines = (tmp_path / "traces" / "e" / "events.jsonl").read_text(encoding="utf-8").strip().split("\n")
    types = {json.loads(line)["type"] for line in lines}
    assert "explore_step" in types
    assert "explore_end" in types
    _reset_global_tracer()


# ============== TracingProvider 用全局 ==============


def test_tracing_provider_uses_global(tmp_path: Path) -> None:
    """TracingProvider(real, model) 不传 tracer，调 simple_converse 后 events.jsonl 含 llm_call。"""
    import json  # noqa: PLC0415

    _reset_global_tracer()
    t = trace_mod.Tracer(tmp_path / "traces", name="tp", auto_timestamp=False)
    trace_mod.set_tracer(t)

    from joker_test.llm.providers.tracing import TracingProvider  # noqa: PLC0415

    inner = MagicMock()
    inner.simple_converse.return_value = {"content": [{"type": "text", "text": "回复"}]}
    wrapped = TracingProvider(inner, model="test-model")
    wrapped.simple_converse("hello", [], reasoning=0)

    trace_mod.trace_finalize()
    lines = (tmp_path / "traces" / "tp" / "events.jsonl").read_text(encoding="utf-8").strip().split("\n")
    llm_events = [json.loads(ln) for ln in lines if json.loads(ln)["type"] == "llm_call"]
    assert len(llm_events) == 1
    assert llm_events[0]["data"]["model"] == "test-model"
    _reset_global_tracer()


def test_tracing_provider_no_trace_when_disabled() -> None:
    """set_tracer(None) 时 TracingProvider 调用不报错不写文件。"""
    _reset_global_tracer()
    trace_mod.set_tracer(None)

    from joker_test.llm.providers.tracing import TracingProvider  # noqa: PLC0415

    inner = MagicMock()
    inner.simple_converse.return_value = {"content": [{"type": "text", "text": "x"}]}
    wrapped = TracingProvider(inner, model="m")
    wrapped.simple_converse("p", [])  # 不报错
    _reset_global_tracer()


# ============== orchestrator 自动 trace ==============


def test_explorer_auto_traces(tmp_path: Path) -> None:
    """LLMExplorer.explore 后 events.jsonl 含 explore_step/action_result/explore_end。"""
    import json  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    from joker_test.executor.backends.fake import FakeBackend, ScreenCfg  # noqa: PLC0415
    from joker_test.executor.base import BBox  # noqa: PLC0415
    from joker_test.explorer.llm_explorer import LLMExplorer  # noqa: PLC0415

    _reset_global_tracer()
    t = trace_mod.Tracer(tmp_path / "traces", name="ex", auto_timestamp=False)
    trace_mod.set_tracer(t)

    # FakeBackend：主菜单 → 角色（点"进入"切屏）
    backend = FakeBackend(
        screens={
            "main": ScreenCfg(texts_map={"进入": BBox(0.5, 0.5, 0.2, 0.1)}, bg_pixel=(10, 20, 30)),
            "char": ScreenCfg(texts_map={"返回": BBox(0.5, 0.5, 0.2, 0.1)}, bg_pixel=(40, 60, 80)),
        },
        transitions={("main", "进入"): "char", ("char", "返回"): "main"},
        initial_screen="main",
    )
    # mock LLM：第一次理解界面，决策点"进入"；第二次理解新界面，决策 stop
    llm = MagicMock()
    llm.simple_converse.side_effect = [
        {"content": [{"type": "text", "text": '{"screen_name": "主菜单", "elements": [{"text": "进入", "type": "button", "bbox": [0.4,0.4,0.2,0.2]}]}'}]},
        {"content": [{"type": "text", "text": '{"action": "click_text", "target": "进入", "description": "点进入"}'}]},
        {"content": [{"type": "text", "text": '{"screen_name": "角色", "elements": [{"text": "返回", "type": "button", "bbox": [0.4,0.4,0.2,0.2]}]}'}]},
        {"content": [{"type": "text", "text": '{"action": "stop", "target": "", "description": "完成"}'}]},
    ]
    # FakeBackend.screenshot 返回一个固定帧（pixel_diff_ratio 检测用）
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    backend.screenshot = MagicMock(return_value=frame)

    explorer = LLMExplorer(backend=backend, llm=llm, max_steps=3, max_stale_steps=3)
    explorer.explore()

    trace_mod.trace_finalize()
    lines = (tmp_path / "traces" / "ex" / "events.jsonl").read_text(encoding="utf-8").strip().split("\n")
    types = {json.loads(ln)["type"] for ln in lines}
    assert "explore_step" in types
    assert "explore_end" in types
    _reset_global_tracer()


def test_generator_auto_traces(tmp_path: Path) -> None:
    """RecordedFlowGenerator.generate 后 events.jsonl 含 semanticized/code_parsed。"""
    import json  # noqa: PLC0415

    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    from joker_test.flow.generator import RecordedFlowGenerator  # noqa: PLC0415
    from joker_test.flow.types import RecordedFlow, RecordedStep  # noqa: PLC0415

    _reset_global_tracer()
    t = trace_mod.Tracer(tmp_path / "traces", name="gen", auto_timestamp=False)
    trace_mod.set_tracer(t)

    shot = tmp_path / "shot.png"
    cv2.imwrite(str(shot), np.zeros((100, 100, 3), dtype=np.uint8))

    mock_provider = MagicMock()
    mock_provider.simple_converse.return_value = {
        "content": [{"type": "text", "text": (
            "### test_x.py\n```python\nfrom joker_test.executor.base import ExecutorBackend\n\n"
            "def test_x(backend: ExecutorBackend) -> None:\n    assert True\n```\n"
            "### x_spec.py\n```python\nfrom pydantic import BaseModel\nclass XSpec(BaseModel):\n    pass\n```\n"
        )}]
    }
    flow = RecordedFlow(
        name="t",
        steps=[RecordedStep(action="click_text", text="进入", screenshot_after="shot.png")],
    )
    gen = RecordedFlowGenerator(provider=mock_provider, ocr_provider=None)
    gen.generate(flow, tmp_path, {"game_name": "测试", "overview": ""})

    trace_mod.trace_finalize()
    lines = (tmp_path / "traces" / "gen" / "events.jsonl").read_text(encoding="utf-8").strip().split("\n")
    types = {json.loads(ln)["type"] for ln in lines}
    assert "semanticized" in types
    assert "code_parsed" in types
    _reset_global_tracer()


def test_verifier_auto_traces(tmp_path: Path) -> None:
    """TestCaseVerifier.verify_and_fix 后 events.jsonl 含 verify_round。"""
    import json  # noqa: PLC0415

    from joker_test.flow.verifier import TestCaseVerifier  # noqa: PLC0415
    from joker_test.generator.types import GeneratedTest  # noqa: PLC0415
    from joker_test.reporters.base import TestCase, TestResult, TestSession  # noqa: PLC0415

    _reset_global_tracer()
    t = trace_mod.Tracer(tmp_path / "traces", name="ver", auto_timestamp=False)
    trace_mod.set_tracer(t)

    # mock session（全过）
    passing_session = TestSession(
        id="s1", started_at="t", game="g", backend="fake",
        tests=[TestCase(name="test_a", module="m")],
        results=[TestResult(test=TestCase(name="test_a", module="m"), status="passed")],
    )
    gen = tmp_path / "gen"
    gen.mkdir()

    class _FakeVerifier(TestCaseVerifier):
        def _run_tests(self, tests):  # noqa: ANN001, ANN202
            return passing_session

    # 写个 test 文件（write_tests_to_dir 需要）
    test = GeneratedTest(
        system="a", test_code="def test_a(): assert True",
        spec_code="pass", test_filename="test_a.py", spec_filename="a_spec.py",
    )
    v = _FakeVerifier(reset_fn=lambda: None, gen_dir=gen, max_retries=1)
    v.verify_and_fix([test], MagicMock(), MagicMock(), tmp_path, {})

    trace_mod.trace_finalize()
    lines = (tmp_path / "traces" / "ver" / "events.jsonl").read_text(encoding="utf-8").strip().split("\n")
    types = {json.loads(ln)["type"] for ln in lines}
    assert "verify_round" in types
    assert "verify_pass" in types
    _reset_global_tracer()
