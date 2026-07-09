"""BedrockProvider —— AWS Bedrock 的 LLMProvider 实现（独立模式主力）。

从 charter_gen.py 解耦出来（M1 重构，2026-07-06）。原 charter_gen._BedrockAdapter +
_resolve_default_provider 挪到此处，去 `_` 前缀（公共 API，charter_gen 要 import）。

设计要点：
- **动态加载 SpecOps-src**：Bedrock 调用层（converse.py/operate.py）不在本仓库，
  按候选路径查找。找不到返回 None（不报错），由调用方决定如何处理（M0 解耦，R-ADR-3）。
- **满足 LLMProvider 协议**：结构性子类型，无需显式继承。
- resolve_default_provider() 是模块加载时的探针，charter_gen 用它赋值 DEFAULT_PROVIDER。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from joker_test.llm.base import LLMProvider, Message

logger = logging.getLogger(__name__)


# SpecOps-src 候选路径（原 charter_gen.py:49-55）
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent.parent  # llm/providers/ → 上溯 3 层到仓库根
_SPECOPS_SRC_CANDIDATES = [
    _REPO_ROOT / "SpecOps-src",                                # 仓库内同级
    _REPO_ROOT.parent / "SpecOps-src",                         # 开发时上溯
    Path(os.environ.get("SPECOPS_SRC", "")).expanduser(),      # 环境变量覆盖
]


class BedrockProvider:
    """把 SpecOps-src 的 converse/operate 适配为 LLMProvider 协议。

    独立模式的主力 provider。仅在 SpecOps-src 存在时由 resolve_default_provider 实例化。
    满足 LLMProvider 协议（结构性子类型，无需继承）。
    """

    def __init__(self, converse_mod: Any, operate_mod: Any) -> None:
        self._converse = converse_mod
        self._operate = operate_mod

    def simple_converse(
        self,
        prompt: str,
        messages: list[Message],
        *,
        reasoning: int = 0,
        images: list[str] | None = None,
    ) -> Message:
        # Bedrock converse 不支持 images 参数（SpecOps-src 的 converse.py 签名固定）
        # images 参数仅保留协议兼容，Bedrock 模式不支持多模态
        return self._converse.simple_converse(prompt, messages, reasoning=reasoning)

    def converse_json(
        self,
        messages: list[Message],
        instruction: str,
    ) -> list[dict[str, Any]]:
        return self._operate.converse_json(messages, instruction)


def resolve_default_provider() -> LLMProvider | None:
    """尝试加载 BedrockProvider（包裹 SpecOps-src 的 converse/operate）。

    SpecOps-src 不存在时返回 None（不报错）。找到但 import 失败也返回 None 并记 warning。
    返回的 provider 满足 LLMProvider 协议。

    charter_gen 模块加载时调用此函数赋值 DEFAULT_PROVIDER。
    """
    specops_src = None
    for candidate in _SPECOPS_SRC_CANDIDATES:
        if candidate and candidate.exists() and (candidate / "converse.py").exists():
            specops_src = candidate
            break

    if specops_src is None:
        return None

    if str(specops_src) not in sys.path:
        sys.path.insert(0, str(specops_src))
    try:
        import converse  # type: ignore[import-not-found]
        import operate  # type: ignore[import-not-found]
    except ImportError as e:
        logger.warning(
            "SpecOps-src 在 %s 但 import 失败: %s。"
            "请安装依赖: pip install boto3 tqdm pillow。将回退到无默认 provider。",
            specops_src, e,
        )
        return None

    return BedrockProvider(converse, operate)


__all__ = ["BedrockProvider", "resolve_default_provider"]
