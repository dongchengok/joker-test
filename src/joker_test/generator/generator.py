"""SmokeTestGenerator —— 冒烟用例生成器（M3a 核心）。

消费 M2 的 UIMap，用 LLM 生成 pytest 冒烟用例（Python 代码 + Pydantic spec）。
产出 GeneratedTest 列表，M3b 执行层落盘并跑这些测试。

流程（复刻 charter_gen 的成熟三段式）：
  1. 渲染 prompt（Jinja2，复用 prompts/loader）
  2. provider.simple_converse 发给 LLM
  3. 从回复文本解析代码块（### filename + ```python）
  4. 质量兜底（QualityChecker: ruff + ast.parse）
  5. 返回 GeneratedTest 列表

设计要点：
- 纯函数 + provider 依赖注入（§9.7 落地影响第 2 条）
- 状态自洽：SmokeTestGenerator 持有 provider 和 quality_checker，不依赖外部状态
- 不含 I/O（不落盘）：generate 只返回数据，落盘由调用方（M3b）做
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from joker_test.generator.quality import QualityChecker, QualityError
from joker_test.generator.types import GeneratedTest
from joker_test.prompts import render_smoke_test_prompt

if TYPE_CHECKING:
    from joker_test.explorer.types import UIMap
    from joker_test.llm.base import LLMProvider, Message

logger = logging.getLogger(__name__)

# 解析 LLM 输出的代码块：### filename.py\n```python\ncode\n```
# 用 [^\n]+ 非贪婪匹配文件名行，``` 包围代码块
_CODE_BLOCK_PATTERN = re.compile(
    r"###\s*(?P<filename>[^\n]+\.py)\s*\n+```(?:python)?\s*\n(?P<code>.*?)```",
    re.DOTALL,
)


class SmokeTestGenerator:
    """冒烟用例生成器。

    Args:
        provider: LLM provider（独立模式 Bedrock，测试 MockProvider）
        quality_checker: 质量检查器（默认 QualityChecker，可注入 mock 测试）

    用法::
        gen = SmokeTestGenerator(provider=MockProvider())
        tests = gen.generate(uimap, game_meta)
        for t in tests:
            print(t.test_filename, len(t.test_code))
    """

    def __init__(
        self,
        provider: LLMProvider,
        quality_checker: QualityChecker | None = None,
    ) -> None:
        self._provider = provider
        self._quality = quality_checker or QualityChecker()

    def generate(self, uimap: UIMap, game_meta: dict) -> list[GeneratedTest]:
        """从 UIMap 生成冒烟用例。

        Args:
            uimap: M2 探索器产出的界面地图
            game_meta: 游戏元数据（含 game_name/overview/systems）

        Returns:
            GeneratedTest 列表（每个被测系统一份）

        Raises:
            QualityError: 生成的代码未通过质量检查
        """
        # 1. 渲染 prompt
        prompt = render_smoke_test_prompt(uimap.model_dump_json(), game_meta)
        logger.info("渲染用例生成 prompt（%d 字符）", len(prompt))

        # 2. 调 LLM
        msg = self._provider.simple_converse(prompt, [], reasoning=16000)
        reply_text = _extract_text(msg)
        if not reply_text:
            raise QualityError("LLM 回复为空，无法解析代码")

        # 3. 解析代码块
        raw_blocks = _parse_code_blocks(reply_text)
        if not raw_blocks:
            raise QualityError(
                f"LLM 回复中未找到代码块（### filename + ```python）。回复前 200 字: {reply_text[:200]}"
            )
        logger.info("解析到 %d 个代码块", len(raw_blocks))

        # 4. 配对 test + spec，构造 GeneratedTest
        tests = _pair_into_generated_tests(raw_blocks)

        # 5. 质量兜底
        for t in tests:
            self._quality.check(t)
            logger.info("质量检查通过: %s / %s", t.test_filename, t.spec_filename)

        return tests


def write_tests_to_dir(tests: list[GeneratedTest], output_dir: str | Path) -> list[Path]:
    """把生成的测试代码落盘（M3a→M3b 衔接）。

    每个 GeneratedTest 写两个文件：test_<system>.py + <system>_spec.py。
    返回写出的 test 文件路径列表（供 runner 执行）。

    Args:
        tests: SmokeTestGenerator.generate() 的返回值
        output_dir: 输出目录（如 "tests/smoke"）

    Returns:
        test 文件路径列表（不含 spec）
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    test_paths: list[Path] = []
    for t in tests:
        (out / t.spec_filename).write_text(t.spec_code, encoding="utf-8")
        test_path = out / t.test_filename
        test_path.write_text(t.test_code, encoding="utf-8")
        test_paths.append(test_path)
        logger.info("已落盘: %s / %s", t.test_filename, t.spec_filename)
    return test_paths


def _extract_text(msg: Message) -> str:
    """从 Message 提取纯文本（拼所有 text 块，忽略 reasoningContent）。"""
    parts: list[str] = []
    for block in msg.get("content", []):
        if "text" in block:
            parts.append(block["text"])
    return "\n".join(parts)


def _parse_code_blocks(text: str) -> list[tuple[str, str]]:
    """解析 LLM 回复里的代码块。

    格式：### filename.py\n```python\ncode\n```

    Returns:
        [(filename, code), ...] 按出现顺序
    """
    blocks: list[tuple[str, str]] = []
    for m in _CODE_BLOCK_PATTERN.finditer(text):
        filename = m.group("filename").strip()
        code = m.group("code").strip()
        blocks.append((filename, code))
    return blocks


def _pair_into_generated_tests(blocks: list[tuple[str, str]]) -> list[GeneratedTest]:
    """把代码块配对成 GeneratedTest（test_*.py + *_spec.py 一组）。

    约定：test 文件名以 test_ 开头，spec 文件名以 _spec.py 结尾。
    配对规则：test_<system>.py 配 <system>_spec.py（去 test_ 前缀和 _spec 后缀对齐 system）。
    若无配对 spec，spec_code 留空字符串（生成器允许只有 test 无 spec）。
    """
    test_blocks: dict[str, str] = {}  # system → test_code
    spec_blocks: dict[str, str] = {}  # system → spec_code

    for filename, code in blocks:
        if filename.startswith("test_") and filename.endswith(".py"):
            # test_inventory.py → system = inventory
            system = filename[len("test_"):-len(".py")]
            test_blocks[system] = code
        elif filename.endswith("_spec.py"):
            # inventory_spec.py → system = inventory
            system = filename[:-len("_spec.py")]
            spec_blocks[system] = code
        else:
            logger.warning("无法识别文件类型: %s，跳过", filename)

    # 配对
    tests: list[GeneratedTest] = []
    for system, test_code in test_blocks.items():
        spec_code = spec_blocks.get(system, "")
        tests.append(
            GeneratedTest(
                system=system,
                test_code=test_code,
                spec_code=spec_code,
                test_filename=f"test_{system}.py",
                spec_filename=f"{system}_spec.py",
            )
        )
    # spec 有但 test 没有的情况（罕见），目前忽略
    return tests


__all__ = ["SmokeTestGenerator"]
