"""Tracer —— 开发期流程跟踪（要求4）。

记录端到端流程的所有行为，输出结构化 trace 文件，方便开发期调试：
- 程序行为：每一步的输入/输出/耗时/状态
- LLM 行为：每次调用的 prompt 摘要、回复摘要、token 估算、耗时
- Prompt 调试：完整 prompt 和回复内联进 HTML 折叠卡片，对照调整

设计要点：
- 状态自洽：Tracer 只持自己的状态（trace 目录、当前 stage），不依赖外部
- 轻量：不侵入业务代码，用 contextmanager + 装饰器模式（provider 内置 trace）
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
import signal
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

        # 即时落盘：事件/LLM dump 写入即 flush，进程崩溃也不丢
        # （旧设计攒内存等 finalize 一次性写，崩溃 = 空目录）
        self._jsonl_path = self._dir / "events.jsonl"
        self._llm_jsonl_path = self._dir / "llm_calls.jsonl"
        self._jsonl_file: Any = self._jsonl_path.open("a", encoding="utf-8")
        self._llm_jsonl_file: Any = None  # 懒打开（首次 log_llm 才建）

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
        """记录一个离散事件（程序行为：文件写入、界面切换、断言结果等）。

        事件即时 append 到 events.jsonl 并 flush，进程崩溃也不丢已发生事件。
        （旧设计攒内存等 finalize 一次性写，崩溃 = 空目录）
        """
        ev = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "elapsed_s": round(time.monotonic() - self._start_time, 2),
            "stage": self._current_stage,
            "type": event_type,
            "data": data or {},
        }
        self._events.append(ev)
        # 即时落盘（崩溃保命）
        try:
            self._jsonl_file.write(json.dumps(ev, ensure_ascii=False) + "\n")
            self._jsonl_file.flush()
        except Exception:  # noqa: BLE001
            _LOGGER.warning("trace 事件即时落盘失败（type=%s）", event_type, exc_info=True)

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

        - 摘要事件即时落盘到 events.jsonl（和 log_event 一样）
        - 完整 prompt/reply 即时落盘到 llm_calls.jsonl（崩溃不丢）
        - 完整内容也内联进 trace.html 的折叠卡片（finalize 渲染）

        Args:
            prompt_summary: prompt 摘要（前 200 字，给时间线索引）
            reply_summary: 回复摘要（前 200 字，给时间线索引）
            duration: 耗时（秒）
            model: 模型名（如 mimo-v2.5）
            prompt_dump: 完整 prompt（即时落盘 + 内联进 HTML 卡片）
            reply_dump: 完整回复（即时落盘 + 内联进 HTML 卡片）
        """
        call_idx = len(self._llm_calls) + 1
        call = {
            "idx": call_idx,
            "stage": self._current_stage,
            "model": model,
            "prompt_summary": prompt_summary[:200],
            "reply_summary": reply_summary[:200] if reply_summary else "(空)",
            "duration_s": round(duration, 2),
            "prompt_full": prompt_dump,
            "reply_full": reply_dump,
            "elapsed_s": round(time.monotonic() - self._start_time, 2),
        }
        # 完整内容存内存（渲染 HTML 用）+ 即时落盘到 llm_calls.jsonl（崩溃保命）
        self._llm_calls.append(call)
        try:
            if self._llm_jsonl_file is None:
                self._llm_jsonl_file = self._llm_jsonl_path.open("a", encoding="utf-8")
            self._llm_jsonl_file.write(json.dumps(call, ensure_ascii=False) + "\n")
            self._llm_jsonl_file.flush()
        except Exception:  # noqa: BLE001
            _LOGGER.warning("trace LLM dump 即时落盘失败（call_idx=%d）", call_idx, exc_info=True)

        # 摘要进事件流（即时落盘，复用 log_event 的 flush）
        self.log_event("llm_call", {
            "call_idx": call_idx,
            "model": model,
            "prompt_summary": prompt_summary[:200],
            "reply_summary": (reply_summary or "(空)")[:200],
            "duration_s": round(duration, 2),
            "prompt_chars": len(prompt_dump),
            "reply_chars": len(reply_dump),
        })

    def log_error(self, error: str, context: dict[str, Any] | None = None) -> None:
        """记录错误/异常（带上下文）。即时落盘（复用 log_event）。"""
        self.log_event("error", {"error": error, **(context or {})})

    def finalize(self) -> dict[str, Any]:
        """结束跟踪，写出 trace 文件，返回摘要。

        产出（一次运行一个目录）：
        - events.jsonl：已由 log_event 即时落盘，finalize 只 flush 尾部 + 关闭句柄
        - llm_calls.jsonl：已由 log_llm 即时落盘，finalize 只关闭句柄
        - trace.html：从内存 _events + _llm_calls 渲染（finalize 独有，崩溃则无）
        - summary.json：数字摘要（finalize 独有，崩溃则无）

        写完后自动清理超出 keep 的旧 trace 目录。

        崩溃保证：即使 finalize 没跑到（进程崩溃），events.jsonl 和 llm_calls.jsonl
        已经在每条事件发生时 flush 到磁盘，不会丢。
        """
        total_duration = round(time.monotonic() - self._start_time, 2)
        llm_count = sum(1 for e in self._events if e["type"] == "llm_call")
        error_count = sum(1 for e in self._events if e["type"] == "error")

        # 1. events.jsonl / llm_calls.jsonl：已即时落盘，只 flush + 关闭句柄
        self._close_jsonl_files()

        # 2. trace.html（人读，单文件，内联完整 prompt/reply）
        html_path = self._dir / "trace.html"
        try:
            html_path.write_text(
                self._render_html(total_duration, llm_count, error_count),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            # HTML 渲染失败不影响 jsonl（已落盘），只警告
            _LOGGER.warning("trace.html 渲染失败（事件流已保全）", exc_info=True)

        # 3. summary.json
        summary = {
            "name": self._name,
            "total_duration_s": total_duration,
            "event_count": len(self._events),
            "llm_call_count": llm_count,
            "error_count": error_count,
            "trace_dir": str(self._dir),
            "trace_html": str(html_path),
            "events_jsonl": str(self._jsonl_path),
        }
        (self._dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 4. 自动清理旧 trace（保留最近 keep 个）
        if self._keep > 0:
            self._cleanup_old_traces()

        return summary

    def _close_jsonl_files(self) -> None:
        """flush + 关闭 jsonl 文件句柄（finalize 和崩溃恢复时调）。"""
        for f in (self._jsonl_file, self._llm_jsonl_file):
            if f is not None and not f.closed:
                try:
                    f.flush()
                    f.close()
                except Exception:  # noqa: BLE001
                    _LOGGER.warning("trace jsonl 句柄关闭失败", exc_info=True)

    # 事件类型 → 图标
    _ICONS = {
        "stage_start": "▶",
        "stage_end": "■",
        "llm_call": "🤖",
        "error": "❌",
        "perceive": "🔍",
        "explore_think": "🧠",
        "dispatch_click": "👆",
        "dispatch_swipe": "↔️",
        "dispatch": "⚙️",
        "plugin_inject": "🔌",
        "plugin_validate": "🔍",
        "action_result": "⚡",
        "repeat_warning": "🔁",
    }

    @staticmethod
    def _status_light(etype: str, data: dict[str, Any]) -> str:
        """根据事件类型和内容返回状态灯。

        🟢 成功/有效  🔴 失败/错误  🟡 不确定/中性  ⚪ 无状态（纯信息）
        """
        if etype == "dispatch_click":
            return "🟢" if data.get("success") else "🔴"
        if etype == "dispatch_swipe":
            return "🟢"
        if etype == "dispatch":
            return "🟢"
        if etype == "action_result":
            ok = data.get("success", False)
            changed = data.get("screen_changed", False)
            if not ok:
                return "🔴"
            return "🟢" if changed else "🟡"
        if etype == "error":
            return "🔴"
        if etype == "llm_call":
            return "🟢"
        if etype == "repeat_warning":
            return "🟡"
        if etype in ("plugin_inject", "plugin_validate"):
            return "🟢"
        # perceive / explore_think / stage_* 是中性信息
        return "⚪"

    def _render_html(self, total_duration: float, llm_count: int, error_count: int) -> str:
        """渲染单文件 HTML trace（统一时间线，每个事件都是可折叠卡片）。"""
        # ---- 摘要区 ----
        summary_html = (
            f"<div class='summary'>总耗时 <b>{total_duration}s</b> "
            f"&nbsp;|&nbsp; 事件 <b>{len(self._events)}</b> "
            f"&nbsp;|&nbsp; <span class='llm'>LLM 调用 <b>{llm_count}</b></span>"
            f"&nbsp;|&nbsp; <span class='err'>错误 <b>{error_count}</b></span></div>"
        )

        # ---- 统一时间线：每个事件一张可折叠卡片 ----
        llm_lookup = {c["idx"]: c for c in self._llm_calls}
        cards: list[str] = []
        for ev in self._events:
            cards.append(self._render_event_card(ev, llm_lookup))

        return _HTML_TEMPLATE.format(
            title=html.escape(f"Trace: {self._name}"),
            summary=summary_html,
            timeline="\n".join(cards) or "<p>（无事件）</p>",
        )

    def _render_event_card(self, ev: dict[str, Any], llm_lookup: dict[int, dict]) -> str:
        """渲染单个事件为可折叠卡片。summary=缩略，展开=完整细节。"""
        elapsed = ev["elapsed_s"]
        stage = ev["stage"] or ""
        etype = ev["type"]
        data = ev["data"]
        icon = self._ICONS.get(etype, "•")

        # ---- 状态灯：🟢成功 🔴失败 🟡不确定 ----
        light = self._status_light(etype, data)

        # ---- summary 行：时间 + 状态灯 + 图标 + 核心缩略信息 ----
        summary_parts = [f"<code>[{elapsed:>6}s]</code>", light, icon]
        if stage:
            summary_parts.append(f"<span class='stage-tag'>{html.escape(stage)}</span>")

        if etype == "stage_start":
            summary_parts.append(f"<b>{html.escape(str(data.get('stage', '')))}</b>")
        elif etype == "stage_end":
            summary_parts.append(
                f"<b>{html.escape(str(data.get('stage', '')))}</b> "
                f"({data.get('duration_s', '?')}s)"
            )
        elif etype == "llm_call":
            ci = data.get("call_idx", "?")
            summary_parts.append(
                f"<b>#{ci}</b> {html.escape(str(data.get('model', '')))} "
                f"<span class='hint'>{data.get('duration_s', '?')}s</span> · "
                f"{html.escape(str(data.get('prompt_summary', ''))[:50])} → "
                f"{html.escape(str(data.get('reply_summary', ''))[:50])}"
            )
        elif etype == "error":
            summary_parts.append(
                f"<span class='err'>{html.escape(str(data.get('error', ''))[:120])}</span>"
            )
        elif etype == "perceive":
            size = data.get("screenshot_size", [])
            size_str = f"{size[0]}×{size[1]}" if len(size) >= 2 else "?"
            screenshot = data.get("screenshot_path", "")
            screenshot_note = f" 截图={screenshot}" if screenshot else ""
            summary_parts.append(
                f"step={data.get('step', '?')} 截图={size_str}{screenshot_note}"
            )
        elif etype == "explore_think":
            x, y = data.get("x"), data.get("y")
            coords = ""
            if x is not None and y is not None:
                coords = f"({x:.2f},{y:.2f})"
            elif x is not None:
                coords = f"(x={x:.2f})"
            summary_parts.append(
                f"step={data.get('step', '?')} <b>{html.escape(str(data.get('action', '')))}</b> "
                f"target={html.escape(str(data.get('target', '') or '(无)'))[:20]} {coords}"
            )
        elif etype == "plugin_inject":
            content_preview = html.escape(str(data.get("content", ""))[:60])
            summary_parts.append(
                f"{html.escape(str(data.get('plugin', '')))} "
                f"注入 {html.escape(str(data.get('inject_point', '')))} "
                f"({data.get('length', 0)}字): {content_preview}"
            )
        elif etype == "plugin_validate":
            summary_parts.append(
                f"校验反馈: {html.escape(str(data.get('feedback', ''))[:100])}"
            )
        elif etype == "dispatch_click":
            summary_parts.append(html.escape(str(data.get("path", ""))))
        elif etype == "dispatch_swipe":
            frm = data.get("from", [])
            to = data.get("to", [])
            summary_parts.append(
                f"swipe {html.escape(str(data.get('direction', '')))} "
                f"({frm[0]:.2f},{frm[1]:.2f})→({to[0]:.2f},{to[1]:.2f})"
            )
        elif etype == "repeat_warning":
            summary_parts.append(
                f"连续重复 {data.get('count', '?')} 次: "
                f"{html.escape(str(data.get('action_key', '')))} ⚠ {html.escape(str(data.get('hint', '')))}"
            )
        elif etype == "action_result":
            ok = data.get("success", False)
            changed = data.get("screen_changed", False)
            diff = data.get("pixel_diff_ratio", 0)
            if ok and changed:
                summary_parts.append(f"step={data.get('step', '?')} 界面变化 diff={round(diff, 4)}")
            elif ok:
                summary_parts.append(f"step={data.get('step', '?')} 无变化 diff={round(diff, 4)}")
            else:
                summary_parts.append(
                    f"step={data.get('step', '?')} 失败 {html.escape(str(data.get('error', '')))[:60]}"
                )
        else:
            summary_parts.append(
                f"{html.escape(etype)}: {html.escape(json.dumps(data, ensure_ascii=False)[:100])}"
            )

        summary_html_str = " ".join(summary_parts)

        # ---- 展开内容：完整 detail ----
        detail_html = self._render_event_detail(etype, data, llm_lookup)

        return f"""
<details class="ev-card ev-{html.escape(etype)}">
  <summary>{summary_html_str}</summary>
  <div class="ev-detail">{detail_html}</div>
</details>"""

    def _render_event_detail(
        self, etype: str, data: dict[str, Any], llm_lookup: dict[int, dict]
    ) -> str:
        """渲染事件展开后的完整细节。"""
        # LLM 调用：展开显示完整 prompt/reply（左右并排）
        if etype == "llm_call":
            ci = data.get("call_idx", 0)
            call = llm_lookup.get(ci, {})
            prompt_full = call.get("prompt_full", "")
            reply_full = call.get("reply_full", "")
            return f"""
<div class="grid">
  <div><h4>Prompt ({len(prompt_full)} 字)</h4><pre>{html.escape(prompt_full or "(空)")}</pre></div>
  <div><h4>Reply ({len(reply_full)} 字)</h4><pre>{html.escape(reply_full or "(空)")}</pre></div>
</div>"""
        # perceive 事件：展开显示截图（有路径时）
        if etype == "perceive":
            screenshot_path = data.get("screenshot_path", "")
            if screenshot_path:
                # 相对路径：trace 目录在 traces/<dir>/，截图在 flows/<dir>/screenshots/
                # 用相对路径让 HTML 可直接打开
                return f'<img src="file:///{html.escape(screenshot_path)}" style="max-width:100%;border:1px solid #eee;border-radius:4px;">'
            return "<pre>（无截图）</pre>"
        # plugin_inject 事件：展开显示注入的具体内容
        if etype == "plugin_inject":
            content = data.get("content", "")
            if content:
                return f"<pre>{html.escape(content)}</pre>"
            return "<pre>（无注入内容）</pre>"
        # 其他事件：完整 JSON dump
        pretty = json.dumps(data, ensure_ascii=False, indent=2)
        return f"<pre>{html.escape(pretty)}</pre>"

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


def rebuild_html(trace_dir: str | Path) -> Path | None:
    """从 events.jsonl + llm_calls.jsonl 重建 trace.html（崩溃恢复用）。

    trace.html 是从 _events + _llm_calls 渲染的 derived view，进程崩溃时可能
    没渲染出来。但 events.jsonl 和 llm_calls.jsonl 是即时落盘的（每条事件发生
    时就 flush），包含了渲染 HTML 所需的全部数据。本函数从这两个文件重建 HTML。

    供两种场景调用：
    1. Tracer.__init__ 检测到上次崩溃（有 jsonl 无 html）时自动补渲染
    2. CLI `python -m joker_test.trace rebuild <dir>` 手动重建

    Args:
        trace_dir: trace 产物目录（含 events.jsonl）

    Returns:
        生成的 html 路径；目录无 events.jsonl 时返回 None
    """
    trace_dir = Path(trace_dir)
    jsonl_path = trace_dir / "events.jsonl"
    if not jsonl_path.exists():
        return None

    # 加载事件流
    events: list[dict[str, Any]] = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))

    # 加载 LLM 完整 dump（崩溃时可能没生成，容错）
    llm_calls: list[dict[str, Any]] = []
    llm_jsonl = trace_dir / "llm_calls.jsonl"
    if llm_jsonl.exists():
        for line in llm_jsonl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                llm_calls.append(json.loads(line))

    if not events:
        return None

    # 构造临时 Tracer 渲染（复用 _render_html，不建新目录不开新句柄）
    name = _TRACE_DIR_PATTERN.sub("", trace_dir.name) or trace_dir.name
    t = Tracer.__new__(Tracer)
    t._name = name
    t._events = events
    t._llm_calls = llm_calls
    t._start_time = 0.0  # elapsed_s 已在每条事件里，不依赖 _start_time

    total_duration = events[-1]["elapsed_s"] if events else 0.0
    llm_count = sum(1 for e in events if e["type"] == "llm_call")
    error_count = sum(1 for e in events if e["type"] == "error")

    html_path = trace_dir / "trace.html"
    html_path.write_text(
        t._render_html(total_duration, llm_count, error_count),  # noqa: SLF001
        encoding="utf-8",
    )

    # 同步补写 summary.json（崩溃时也没生成）
    summary = {
        "name": name,
        "total_duration_s": total_duration,
        "event_count": len(events),
        "llm_call_count": llm_count,
        "error_count": error_count,
        "trace_dir": str(trace_dir),
        "trace_html": str(html_path),
        "events_jsonl": str(jsonl_path),
        "rebuilt": True,  # 标记：这是崩溃后重建的，非正常运行产出
    }
    (trace_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return html_path


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
_signal_handlers_registered: bool = False  # 信号处理器只注册一次
_finalize_done: bool = False  # finalize 幂等：防止 signal handler 和 atexit 重复


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
        self._jsonl_file = None  # 不打开文件（NoOp 不写盘）
        self._llm_jsonl_file = None

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

    def _close_jsonl_files(self) -> None:
        """空操作（NoOp 没有文件句柄）。"""

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

    非 None 时注册 atexit + 信号处理器，保证正常退出/Ctrl+C/kill 都能渲染 HTML。
    """
    global _global_tracer, _initialized, _atexit_registered
    _global_tracer = tracer
    _initialized = True
    if tracer is not None:
        if not _atexit_registered:
            atexit.register(_auto_finalize)
            _atexit_registered = True
        _setup_signal_handlers()


def get_tracer() -> Tracer:
    """取全局 tracer。

    - 已配置（set_tracer 设过）→ 返回配置的（含 None 关闭时返回 _NoOpTracer）
    - 未配置且未初始化 → 惰性创建默认 Tracer（首次打点触发）+ 注册 atexit + 信号处理器
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
    _setup_signal_handlers()
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

    全局委托。provider 内置 trace 调这个自动记 LLM 行为。
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


# ==================== 信号处理器（Ctrl+C / kill 时也渲染 HTML）====================
# 覆盖场景：SIGINT（Ctrl+C）、SIGTERM（kill PID）、SIGBREAK（Windows Ctrl+Break）
# 不覆盖：SIGKILL（kill -9，内核直接杀，无法捕获）、断电
# 兜底：kill -9 / 断电由即时落盘的 jsonl + rebuild_html 兜底


# 各信号对应的默认退出行为
_SIGNAL_EXIT: dict[int, type[KeyboardInterrupt]] = {
    signal.SIGINT: KeyboardInterrupt,
}
# SIGTERM / SIGBREAK 默认行为是 terminate（非异常），需要特殊处理
if hasattr(signal, "SIGBREAK"):
    _SIGNAL_EXIT[signal.SIGBREAK] = KeyboardInterrupt  # Windows Ctrl+Break


def _signal_handler(signum: int, frame: Any) -> None:
    """信号处理器：收到 Ctrl+C/kill 时，先 finalize 渲染 HTML，再按原行为退出。

    Python 的 signal handler 在主线程下一次字节码执行时被调用（非异步中断），
    所以在 handler 里做文件 I/O（写 HTML）是安全的。

    幂等：_auto_finalize 内部通过 _finalize_done 保证只 finalize 一次。
    """
    # 先渲染 HTML（_auto_finalize 内部设 _finalize_done 防重复）
    try:
        _auto_finalize()
    except Exception:  # noqa: BLE001
        pass  # _auto_finalize 内部已有 stderr 记录
    # 恢复默认信号处理器，重新抛出信号让进程按原行为退出
    _restore_default_signal(signum)
    # SIGINT → KeyboardInterrupt（Python 约定）；SIGTERM/SIGBREAK → SystemExit
    exc_cls = _SIGNAL_EXIT.get(signum)
    if exc_cls is not None:
        raise exc_cls(1)
    raise SystemExit(128 + signum)


def _restore_default_signal(signum: int) -> None:
    """恢复信号的默认处理器。"""
    try:
        signal.signal(signum, signal.SIG_DFL)
    except (OSError, ValueError):
        pass  # 某些信号在某些平台不可改（如非主线程）


def _setup_signal_handlers() -> None:
    """注册信号处理器（只注册一次）。

    注册 SIGINT（Ctrl+C）、SIGTERM（kill PID），Windows 额外注册 SIGBREAK。
    """
    global _signal_handlers_registered
    if _signal_handlers_registered:
        return
    _signal_handlers_registered = True
    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)  # type: ignore[attr-defined]
    for sig in signals:
        try:
            signal.signal(sig, _signal_handler)
        except (OSError, ValueError):
            pass  # 非主线程或信号不可用时跳过


def _auto_finalize() -> None:
    """atexit 回调：进程退出时自动 finalize（渲染 HTML + summary + 关闭句柄）。

    events.jsonl / llm_calls.jsonl 已由 log_event / log_llm 即时落盘，
    即使这里抛异常，事件流也不会丢。这里只负责 HTML/summary 渲染 + 句柄关闭。

    幂等：已被 signal handler finalize 过就跳过（_finalize_done）。
    异常记到 stderr（不再静默吞），让崩溃原因可见。
    """
    global _global_tracer, _finalize_done
    if _global_tracer is None or _finalize_done:
        return
    _finalize_done = True
    try:
        # 有事件才 finalize（避免空 trace 目录）
        if _global_tracer._events:  # noqa: SLF001
            _global_tracer.finalize()
    except Exception as e:  # noqa: BLE001
        # atexit 里不能抛异常（会打到 stderr 干扰退出），但记下来让崩溃可见
        import sys  # noqa: PLC0415

        print(f"[trace] atexit finalize 失败（事件流已落盘保全）: {e}", file=sys.stderr)
        # 兜底：确保 jsonl 句柄关闭
        try:
            _global_tracer._close_jsonl_files()  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass


# HTML 模板：摘要 + 统一时间线（每个事件可折叠卡片，内联完整详情）
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", sans-serif; margin: 2em; max-width: 1400px; }}
.summary {{ margin-bottom: 1.5em; padding: 1em 1.2em; background: #f5f5f5; border-radius: 6px; font-size: 1.05em; }}
.summary b {{ color: #333; }}
.llm {{ color: #1565c0; }} .err {{ color: #c62828; }} .ok {{ color: #2e7d32; }}
h2 {{ margin-top: 1.8em; border-bottom: 2px solid #e0e0e0; padding-bottom: 0.3em; }}
.timeline {{ font-family: "Consolas", monospace; font-size: 0.92em; }}
details.ev-card {{ border: 1px solid #e8e8e8; border-radius: 4px; margin: 3px 0; padding: 0; }}
details.ev-card > summary {{ padding: 5px 10px; cursor: pointer; font-weight: 500; background: #fafafa; border-radius: 4px; line-height: 1.6; }}
details.ev-card > summary:hover {{ background: #f0f0f0; }}
details.ev-card[open] > summary {{ border-bottom: 1px solid #e0e0e0; border-radius: 4px 4px 0 0; }}
details.ev-llm_call > summary {{ background: #f6fbff; }}
details.ev-error > summary {{ background: #fff5f5; }}
details.ev-stage_start > summary, details.ev-stage_end > summary {{ background: #f5fff5; }}
code {{ color: #888; min-width: 6em; display: inline-block; }}
.stage-tag {{ background: #e3f2fd; color: #1565c0; padding: 1px 6px; border-radius: 3px; font-size: 0.85em; }}
.hint {{ color: #999; font-weight: normal; font-size: 0.85em; }}
.ev-detail {{ padding: 0; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1em; padding: 1em; }}
.grid h4 {{ margin: 0 0 0.5em; color: #555; font-size: 0.9em; }}
.grid pre, .ev-detail > pre {{ background: #fafafa; border: 1px solid #eee; border-radius: 4px; padding: 0.8em;
            overflow-x: auto; font-size: 0.82em; line-height: 1.5; max-height: 600px; overflow-y: auto;
            white-space: pre-wrap; word-break: break-word; margin: 0; }}
.ev-detail > pre {{ margin: 0; border: none; border-top: 1px solid #eee; border-radius: 0 0 4px 4px; }}
@media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style></head><body>
<h1>{title}</h1>
{summary}
<h2>时间线</h2>
<div class="timeline">
{timeline}
</div>
</body></html>
"""


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="joker-test trace 工具")
    parser.add_argument("command", choices=["clean", "rebuild"], help="clean=清理旧 trace / rebuild=从 jsonl 重建 HTML")
    parser.add_argument("--dir", default="traces", help="trace 父目录（默认 traces）")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="保留最近 N 个")
    parser.add_argument("trace_dir", nargs="?", help="rebuild: 指定 trace 目录路径")
    args = parser.parse_args()
    if args.command == "clean":
        deleted = clean_traces(args.dir, keep=args.keep)
        print(f"已清理 {deleted} 个旧 trace，保留最近 {args.keep} 个（{args.dir}）")
    elif args.command == "rebuild":
        if args.trace_dir:
            # 重建指定目录
            html_path = rebuild_html(args.trace_dir)
            if html_path:
                print(f"✓ 已重建 {html_path}")
            else:
                print(f"✗ {args.trace_dir} 无 events.jsonl，无法重建")
        else:
            # 扫描所有目录，重建有 jsonl 但无 html 的僵尸目录
            traces_dir = Path(args.dir)
            recovered = 0
            for d in sorted(traces_dir.iterdir()):
                if _is_trace_dir(d):
                    has_jsonl = (d / "events.jsonl").exists()
                    has_html = (d / "trace.html").exists()
                    if has_jsonl and not has_html:
                        html_path = rebuild_html(d)
                        if html_path:
                            print(f"✓ 崩溃恢复: {d.name} → {html_path}")
                            recovered += 1
            if recovered:
                print(f"共恢复 {recovered} 个崩溃的 trace")
            else:
                print("无需恢复（所有 trace 目录都有 HTML）")


__all__ = [
    "DEFAULT_KEEP",
    "Tracer",
    "clean_traces",
    "get_tracer",
    "rebuild_html",
    "set_tracer",
    "trace_error",
    "trace_event",
    "trace_finalize",
    "trace_llm",
    "trace_stage",
]
