"""joker_test.prompts —— prompt 加载与渲染层。

把 charter_gen.py 里硬编码的提示词抽到独立文件，便于独立编辑、版本化、对 LLM 友好。
详见 loader.py 模块文档。
"""

from joker_test.prompts.loader import (
    load_analyst_checklist,
    load_bug_definition,
    load_charter_schema,
    load_default_heuristics,
    load_default_personas,
    render_analyst_prompt,
    render_architect_prompt,
    render_recorded_flow_prompt,
    render_smoke_test_prompt,
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
