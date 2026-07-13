"""
游戏测试 Charter 生成器（Charter-Driven Exploratory Testing）

基于 SpecOps test_spec_gen.py 改造：
- 从 feature-driven 改为 charter-driven（探索式，不是功能验证）
- 加入 Persona（破坏狂/贪婪者/急躁鬼等玩家人格）
- 加入 Heuristics（QA 经验启发式库）
- 加入 Coverage 维度（区域/功能/操作/状态）

每个 (target × persona) 组合生成一个独立 Charter JSON，
供 Phase 2 执行层（待实现）调度。

用法：
    python -m joker_test.charter_gen <targets.json> <game_metadata.json> <output_dir>
                                     [--ids 1 2] [--personas 破坏狂 贪婪者]
                                     [--batch 2] [--verbose]

或者（pip install -e . 之后）：
    joker-test generate-charter <targets.json> <game_metadata.json> <output_dir>
"""

import argparse
import json
import logging
import os
import sys

from tqdm import tqdm

from joker_test.llm.base import LLMProvider, Message
from joker_test.llm.providers.anthropic import AnthropicProvider
from joker_test.llm.providers.mock import MockProvider
from joker_test.prompts import (
    load_charter_schema,
    load_default_heuristics,
    load_default_personas,
    render_analyst_prompt,
    render_architect_prompt,
)

logger = logging.getLogger(__name__)


# 默认用 AnthropicProvider（从 .env 读 MiMo 配置），失败则 None（测试用 MockProvider）。
try:
    DEFAULT_PROVIDER: LLMProvider | None = AnthropicProvider()
except (ValueError, Exception):  # noqa: BLE001
    DEFAULT_PROVIDER = None

# 提示词常量/模板/数据已抽到 joker_test.prompts 包（见 prompts/loader.py），
# 通过 load_default_personas / load_default_heuristics / render_*_prompt 调用。


# ============== 核心函数 ==============

def _log_message(message, label):
    """把 LLM 响应（含 thinking 块）漂亮地输出到日志。仅在 --verbose 时生效。"""
    for block in message.get("content", []):
        if "reasoningContent" in block:
            thinking_text = block["reasoningContent"].get("reasoningText", {}).get("text", "")
            logger.info(f"=== {label} — 思考 ===\n{thinking_text}\n")
        elif "text" in block:
            logger.info(f"=== {label} — 回复 ===\n{block['text']}\n")


def generate_charters(targets_file, game_meta_file, output_dir=None,
                      target_ids=None, persona_filter=None, batch=2,
                      provider: LLMProvider | None = None):
    """
    主函数：为每个 (target × persona) 组合生成 Charter。

    Args:
        targets_file: 被测目标列表 JSON（如铁匠铺、任务系统）
        game_meta_file: 游戏元数据 JSON（含 Persona 库、Heuristics 库、存档信息）
        output_dir: 输出目录
        target_ids: 只跑指定 target id（None=全部）
        persona_filter: 只跑指定 Persona 名称（None=全部）
        batch: 一次送几个 target 给 LLM（默认 2，因为每个 target 会展开多个 persona）
        provider: LLM provider。None 时用 DEFAULT_PROVIDER（从 .env 配置）。
            测试/CI 用 MockProvider。
    """
    if provider is None:
        provider = DEFAULT_PROVIDER
    if provider is None:
        raise RuntimeError(
            "无可用 LLM provider。解决方式（任选其一）：\n"
            "  1. 测试/CI：传入 MockProvider（from joker_test.llm import MockProvider）\n"
            "  2. 配置 .env（MIMO_API_KEY/MIMO_BASE_URL/MIMO_MODEL）后用 AnthropicProvider\n"
            "  3. 自定义：实现 LLMProvider 协议并注入（见 llm/base.py）"
        )

    # 1. 加载输入（统一用 utf-8，避免 Windows 默认 gbk 问题）
    with open(targets_file, encoding="utf-8") as f:
        targets = json.load(f)
    with open(game_meta_file, encoding="utf-8") as f:
        game_meta = json.load(f)

    if target_ids:
        targets = [t for t in targets if t["id"] in target_ids]

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # 2. 准备 Persona / Heuristics（metadata 优先，否则用默认，默认来自 prompts/data/*.yaml）
    personas = game_meta.get("personas") or load_default_personas()
    if persona_filter:
        personas = [p for p in personas if p["name"] in persona_filter]

    heuristics = game_meta.get("heuristics") or load_default_heuristics()

    print(f"目标系统数：{len(targets)}，Persona 数：{len(personas)}，"
          f"预计生成 Charter 数：{len(targets) * len(personas)}")

    # 3. 批量循环
    for i in tqdm(range(0, len(targets), batch), desc="生成 Charter 中"):
        batch_targets = targets[i:i + batch]

        # 4. 构建 Architect prompt（中文）—— 由 prompts/loader.py 渲染
        architect_prompt = render_architect_prompt(
            batch_targets, game_meta, personas, heuristics)

        messages: list[Message] = []

        from joker_test.llm.base import build_user_message  # noqa: PLC0415

        # 5. Step 1: Charter Architect 生成
        step1_msg = provider.create(messages=[build_user_message(architect_prompt)])
        _log_message(step1_msg, "Step 1 — Architect 生成")
        messages.append(step1_msg)

        # 6. Step 2: Charter Analyst 反思
        analyst_prompt = render_analyst_prompt(game_meta)
        step2_msg = provider.create(messages=messages + [build_user_message(analyst_prompt)])
        _log_message(step2_msg, "Step 2 — Analyst 反思")
        messages.append(step2_msg)

        # 7. Step 3: JSON 提取
        json_schema = load_charter_schema()
        extract_prompt = (
            f"谢谢。请将修订后的 Charter 输出为 JSON 数组。Schema 如下：\n\n{json_schema}\n\n"
            "请只回答 JSON 数组，不要其他文字。"
        )
        extract_msg = provider.create(messages=messages + [build_user_message(extract_prompt)])
        from joker_test.llm.providers.anthropic import (  # noqa: PLC0415
            extract_text,
            parse_json_array,
        )

        extracted = parse_json_array(extract_text(extract_msg))

        # 8. 每个 Charter 写成独立 JSON 文件
        for charter in extracted:
            write_charter(output_dir, charter)


def write_charter(output_dir, charter):
    """把单个 Charter 写成独立 JSON 文件。

    文件名格式：T{target_id:02d}_C{charter_id:02d}_{persona}_{system}.json
    特殊字符会被清洗（/ → "或"，空格 → _）。
    """
    cid = charter.get("charter_id", 0)
    tid = charter.get("target_id", 0)
    persona = charter.get("persona", "未知")
    target_name = charter.get("target_system", "未命名")

    # 清洗文件名（避免 / \ : * ? " < > | 等 Windows 非法字符）
    safe_persona = str(persona).replace(" ", "_")
    safe_target = (str(target_name)
                   .replace("/", "或")
                   .replace("\\", " ")
                   .replace(" ", "_"))
    file_name = f"T{tid:02d}_C{cid:02d}_{safe_persona}_{safe_target}.json"

    # 字段转换：charter_changes_game_state → env_probing_required
    # 这是 Phase 1 → Phase 4 的契约（参考 SpecOps test_spec_gen.py）
    changes_state = charter.pop("charter_changes_game_state", "no")
    charter["env_probing_required"] = "yes" if changes_state.strip().lower() == "yes" else "no"

    # 写文件（utf-8 + ensure_ascii=False，保留中文可读性）
    file_path = os.path.join(output_dir, file_name)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(charter, f, ensure_ascii=False, indent=4)
    print(f"已生成：{file_name}")


# ============== CLI 入口 ==============

def main():
    parser = argparse.ArgumentParser(
        description="游戏测试 Charter 生成器（Charter-Driven Exploratory Testing）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("targets_file", help="被测目标列表 JSON 路径")
    parser.add_argument("game_meta_file", help="游戏元数据 JSON 路径")
    parser.add_argument("output_dir", help="输出目录")
    parser.add_argument("--ids", type=int, nargs="+",
                        help="只跑指定 target id（默认全部）")
    parser.add_argument("--personas", nargs="+",
                        help="只跑指定 Persona 名称（如 破坏狂 贪婪者）")
    parser.add_argument("--batch", type=int, default=2,
                        help="一次送几个 target 给 LLM（默认 2，因为每个 target 会展开多个 persona）")
    parser.add_argument("--verbose", action="store_true",
                        help="记录完整 LLM 响应（含 thinking）到 <output_dir>/generation.log")
    parser.add_argument("--provider", choices=["mock", "default"], default="default",
                        help="LLM provider：mock=离线测试（无网络）；default=从.env配置（MiMo）"
                             "（SpecOps-src 存在则 Bedrock，否则报错）。默认 default。")
    args = parser.parse_args()

    # 配置日志（仅在 --verbose 时启用，否则 logger.info 是空操作）
    if args.verbose:
        os.makedirs(args.output_dir, exist_ok=True)
        log_path = os.path.join(args.output_dir, "generation.log")
        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
            handlers=[
                logging.FileHandler(log_path, mode="w", encoding="utf-8"),
                logging.StreamHandler(sys.stderr),
            ],
        )
        logger.info(f"详细日志输出到 {log_path}")

    # 解析 provider（M0 解耦：--provider mock 用于离线/CI；default 用 DEFAULT_PROVIDER）
    provider: LLMProvider | None
    if args.provider == "mock":
        provider = MockProvider()
    else:
        provider = None  # generate_charters 内部会 fallback 到 DEFAULT_PROVIDER

    generate_charters(
        args.targets_file,
        args.game_meta_file,
        output_dir=args.output_dir,
        target_ids=args.ids,
        persona_filter=args.personas,
        batch=args.batch,
        provider=provider,
    )


if __name__ == "__main__":
    main()
