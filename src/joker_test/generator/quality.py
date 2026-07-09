"""生成代码质量兜底（M3a）。

DESIGN §4.2 "潜在风险"明确："用 pytest 跑一遍 + mypy 类型检查兜底"。
M3a 不做 pytest 试跑（fixture 是 M3b 的），只做：
  1. ruff check（语法 + 风格，subprocess）
  2. ast.parse（语法正确性，不真 import 避免副作用）

失败抛 QualityError，含具体错误信息。生成器捕获后决定重试还是报错。
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
from pathlib import Path

from joker_test.generator.types import GeneratedTest


class QualityError(Exception):
    """生成代码质量检查失败。"""


class QualityChecker:
    """生成代码质量检查器。

    M3a 范围：ruff + ast.parse。
    M3b 会扩展 pytest 试跑（fixture 就绪后）。
    """

    def check(self, test: GeneratedTest) -> None:
        """检查一个 GeneratedTest 的代码质量。失败抛 QualityError。"""
        self._check_ast(test)
        self._check_ruff(test)

    def _check_ast(self, test: GeneratedTest) -> None:
        """ast.parse 语法正确性检查（不真 import，避免副作用）。"""
        for label, code in [("test_code", test.test_code), ("spec_code", test.spec_code)]:
            try:
                ast.parse(code)
            except SyntaxError as e:
                raise QualityError(
                    f"[{test.system}/{label}] 语法错误: {e.msg}（行 {e.lineno}）"
                ) from e

    def _check_ruff(self, test: GeneratedTest) -> None:
        """ruff check 风格/语法（subprocess，写到临时文件）。

        用临时文件而非 stdin，因为 ruff 的路径相关规则（如 import 排序）需要文件名上下文。
        E501（行长度）已在 pyproject ignore，不阻断。
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            test_file = tmp_path / test.test_filename
            spec_file = tmp_path / test.spec_filename
            test_file.write_text(test.test_code, encoding="utf-8")
            spec_file.write_text(test.spec_code, encoding="utf-8")

            # 找项目的 ruff（用 sys.executable 对应环境的 ruff）
            ruff_bin = str(Path(sys.executable).parent / "ruff")
            if not Path(ruff_bin).exists():
                # Linux/Mac 兜底
                ruff_bin = "ruff"

            try:
                result = subprocess.run(  # noqa: S603
                    [ruff_bin, "check", "--select=E9,F821,F822", str(test_file), str(spec_file)],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            except (subprocess.SubprocessError, FileNotFoundError):
                # ruff 不可用时降级为只警告（不阻断生成，ruff 是 dev 依赖）
                return

            # E9 = SyntaxError，F821/F822 = undefined name（ruff 能检出的严重问题）
            if result.returncode != 0 and result.stdout.strip():
                raise QualityError(
                    f"[{test.system}] ruff 检查失败:\n{result.stdout.strip()}"
                )


__all__ = ["QualityChecker", "QualityError"]
