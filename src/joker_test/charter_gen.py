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
import datetime
import json
import logging
import os
import sys
from pathlib import Path

# SpecOps-src 路径解析:
# - 开发时(本仓库):仓库根 / "SpecOps-src"  → src/joker_test/ 上溯 3 层
# - 用户环境:可能完全不存在,需给清晰错误
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
_SPECOPS_SRC_CANDIDATES = [
    _REPO_ROOT / "SpecOps-src",                                # 仓库内同级
    _HERE.parent.parent.parent / "SpecOps-src",                # 调研目录(开发时)
    Path(os.environ.get("SPECOPS_SRC", "")).expanduser(),      # 环境变量覆盖
]

_specops_src = None
for candidate in _SPECOPS_SRC_CANDIDATES:
    if candidate and candidate.exists() and (candidate / "converse.py").exists():
        _specops_src = candidate
        break

if _specops_src is not None:
    if str(_specops_src) not in sys.path:
        sys.path.insert(0, str(_specops_src))
    try:
        import converse  # noqa: E402
        import operate   # noqa: E402
    except ImportError as _e:
        raise ImportError(
            f"SpecOps-src 在 {_specops_src} 但 import 失败: {_e}\n"
            f"请安装 SpecOps 依赖: pip install boto3 tqdm pillow"
        ) from _e
else:
    raise ImportError(
        "charter_gen.py 当前依赖 SpecOps-src 的 converse.py 和 operate.py,但找不到该目录。\n\n"
        f"已尝试路径: {[str(p) for p in _SPECOPS_SRC_CANDIDATES if p]}\n\n"
        "解决方案(任选其一):\n"
        "  1. clone SpecOps 到仓库根:\n"
        "     git clone https://github.com/yusf1013/SpecOps.git SpecOps-src\n"
        "  2. 设置环境变量:\n"
        "     export SPECOPS_SRC=/path/to/SpecOps\n"
        "  3. 安装 SpecOps 依赖(若已 clone):\n"
        "     pip install boto3 tqdm pillow\n"
        "  4. 自己实现 converse/operate 接口(详见 DESIGN.md ADR-002)"
    )

from tqdm import tqdm  # noqa: E402

logger = logging.getLogger(__name__)


# ============== 常量定义 ==============

# Bug 定义：让 LLM 知道什么算 bug、什么不算（防 false positive）
# 关键短语："合理预期"——不是所有偏离都算 bug
BUG_DEFINITION = (
    "游戏 Bug 定义：被测 Agent（玩家 AI）的实际行为偏离了游戏设计的\"合理预期\"。包括但不限于：\n"
    "  - 视觉异常：穿模、UI 错位、贴图丢失、动画卡死、坐标越界\n"
    "  - 状态异常：数值越界（金币为负、血量超上限）、状态不同步（客户端/服务端不一致）\n"
    "  - 流程异常：任务无法完成、对话矛盾、流程死锁、软锁存档\n"
    "  - 经济异常：金币/物品复制、刷分漏洞、负数套利\n"
    "重要：Charter 的 expected_behaviors 只应包含\"游戏设计的合理预期\"，"
    "不要把\"建议性优化\"或\"细节体验\"也当成 Bug。"
)

# 反思 checklist：对应论文 Section 4.2 的 Oracle Generalizability + Prompt Completeness
# 第 5 条是关键：决定 Phase 4 是否需要内存读取或反向 Agent 验证
ANALYST_CHECKLIST = [
    "目标可达性：玩家 AI 在时间预算内（默认 30 分钟）能到达这个测试状态吗？前置条件是否齐全？",
    "Persona 一致性：Charter 的探索方向是否符合该 Persona 的行为特征？"
    "（破坏狂偏重崩溃，贪婪者偏重经济，急躁鬼偏重时序，完美主义偏重极限，混乱中立偏重非常规路径）",
    "启发式覆盖度：有没有遗漏经典 QA 经验？例如极端值、边界探索、时序错乱、组合输入、状态穿越、经济异常、多端冲突。",
    "Oracle 可泛化性：expected_behaviors 是否过度约束？应允许多种正常路径（玩家可以用不同方式完成任务）。",
    "该 Charter 是否会修改游戏环境（数值变化、状态变更、存档影响）？请记下 yes/no，"
    "这决定 Phase 4 是否需要内存读取或反向 Agent 验证。",
    "是否还有占位符未替换（如 <NPC 名称>、某道具）？必须用具体的游戏内实体。",
]

# Persona 库：5 种玩家人格。可在 game_metadata.json 中覆盖
DEFAULT_PERSONAS = [
    {
        "name": "破坏狂",
        "description": "尝试让游戏崩溃或进入未定义状态。偏好极端输入、非法操作、组合按键、边界操作。",
    },
    {
        "name": "贪婪者",
        "description": "尝试让数值越界（金币为负、物品堆叠超上限）。偏好重复操作、经济漏洞、刷分。",
    },
    {
        "name": "急躁鬼",
        "description": "跳过所有动画、过场、对话。偏好中途退出、断网重连、快速连点、UI 切换竞速。",
    },
    {
        "name": "完美主义",
        "description": "尝试不可能任务（10 级杀 boss、空手通关）。偏好极限玩法、最低数值通关。",
    },
    {
        "name": "混乱中立",
        "description": "做\"不太对\"的操作（卖任务道具、攻击 NPC、卡墙角）。偏好非常规路径、规则违反。",
    },
]

# Heuristics 库：7 条通用 QA 经验。可在 game_metadata.json 中追加或覆盖
DEFAULT_HEURISTICS = [
    "极端值：输入 0 / 负数 / 最大值（金币、数量、等级、坐标）",
    "边界探索：地图最边缘、空气墙、可穿透地形、地图拼接缝隙",
    "时序错乱：中途退出、断网重连、过场动画中操作、UI 弹窗中操作",
    "组合输入：同时按键、快速连点、键鼠冲突、连发手柄",
    "状态穿越：过场动画中操作、对话中移动、UI 卡住时切场景、保存读档穿越",
    "经济异常：金币为 0/负数、物品复制（保存-读档-保存循环）、跨 NPC 套利",
    "多端冲突：双端同时操作、客户端断线时服务端状态、跨设备同步异常",
]


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
                      target_ids=None, persona_filter=None, batch=2):
    """
    主函数：为每个 (target × persona) 组合生成 Charter。

    Args:
        targets_file: 被测目标列表 JSON（如铁匠铺、任务系统）
        game_meta_file: 游戏元数据 JSON（含 Persona 库、Heuristics 库、存档信息）
        output_dir: 输出目录
        target_ids: 只跑指定 target id（None=全部）
        persona_filter: 只跑指定 Persona 名称（None=全部）
        batch: 一次送几个 target 给 LLM（默认 2，因为每个 target 会展开多个 persona）
    """
    # 1. 加载输入（统一用 utf-8，避免 Windows 默认 gbk 问题）
    with open(targets_file, "r", encoding="utf-8") as f:
        targets = json.load(f)
    with open(game_meta_file, "r", encoding="utf-8") as f:
        game_meta = json.load(f)

    if target_ids:
        targets = [t for t in targets if t["id"] in target_ids]

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # 2. 准备 Persona / Heuristics（metadata 优先，否则用默认）
    personas = game_meta.get("personas", DEFAULT_PERSONAS)
    if persona_filter:
        personas = [p for p in personas if p["name"] in persona_filter]

    heuristics = game_meta.get("heuristics", DEFAULT_HEURISTICS)

    print(f"目标系统数：{len(targets)}，Persona 数：{len(personas)}，"
          f"预计生成 Charter 数：{len(targets) * len(personas)}")

    # 3. 批量循环
    for i in tqdm(range(0, len(targets), batch), desc="生成 Charter 中"):
        batch_targets = targets[i:i + batch]

        # 4. 构建 Architect prompt（中文）
        architect_prompt = _build_architect_prompt(
            batch_targets, game_meta, personas, heuristics)

        messages = []

        # 5. Step 1: Charter Architect 生成（reasoning=16000，标准深度）
        step1_msg = converse.simple_converse(architect_prompt, messages, reasoning=16000)
        _log_message(step1_msg, "Step 1 — Architect 生成")
        messages.append(step1_msg)

        # 6. Step 2: Charter Analyst 反思（reasoning=32000，最深思考）
        #    关键：同一 conversation 反思，Analyst 能看到 Architect 的 thinking
        analyst_prompt = _build_analyst_prompt(game_meta)
        step2_msg = converse.simple_converse(analyst_prompt, messages, reasoning=32000)
        _log_message(step2_msg, "Step 2 — Analyst 反思")
        messages.append(step2_msg)

        # 7. Step 3: JSON 提取（让 LLM 按 schema 输出结构化 Charter 数组）
        json_schema = _build_json_schema()
        extracted = operate.converse_json(
            messages,
            f"谢谢。请将修订后的 Charter 输出为 JSON 数组。Schema 如下：\n\n{json_schema}"
        )

        # 8. 每个 Charter 写成独立 JSON 文件
        for charter in extracted:
            write_charter(output_dir, charter)


def _build_architect_prompt(targets, game_meta, personas, heuristics):
    """构建 Architect prompt（中文）—— 负责生成 Charter 草稿。"""
    game_name = game_meta.get("game_name", "未知游戏")
    game_overview = game_meta.get("overview", "（无游戏概述）")
    load_save = game_meta.get("load_save", "默认新存档")

    personas_text = "\n".join(
        f"  - {p['name']}：{p['description']}" for p in personas
    )
    heuristics_text = "\n".join(f"  - {h}" for h in heuristics)
    targets_json = json.dumps(targets, ensure_ascii=False, indent=4)

    return (
        f"今天是 {datetime.date.today().isoformat()}。\n\n"
        f"你正在为游戏《{game_name}》生成\"探索式测试章程\"（Charter）。\n\n"
        f"## 游戏基本信息\n{game_overview}\n\n"
        f"## 默认加载存档\n{load_save}\n\n"
        f"## 被测系统列表\n{targets_json}\n\n"
        f"## 要生成的 Persona（玩家人格）\n{personas_text}\n\n"
        f"## 参考的 QA 经验启发式（Heuristics 库）\n{heuristics_text}\n\n"
        f"## 任务\n"
        f"请为每个 (系统 × Persona) 组合生成一个独立的 Charter。\n\n"
        f"{BUG_DEFINITION}\n\n"
        f"## 每个 Charter 必须满足以下质量要求\n"
        f"a) **目标具体、可观察、可判断 bug** —— 不要写\"测试强化系统\"，"
        f"要写\"测试+10 满级武器的强化边界\"\n"
        f"b) **Persona 风格明显** —— 不同 Persona 对同一系统的探索方向应该显著不同。"
        f"破坏狂 vs 贪婪者测同一个铁匠铺，目标完全不同\n"
        f"c) **启发式具体到操作层面** —— 不要写\"测试边界\"，"
        f"要写\"连续点击强化 10 次直到金币耗尽\"\n"
        f"d) **expected_behaviors 是\"正常情况下的合理预期\"** —— 作为异常的对照基线，"
        f"不是\"理想完美行为\"\n"
    )


def _build_analyst_prompt(game_meta):
    """构建 Analyst prompt（中文）—— 6 条反思 checklist。

    关键设计：和 Architect 用同一 conversation，Analyst 能看到 Architect 的 thinking。
    但反思的评判标准和 Architect 不同（这就是论文说的 "separating the guidelines"）。
    """
    game_specific = game_meta.get("analyst_extra_instructions", "")

    checklist_text = "\n".join(
        f"{i + 1}. {item}" for i, item in enumerate(ANALYST_CHECKLIST)
    )

    return (
        f"谢谢。现在请按以下 checklist 逐条审视刚才生成的 Charter：\n\n"
        f"{checklist_text}\n\n"
        f"{game_specific}\n"
        f"如果有任何不达标的地方，请直接修订（不要重新生成，只修订有问题的部分）。\n"
    )


def _build_json_schema():
    """构建 Charter 的 JSON 输出 schema（让 LLM 知道输出什么字段）。"""
    return (
        "{\n"
        "\tcharter_id: int,                              // Charter 唯一 ID\n"
        "\ttarget_id: int,                               // 对应被测系统的 id\n"
        "\tpersona: string,                              // Persona 名称\n"
        "\ttarget_system: string,                        // 被测系统名称\n"
        "\ttarget_description: string,                   // 被测系统描述\n"
        "\tload_save: string,                            // 加载哪个存档\n"
        "\tgoal: string,                                 // Charter 总目标\n"
        "\texploration_targets: string[],                // 具体探索目标列表\n"
        "\theuristics: string[],                         // 应用的启发式（具体到操作）\n"
        "\texpected_behaviors: string[],                 // 正常预期（作为异常对照基线）\n"
        "\tcoverage_dimensions: {                        // Coverage Map 四维\n"
        "\t\tregion: string[],                          //   区域\n"
        "\t\tfunction: string[],                        //   功能\n"
        "\t\toperation: string[],                       //   操作\n"
        "\t\tstate: string[]                            //   状态\n"
        "\t},\n"
        "\ttime_budget_minutes: int,                     // 时间预算（分钟）\n"
        "\tcharter_changes_game_state: \"yes\" | \"no\",     // 是否改游戏状态\n"
        "\tseverity_threshold: \"P0\" | \"P1\" | \"P2\"        // 上报 Bug 的最低严重度\n"
        "}"
    )


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

    generate_charters(
        args.targets_file,
        args.game_meta_file,
        output_dir=args.output_dir,
        target_ids=args.ids,
        persona_filter=args.personas,
        batch=args.batch,
    )


if __name__ == "__main__":
    main()
