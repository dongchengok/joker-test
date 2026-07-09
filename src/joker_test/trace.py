"""Tracer —— 开发期流程跟踪（要求4）。

记录端到端流程的所有行为，输出结构化 trace 文件，方便开发期调试：
- 程序行为：每一步的输入/输出/耗时/状态
- LLM 行为：每次调用的 prompt 摘要、回复摘要、token 估算、耗时
- Prompt 调试：完整 prompt 和回复内联进 HTML 折叠卡片，对照调整

设计要点：
- 状态自洽：Tracer 只持自己的状态（trace 目录、当前 stage），不依赖外部
- 轻量：不侵入业务代码，用 contextmanager + 装饰器模式（TracingProvider）
- 可读：人类可读的单文件 HTML（折叠卡片）+ 机器可读的 jsonl 事件流

产物结构（一次运行一个目录，时间戳在前可排序）::

    traces/2026-07-08_1530_e2e/
    ├── trace.html      人看（摘要时间线 + 可展开的完整 prompt/reply 卡片）
    ├── events.jsonl    程序读（事件流，每行一个 JSON，grep/jq 友好）
    └── summary.json    程序读（数字摘要，CI 判断用）

用法::

    tracer = Tracer(output_dir="traces", name="e2e")
    with tracer.stage("charter_gen"):
        tracer.log_llm(prompt_summary="...", reply_summary="...",
                       duration=3.2, prompt_dump="...", reply_dump="...")
    summary = tracer.finalize()  # 自动写产物 + 清理旧 trace
"""

from __future__ import annotations

import atexit
import datetime
import html
import json
import logging
import re
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

DEFAULT_KEEP = 20  # 默认保留最近 20 次 trace，超出自动清理

# trace 目录命名格式：<日期>_<时分>_<name>/，如 "2026-07-08_1530_e2e"
# 清理时只认这个格式的目录，避免误删 traces/ 下的非 trace 目录
_TRACE_DIR_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}_")


def _is_trace_dir(path: Path) -> bool:
    """判断目录是否是 trace 产物目录（按命名格式过滤，防误删非 trace 目录）。"""
    return path.is_dir() and bool(_TRACE_DIR_PATTERN.match(path.name))


class Tracer:
    """开发期流程跟踪器。

    Args:
        output_dir: trace 输出**父目录**（如 "traces"，实际产物写进它的子目录）
        name: 本次运行的名称（如 "e2e", "charter", "explore"），进子目录名
        keep: 保留最近几次 trace（默认 20，0=永不清理，开发期防累积）
        auto_timestamp: 子目录是否自动加时间戳前缀（默认 True）

    产物目录命名：<日期>_<时分>_<name>/（时间在前，ls 自动按时间排序）。

    状态自洽：所有状态（events 列表、当前 stage、计时器）都是自身属性。
    """

    def __init__(
        self,
        output_dir: str | Path,
        name: str = "run",
        *,
        keep: int = DEFAULT_KEEP,
        auto_timestamp: bool = True,
    ) -> None:
        self._name = name
        self._keep = keep
        # 子目录：<父>/<日期>_<时分>_<name>/（时间在前保证 ls 排序=时间序）
        parent = Path(output_dir)
        if auto_timestamp:
            ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
            subdir = f"{ts}_{name}"
        else:
            subdir = name
        self._dir = parent / subdir
        self._dir.mkdir(parents=True, exist_ok=True)

        self._events: list[dict[str, Any]] = []
        self._current_stage: str | None = None
        self._stage_start: float = 0.0
        self._start_time = time.monotonic()
        # LLM 完整内容内联进 HTML（不再写散文件 llm_dumps/）
        self._llm_calls: list[dict[str, Any]] = []

    @property
    def trace_dir(self) -> Path:
        """本次 trace 的产物目录（用于调用方存截图等附加文件）。"""
        return self._dir

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """标记一个阶段（如 charter_gen / explore / generate_test / run_test）。

        自动记录阶段开始/结束 + 耗时。嵌套 stage 会覆盖父 stage。

        漏用检测：如果生成器被 GC 回收（漏了 with / 没消费），GeneratorExit 触发，
        stage_end 事件带 clean_exit=False 并打 warning（让漏用从静默变可见）。
        """
        prev_stage = self._current_stage
        prev_start = self._stage_start
        self._current_stage = name
        self._stage_start = time.monotonic()
        self.log_event("stage_start", {"stage": name})
        clean_exit = True
        try:
            yield
        except GeneratorExit:
            # 不是正常 with 退出，是被 GC 回收的 → 漏了 with 或生成器未被消费
            clean_exit = False
            raise  # GeneratorExit 必须重新抛出（否则 CPython 报 RuntimeError）
        finally:
            duration = round(time.monotonic() - self._stage_start, 2)
            self.log_event("stage_end", {
                "stage": name, "duration_s": duration, "clean_exit": clean_exit,
            })
            if not clean_exit:
                _LOGGER.warning(
                    "stage '%s' 未正常退出（漏了 with？或生成器未被 with 消费）", name
                )
            self._current_stage = prev_stage
            self._stage_start = prev_start

    def log_event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """记录一个离散事件（程序行为：文件写入、界面切换、断言结果等）。"""
        self._events.append({
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "elapsed_s": round(time.monotonic() - self._start_time, 2),
            "stage": self._current_stage,
            "type": event_type,
            "data": data or {},
        })

    def log_llm(
        self,
        prompt_summary: str,
        reply_summary: str,
        duration: float,
        model: str = "",
        prompt_dump: str = "",
        reply_dump: str = "",
    ) -> None:
        """记录一次 LLM 调用。

        完整 prompt/reply 内联进 trace.html 的折叠卡片（不再产 llm_dumps/ 散文件）。
        摘要进 events.jsonl（快速浏览）。

        Args:
            prompt_summary: prompt 摘要（前 200 字，给时间线索引）
            reply_summary: 回复摘要（前 200 字，给时间线索引）
            duration: 耗时（秒）
            model: 模型名（如 mimo-v2.5）
            prompt_dump: 完整 prompt（内联进 HTML 卡片，调试用）
            reply_dump: 完整回复（内联进 HTML 卡片，调试用）
        """
        call_idx = len(self._llm_calls) + 1
        # 完整内容单独存（进 HTML 卡片，不进 jsonl 避免事件流膨胀）
        self._llm_calls.append({
            "idx": call_idx,
            "stage": self._current_stage,
            "model": model,
            "prompt_summary": prompt_summary[:200],
            "reply_summary": reply_summary[:200] if reply_summary else "(空)",
            "duration_s": round(duration, 2),
            "prompt_full": prompt_dump,
            "reply_full": reply_dump,
            "elapsed_s": round(time.monotonic() - self._start_time, 2),
        })
        # 事件流只记摘要（程序读用，不含完整 prompt/reply 避免膨胀）
        self._events.append({
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "elapsed_s": round(time.monotonic() - self._start_time, 2),
            "stage": self._current_stage,
            "type": "llm_call",
            "data": {
                "call_idx": call_idx,
                "model": model,
                "prompt_summary": prompt_summary[:200],
                "reply_summary": (reply_summary or "(空)")[:200],
                "duration_s": round(duration, 2),
                "prompt_chars": len(prompt_dump),
                "reply_chars": len(reply_dump),
            },
        })

    def log_error(self, error: str, context: dict[str, Any] | None = None) -> None:
        """记录错误/异常（带上下文）。"""
        self._events.append({
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "elapsed_s": round(time.monotonic() - self._start_time, 2),
            "stage": self._current_stage,
            "type": "error",
            "data": {"error": error, **(context or {})},
        })

    def finalize(self) -> dict[str, Any]:
        """结束跟踪，写出 trace 文件，返回摘要。

        产出（一次运行一个目录）：
        - trace.html：单文件，人看（摘要时间线 + LLM 折叠卡片内联完整 prompt/reply）
        - events.jsonl：机器读（事件流，每行一个 JSON）
        - summary.json：数字摘要

        写完后自动清理超出 keep 的旧 trace 目录。
        """
        total_duration = round(time.monotonic() - self._start_time, 2)
        llm_count = sum(1 for e in self._events if e["type"] == "llm_call")
        error_count = sum(1 for e in self._events if e["type"] == "error")

        # 1. events.jsonl（机器读）
        jsonl_path = self._dir / "events.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as f:
            for ev in self._events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")

        # 2. trace.html（人读，单文件，内联完整 prompt/reply）
        html_path = self._dir / "trace.html"
        html_path.write_text(self._render_html(total_duration, llm_count, error_count),
                             encoding="utf-8")

        # 3. summary.json
        summary = {
            "name": self._name,
            "total_duration_s": total_duration,
            "event_count": len(self._events),
            "llm_call_count": llm_count,
            "error_count": error_count,
            "trace_dir": str(self._dir),
            "trace_html": str(html_path),
            "events_jsonl": str(jsonl_path),
        }
        (self._dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 4. 自动清理旧 trace（保留最近 keep 个）
        if self._keep > 0:
            self._cleanup_old_traces()

        return summary

    def _render_html(self, total_duration: float, llm_count: int, error_count: int) -> str:
        """渲染单文件 HTML trace（摘要时间线 + LLM 折叠卡片）。"""
        # ---- 摘要区 ----
        summary_html = (
            f"<div class='summary'>总耗时 <b>{total_duration}s</b> "
            f"&nbsp;|&nbsp; 事件 <b>{len(self._events)}</b> "
            f"&nbsp;|&nbsp; <span class='llm'>LLM 调用 <b>{llm_count}</b></span>"
            f"&nbsp;|&nbsp; <span class='err'>错误 <b>{error_count}</b></span></div>"
        )

        # ---- 时间线区 ----
        timeline: list[str] = []
        for ev in self._events:
            elapsed = ev["elapsed_s"]
            stage = ev["stage"] or "-"
            etype = ev["type"]
            data = ev["data"]
            if etype == "stage_start":
                timeline.append(f"<li><code>[{elapsed:>6}s]</code> <b>▶ {html.escape(str(data['stage']))}</b></li>")
            elif etype == "stage_end":
                timeline.append(
                    f"<li><code>[{elapsed:>6}s]</code> <b>■ {html.escape(str(data['stage']))}</b> "
                    f"({data['duration_s']}s)</li>"
                )
            elif etype == "llm_call":
                ci = data.get("call_idx", "?")
                timeline.append(
                    f"<li><code>[{elapsed:>6}s]</code> 🤖 "
                    f"<a href=\"#call-{ci}\">#{ci}</a> "
                    f"<span class='stage-tag'>{html.escape(str(stage))}</span> "
                    f"{html.escape(str(data['prompt_summary'])[:60])} → "
                    f"{html.escape(str(data['reply_summary'])[:60])} "
                    f"({data['duration_s']}s)</li>"
                )
            elif etype == "error":
                timeline.append(
                    f"<li><code>[{elapsed:>6}s]</code> <span class='err'>❌ ERROR</span>: "
                    f"{html.escape(str(data.get('error', '')))}</li>"
                )
            else:
                timeline.append(
                    f"<li><code>[{elapsed:>6}s]</code> [{html.escape(str(stage))}] "
                    f"{html.escape(etype)}: {html.escape(json.dumps(data, ensure_ascii=False)[:120])}</li>"
                )

        # ---- LLM 调用详情区（折叠卡片，内联完整 prompt/reply）----
        cards: list[str] = []
        for call in self._llm_calls:
            prompt_escaped = html.escape(call["prompt_full"] or "(空)")
            reply_escaped = html.escape(call["reply_full"] or "(空)")
            cards.append(f"""
<details class="llm-card" id="call-{call['idx']}">
  <summary># {call['idx']} · <span class="stage-tag">{html.escape(str(call['stage']))}</span>
    · {html.escape(call['model'])} · {call['duration_s']}s
    <span class="hint">（点击展开完整 prompt/reply）</span></summary>
  <div class="grid">
    <div><h4>Prompt ({len(call['prompt_full'])} 字)</h4><pre>{prompt_escaped}</pre></div>
    <div><h4>Reply ({len(call['reply_full'])} 字)</h4><pre>{reply_escaped}</pre></div>
  </div>
</details>""")

        return _HTML_TEMPLATE.format(
            title=html.escape(f"Trace: {self._name}"),
            summary=summary_html,
            timeline="\n".join(timeline) or "<li>（无事件）</li>",
            cards="\n".join(cards) or "<p>（无 LLM 调用）</p>",
        )

    def _cleanup_old_traces(self) -> None:
        """保留最近 keep 个 trace 目录，删最旧的（按目录名排序=时间序）。

        只清理同目录下的兄弟目录（<日期>_<时分>_<name>/ 格式），不删文件。
        """
        parent = self._dir.parent
        # 只看 trace 目录（按命名格式过滤，防误删非 trace 目录），按名字排序=时间序
        siblings = sorted(
            [p for p in parent.iterdir() if _is_trace_dir(p) and p != self._dir],
            key=lambda p: p.name,
        )
        # 兄弟 + 自己，保留最后 keep 个
        all_runs = siblings + [self._dir]
        all_runs.sort(key=lambda p: p.name)
        for old in all_runs[: len(all_runs) - self._keep]:
            if old == self._dir:
                continue  # 不删自己
            shutil.rmtree(old, ignore_errors=True)


def clean_traces(traces_dir: str | Path, keep: int = DEFAULT_KEEP) -> int:
    """清理 trace 目录，保留最近 keep 个。返回删掉的目录数。

    供 CLI（python -m joker_test.trace clean --keep N）或手动脚本调用。
    """
    traces_dir = Path(traces_dir)
    if not traces_dir.is_dir():
        return 0
    runs = sorted(
        [p for p in traces_dir.iterdir() if _is_trace_dir(p)], key=lambda p: p.name
    )
    to_delete = runs[: len(runs) - keep] if len(runs) > keep else []
    for old in to_delete:
        shutil.rmtree(old, ignore_errors=True)
    return len(to_delete)


# ==================== 全局 tracer（仿 logging 模式）====================
# 业务代码零参数零感知：直接调 trace_event/trace_stage 等模块级函数，
# 不用 import get_tracer。首次打点惰性建 Tracer，进程退出 atexit 自动 finalize。
# set_tracer(None) → _NoOpTracer 全空操作（--no-trace 用）。

_DEFAULT_TRACE_DIR = "traces"
_DEFAULT_NAME = "run"

# 全局状态（模块级单例）
_global_tracer: Tracer | None = None  # 当前 tracer（None=未配置或已关闭）
_initialized: bool = False  # 区分"还没建"（惰性建）和"主动 set 过"（含 None 关闭）
_atexit_registered: bool = False  # atexit 只注册一次


class _NoOpTracer(Tracer):
    """空操作 tracer：set_tracer(None) 关闭时 get_tracer() 返回它。

    所有方法空操作，不建目录不写文件。业务代码拿到它正常调 log_event 等不报错。
    """

    def __init__(self) -> None:  # noqa: D401 - 跳过父类 __init__（不建目录）
        """空构造（跳过 Tracer.__init__ 的目录创建）。

        注意：trace_dir 返回 Path()（即 cwd），但 NoOp 不写盘所以无害。
        调用方不应在 NoOp 模式下用 trace_dir 拼路径写文件。
        """
        # 不调 super().__init__，避免建 traces/ 目录
        self._name = "noop"
        self._keep = 0
        self._dir = Path()  # 占位（NoOp 不写盘，trace_dir 不应被用于拼路径写文件）
        self._events = []
        self._current_stage = None
        self._stage_start = 0.0
        self._start_time = 0.0
        self._llm_calls = []

    def log_event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """空操作。"""

    def log_llm(
        self,
        prompt_summary: str,
        reply_summary: str,
        duration: float,
        model: str = "",
        prompt_dump: str = "",
        reply_dump: str = "",
    ) -> None:
        """空操作。"""

    def log_error(self, error: str, context: dict[str, Any] | None = None) -> None:
        """空操作。"""

    def finalize(self) -> dict[str, Any]:
        """空操作，返回空 dict。"""
        return {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:  # type: ignore[override]
        """空操作上下文。"""
        yield


def set_tracer(tracer: Tracer | None) -> None:
    """主动配置全局 tracer。

    Args:
        tracer: 要用的 Tracer 实例；None = 关闭（get_tracer 返回 _NoOpTracer，
            所有打点空操作不写文件，--no-trace 用）

    非 None 时注册 atexit 自动 finalize（进程退出自动写 trace.html 等）。
    """
    global _global_tracer, _initialized, _atexit_registered
    _global_tracer = tracer
    _initialized = True
    if tracer is not None and not _atexit_registered:
        atexit.register(_auto_finalize)
        _atexit_registered = True


def get_tracer() -> Tracer:
    """取全局 tracer。

    - 已配置（set_tracer 设过）→ 返回配置的（含 None 关闭时返回 _NoOpTracer）
    - 未配置且未初始化 → 惰性创建默认 Tracer（首次打点触发）+ 注册 atexit
    """
    global _global_tracer, _initialized, _atexit_registered
    if _global_tracer is not None:
        return _global_tracer
    if _initialized:
        # 主动 set_tracer(None) 关闭过
        return _NoOpTracer()
    # 首次：惰性创建默认 Tracer
    _global_tracer = Tracer(_DEFAULT_TRACE_DIR, name=_DEFAULT_NAME)
    _initialized = True
    if not _atexit_registered:
        atexit.register(_auto_finalize)
        _atexit_registered = True
    return _global_tracer


def trace_event(event_type: str, data: dict[str, Any] | None = None) -> None:
    """记录一个离散事件（程序行为：操作成功、界面切换、断言结果等）。

    全局委托给 get_tracer()，业务代码直接调，不用 import get_tracer。
    """
    get_tracer().log_event(event_type, data)


def trace_llm(
    prompt_summary: str,
    reply_summary: str,
    duration: float,
    *,
    model: str = "",
    prompt_dump: str = "",
    reply_dump: str = "",
) -> None:
    """记录一次 LLM 调用（prompt/reply 摘要 + 完整 dump 内联进 HTML）。

    全局委托。TracingProvider 调这个自动记 LLM 行为。
    """
    get_tracer().log_llm(
        prompt_summary, reply_summary, duration,
        model=model, prompt_dump=prompt_dump, reply_dump=reply_dump,
    )


def trace_error(error: str, context: dict[str, Any] | None = None) -> None:
    """记录错误/异常（带上下文）。全局委托。"""
    get_tracer().log_error(error, context)


def trace_stage(name: str) -> Any:
    """标记一个阶段（explore/generate/verify），自动记录开始/结束/耗时。

    全局委托。用法：with trace_stage("explore"): ...
    """
    return get_tracer().stage(name)


def trace_finalize() -> dict[str, Any]:
    """显式收尾（可选）：写 trace.html + events.jsonl + summary.json，返回 summary。

    atexit 也会自动调（幂等）。想拿 summary print 的调用方才显式调这个。
    """
    return get_tracer().finalize()


def _auto_finalize() -> None:
    """atexit 回调：惰性建的 tracer 进程退出时自动 finalize（幂等）。

    已 finalize 的 tracer 再调 finalize 是安全的（重新写一遍，内容不变），
    但为避免重复 I/O，这里检查 _events 为空就不写（NoOp 或已清）。
    """
    global _global_tracer
    if _global_tracer is None:
        return
    try:
        # 有事件才 finalize（避免空 trace 目录）
        if _global_tracer._events:  # noqa: SLF001
            _global_tracer.finalize()
    except Exception:  # noqa: BLE001
        pass  # atexit 不抛异常


# HTML 模板：摘要 + 时间线 + LLM 折叠卡片（单文件自包含）
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", sans-serif; margin: 2em; max-width: 1400px; }}
.summary {{ margin-bottom: 1.5em; padding: 1em 1.2em; background: #f5f5f5; border-radius: 6px; font-size: 1.05em; }}
.summary b {{ color: #333; }}
.llm {{ color: #1565c0; }} .err {{ color: #c62828; }}
h2 {{ margin-top: 1.8em; border-bottom: 2px solid #e0e0e0; padding-bottom: 0.3em; }}
ul.timeline {{ list-style: none; padding-left: 0; font-family: "Consolas", monospace; font-size: 0.92em; }}
ul.timeline li {{ padding: 3px 0; border-bottom: 1px solid #f0f0f0; }}
ul.timeline code {{ color: #888; min-width: 6em; display: inline-block; }}
.stage-tag {{ background: #e3f2fd; color: #1565c0; padding: 1px 6px; border-radius: 3px; font-size: 0.85em; }}
details.llm-card {{ border: 1px solid #e0e0e0; border-radius: 6px; margin: 0.8em 0; padding: 0; }}
details.llm-card > summary {{ padding: 0.8em 1em; cursor: pointer; font-weight: 500; background: #fafafa; border-radius: 6px; }}
details.llm-card > summary:hover {{ background: #f0f0f0; }}
details.llm-card[open] > summary {{ border-bottom: 1px solid #e0e0e0; border-radius: 6px 6px 0 0; }}
.hint {{ color: #999; font-weight: normal; font-size: 0.85em; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1em; padding: 1em; }}
.grid h4 {{ margin: 0 0 0.5em; color: #555; font-size: 0.9em; }}
.grid pre {{ background: #fafafa; border: 1px solid #eee; border-radius: 4px; padding: 0.8em;
            overflow-x: auto; font-size: 0.82em; line-height: 1.5; max-height: 600px; overflow-y: auto;
            white-space: pre-wrap; word-break: break-word; }}
@media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style></head><body>
<h1>{title}</h1>
{summary}
<h2>时间线</h2>
<ul class="timeline">
{timeline}
</ul>
<h2>LLM 调用详情</h2>
{cards}
</body></html>
"""


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="joker-test trace 清理工具")
    parser.add_argument("command", choices=["clean"], help="清理旧 trace")
    parser.add_argument("--dir", default="traces", help="trace 父目录（默认 traces）")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="保留最近 N 个")
    args = parser.parse_args()
    if args.command == "clean":
        deleted = clean_traces(args.dir, keep=args.keep)
        print(f"已清理 {deleted} 个旧 trace，保留最近 {args.keep} 个（{args.dir}）")


__all__ = [
    "DEFAULT_KEEP",
    "Tracer",
    "clean_traces",
    "get_tracer",
    "set_tracer",
    "trace_error",
    "trace_event",
    "trace_finalize",
    "trace_llm",
    "trace_stage",
]
