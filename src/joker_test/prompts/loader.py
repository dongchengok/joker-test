"""Prompt 加载与渲染层。

把 charter_gen.py 里硬编码的 prompt 常量/模板抽到独立文件（constants/data/templates），
本模块负责加载并渲染。

文件组织（业界做法，见 roadmap 调研结论）：
- constants/*.md   —— 框架级常量（bug_definition / analyst_checklist），纯 Markdown
- data/*.yaml      —— 结构化数据（personas / heuristics）
- templates/*.md.j2 —— Jinja2 模板（architect / analyst）。双扩展名诚实表达：
                      .md 表示内容是 Markdown 风格，.j2 表示由 Jinja2 渲染
                      （含 {{ }} 占位符、{% for %} 循环）。
                      用 {{ }}（双花括号）避免与 JSON {} 冲突（LangChain 硬规则）。
                      模板内用 XML 标签包裹语义块（Anthropic 官方推荐，Claude 解析更可靠）。
- templates/charter_schema.md —— 无占位符的纯文本（给 LLM 看的字段清单）。

设计原则：本类状态自洽，持有自己的 Jinja2 Environment，不依赖 charter_gen 反向引用。
"""

from __future__ import annotations

import datetime
import json
from functools import lru_cache
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

# prompts/ 目录（本文件所在目录）
_PROMPTS_DIR = Path(__file__).resolve().parent
_CONSTANTS_DIR = _PROMPTS_DIR / "constants"
_DATA_DIR = _PROMPTS_DIR / "data"
_TEMPLATES_DIR = _PROMPTS_DIR / "templates"


@lru_cache(maxsize=1)
def _jinja_env() -> Environment:
    """Jinja2 环境（缓存，trim_blocks/lstrip_blocks 去掉块标签留的空行）。"""
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        trim_blocks=True,        # 块标签（{% %}）后的换行自动去掉
        lstrip_blocks=True,      # 块标签前的行首空白自动去掉
        undefined=StrictUndefined,  # 占位符写错立刻报错，不静默成空串
        keep_trailing_newline=False,
    )


def _read_text(path: Path) -> str:
    """读取文本文件（强制 utf-8，Windows 默认 gbk 会炸）。"""
    return path.read_text(encoding="utf-8").rstrip()  # rstrip 去掉文件末尾多余空行


# ============== 常量加载 ==============

@lru_cache(maxsize=8)
def load_constant(name: str) -> str:
    """加载 constants/<name>.md 的纯文本内容。

    Args:
        name: 常量名（不含扩展名），如 "bug_definition" / "analyst_checklist"
    """
    return _read_text(_CONSTANTS_DIR / f"{name}.md")


def load_bug_definition() -> str:
    return load_constant("bug_definition")


def load_analyst_checklist() -> list[str]:
    """加载 analyst_checklist.md，按行切成 list（保留原编号文本）。"""
    text = load_constant("analyst_checklist")
    return [line for line in text.splitlines() if line.strip()]


# ============== 数据加载 ==============

@lru_cache(maxsize=8)
def load_data(name: str) -> list:
    """加载 data/<name>.yaml 的结构化数据。

    Args:
        name: 数据名（不含扩展名），如 "personas" / "heuristics"
    """
    with open(_DATA_DIR / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_default_personas() -> list[dict]:
    return load_data("personas")


def load_default_heuristics() -> list[str]:
    return load_data("heuristics")


# ============== 模板渲染 ==============

def _render(template_name: str, **context) -> str:
    """渲染 Jinja2 模板。占位符缺失会因 StrictUndefined 立刻报错。"""
    template = _jinja_env().get_template(template_name)
    return template.render(**context).strip()


def render_architect_prompt(
    targets: list[dict],
    game_meta: dict,
    personas: list[dict],
    heuristics: list[str],
) -> str:
    """渲染 Architect prompt（生成 Charter 草稿）。

    对应原 charter_gen._build_architect_prompt。升级点：
    - Jinja2 占位符（消除 JSON {} 与 f-string 冲突隐患）
    - XML 标签包裹语义块（Anthropic 推荐）
    """
    return _render(
        "architect.md.j2",
        today=datetime.date.today().isoformat(),
        game_name=game_meta.get("game_name", "未知游戏"),
        game_overview=game_meta.get("overview", "（无游戏概述）"),
        load_save=game_meta.get("load_save", "默认新存档"),
        targets_json=json.dumps(targets, ensure_ascii=False, indent=4),
        personas=personas,
        heuristics=heuristics,
        bug_definition=load_bug_definition(),
    )


def render_analyst_prompt(game_meta: dict) -> str:
    """渲染 Analyst prompt（6 条反思 checklist）。

    对应原 charter_gen._build_analyst_prompt。
    checklist 从 analyst_checklist.md 加载，编号后注入模板。
    game_specific 可选（来自 game_metadata.json["analyst_extra_instructions"]）。
    """
    checklist = load_analyst_checklist()
    checklist_text = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(checklist))
    return _render(
        "analyst.md.j2",
        checklist_text=checklist_text,
        game_specific=game_meta.get("analyst_extra_instructions", ""),
    )


def load_charter_schema() -> str:
    """加载 Charter 的 JSON 输出 schema（纯文本，无占位符）。

    对应原 charter_gen._build_json_schema。
    """
    return _read_text(_TEMPLATES_DIR / "charter_schema.md")


def render_smoke_test_prompt(uimap_json: str, game_meta: dict) -> str:
    """渲染冒烟用例生成 prompt（M3a 新增）。

    把 UIMap JSON + 游戏元数据 + 质量标准注入模板，让 LLM 产出 pytest 代码 + Pydantic spec。

    Args:
        uimap_json: UIMap 序列化后的 JSON 字符串（M2 探索器的产出）
        game_meta: 游戏元数据 dict（含 game_name/overview 等）
    """
    systems = game_meta.get("systems") or game_meta.get("targets")
    return _render(
        "smoke_test.md.j2",
        game_name=game_meta.get("game_name", "未知游戏"),
        game_overview=game_meta.get("overview", "（无概述）"),
        game_systems=[s.get("system") or s.get("name", "") for s in systems] if systems else [],
        uimap_json=uimap_json,
        quality_rules=load_constant("smoke_quality_rules"),
    )


def render_recorded_flow_prompt(
    flow_json: str, game_meta: dict, warnings: list[str] | None = None
) -> str:
    """渲染录制操作流转 test_case 的 prompt（flow 包新增）。

    把录制操作流 JSON + 游戏元数据 + 语义化警告注入模板，让 LLM 产出 pytest 代码。
    截图通过 simple_converse 的 images 参数传，不在 prompt 文本里。

    Args:
        flow_json: RecordedFlow + 语义化结果序列化后的 JSON 字符串
        game_meta: 游戏元数据 dict
        warnings: 语义化冲突警告列表（如坐标既匹配 OCR 又匹配模板的冲突项）
    """
    systems = game_meta.get("systems") or game_meta.get("targets")
    return _render(
        "recorded_flow.md.j2",
        game_name=game_meta.get("game_name", "未知游戏"),
        game_overview=game_meta.get("overview", "（无概述）"),
        game_systems=[s.get("system") or s.get("name", "") for s in systems]
        if systems
        else [],
        flow_json=flow_json,
        warnings=warnings or [],
        quality_rules=load_constant("smoke_quality_rules"),
    )


__all__ = [
    "load_bug_definition",
    "load_analyst_checklist",
    "load_default_personas",
    "load_default_heuristics",
    "render_architect_prompt",
    "render_analyst_prompt",
    "render_smoke_test_prompt",
    "render_recorded_flow_prompt",
    "load_charter_schema",
]
