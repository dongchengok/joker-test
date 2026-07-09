"""HtmlReporter —— 把测试结果渲染成单 HTML 文件（人类可读）。"""

from __future__ import annotations

from pathlib import Path

from joker_test.reporters.base import TestCase, TestResult, TestSession


class HtmlReporter:
    """HTML 报告器。用 Jinja2 渲染测试结果为单 HTML 文件。

    满足 TestReporter 协议（结构性子类型）。
    """

    name = "html"

    def __init__(self, output_path: str | Path) -> None:
        self._output_path = Path(output_path)
        self._session: TestSession | None = None
        self._results: list[TestResult] = []

    def on_session_start(self, session: TestSession) -> None:
        self._session = session

    def on_test_start(self, test: TestCase) -> None:
        pass

    def on_test_end(self, result: TestResult) -> None:
        self._results.append(result)

    def on_session_end(self, session: TestSession) -> None:
        if self._session is not None:
            self._session.results = self._results

    def finalize(self) -> str:
        """渲染 HTML 文件，返回路径。"""
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        html = self._render_html()
        self._output_path.write_text(html, encoding="utf-8")
        return str(self._output_path)

    def _render_html(self) -> str:
        """渲染 HTML（简单模板，不依赖外部 CSS，自包含）。"""
        session = self._session
        total = len(self._results)
        passed = sum(1 for r in self._results if r.status == "passed")
        failed = sum(1 for r in self._results if r.status == "failed")
        rows = "\n".join(self._render_row(r) for r in self._results)
        game = session.game if session else "unknown"
        return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>joker-test 报告</title>
<style>
body {{ font-family: sans-serif; margin: 2em; }}
.summary {{ margin-bottom: 1.5em; padding: 1em; background: #f5f5f5; border-radius: 4px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
th {{ background: #f9f9f9; }}
.passed {{ color: #2e7d32; font-weight: bold; }}
.failed {{ color: #c62828; font-weight: bold; }}
.skipped {{ color: #666; }}
.error {{ color: #c62828; }}
pre {{ background: #fff5f5; padding: 8px; overflow-x: auto; }}
</style></head><body>
<h1>joker-test 测试报告</h1>
<div class="summary">
<b>游戏:</b> {game} &nbsp;|&nbsp;
<b>总数:</b> {total} &nbsp;|&nbsp;
<span class="passed">通过 {passed}</span> &nbsp;|&nbsp;
<span class="failed">失败 {failed}</span>
</div>
<table><thead><tr><th>测试</th><th>状态</th><th>耗时(s)</th><th>错误</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""

    def _render_row(self, r: TestResult) -> str:
        status_class = r.status
        error_html = f"<pre>{_esc(r.error or '')}</pre>" if r.error else ""
        name = _esc(r.test.name)
        return (
            f'<tr><td>{name}</td>'
            f'<td class="{status_class}">{r.status}</td>'
            f'<td>{r.duration:.3f}</td>'
            f'<td>{error_html}</td></tr>'
        )


def _esc(text: str) -> str:
    """HTML 转义。"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


__all__ = ["HtmlReporter"]
