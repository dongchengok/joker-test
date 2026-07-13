"""joker-test CLI 入口。

pyproject.toml 已注册 joker-test = "joker_test.cli:main"。
子命令：
  - explore：智能探索入口（固化命中检查 + 三模式探索 + 可串联固化/执行）
  - record：录制操作流程并生成 test_case（explore --mode manual 的快捷方式）
  - explore-ui：界面探索（产出 StateMap，explore --no-solidify 的快捷方式）
  - run-smoke：跑冒烟测试 + 报告（脱离 LLM）
  - run-all：一键编排（AgenticOrchestrator 四阶段流水线）
  - generate-charter：生成 Charter（委托 charter_gen）
  - validate：校验 charter/test 文件
无参数等价 run-all。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from joker_test import __version__
from joker_test.executor import set_active_backend


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。返回退出码（0=成功）。"""
    parser = argparse.ArgumentParser(
        prog="joker-test",
        description=f"joker-test v{__version__} —— AI 驱动的游戏测试平台",
    )
    sub = parser.add_subparsers(dest="command", required=False)

    # generate-charter
    p_gc = sub.add_parser("generate-charter", help="生成探索式测试 Charter")
    p_gc.add_argument("targets_file")
    p_gc.add_argument("game_meta_file")
    p_gc.add_argument("output_dir")
    p_gc.add_argument("--ids", type=int, nargs="+")
    p_gc.add_argument("--personas", nargs="+")
    p_gc.add_argument("--provider", choices=["mock", "default"], default="default")

    # explore：智能探索入口（固化命中检查 + 三模式探索）
    p_exp = sub.add_parser(
        "explore", help="智能探索入口（固化命中检查 + 三模式探索）"
    )
    p_exp.add_argument("--intent", required=True, help="测试意图")
    p_exp.add_argument("--mode", choices=["manual", "dfs", "llm"], default="llm")
    p_exp.add_argument("--reuse", default=None, help="显式复用资产路径")
    p_exp.add_argument(
        "--check-reuse", dest="check_reuse", action="store_true", default=True
    )
    p_exp.add_argument(
        "--no-check-reuse", dest="check_reuse", action="store_false"
    )
    p_exp.add_argument("--solidify", dest="solidify", action="store_true", default=True)
    p_exp.add_argument("--no-solidify", dest="solidify", action="store_false")
    p_exp.add_argument("--execute", dest="execute", action="store_true", default=True)
    p_exp.add_argument("--no-execute", dest="execute", action="store_false")
    p_exp.add_argument("--game", default="")
    p_exp.add_argument("--backend", default="fake", choices=["airtest", "fake"])
    p_exp.add_argument("--max-steps", type=int, default=30)
    p_exp.add_argument(
        "--strategy",
        choices=["react_state", "conversation"],
        default="conversation",
        help="探索策略（conversation=对齐Open-AutoGLM默认 / react_state=ReAct+状态机固定token）",
    )
    p_exp.add_argument("--no-trace", action="store_true")
    p_exp.add_argument("--config", default="joker-test-config.json",
                        help="配置文件路径（不存在则自动生成）")

    # explore-ui
    p_eu = sub.add_parser("explore-ui", help="探索游戏界面，产出界面地图")
    p_eu.add_argument("--window", required=True, help="窗口标题（子串匹配）")
    p_eu.add_argument("--output", required=True, help="输出 StateMap JSON 路径")
    p_eu.add_argument("--max-depth", type=int, default=5)
    p_eu.add_argument("--no-trace", action="store_true", help="关闭 trace（默认开启）")

    # run-smoke
    p_rs = sub.add_parser("run-smoke", help="跑冒烟测试 + 产出报告")
    p_rs.add_argument("test_paths", nargs="+", help="测试文件/目录")
    p_rs.add_argument("--report-dir", default="reports", help="报告输出目录")
    p_rs.add_argument("--game", default="unknown")

    # run-all
    p_ra = sub.add_parser("run-all", help="一键编排：探索→生成→跑→报告")
    p_ra.add_argument("--game-meta", default=None, help="游戏元数据 JSON")
    p_ra.add_argument("--report-dir", default="reports")
    p_ra.add_argument("--mock", action="store_true", help="用 MockBackend（CI）")
    p_ra.add_argument("--no-trace", action="store_true", help="关闭 trace（默认开启）")
    p_ra.add_argument("--config", default="joker-test-config.json",
                       help="配置文件路径（不存在则自动生成）")

    # validate（DESIGN §4.6.1，MVP 骨架）
    p_val = sub.add_parser("validate", help="校验 charter JSON 或 testcase Python 文件")
    p_val.add_argument("path", help="要校验的文件（charter .json 或 test .py）")
    p_val.add_argument("--type", choices=["charter", "test"], default="auto",
                       help="校验类型（auto=按扩展名自动判断）")

    # record：录制操作流程（pynput 监听人类操作）→ LLM 起名 → 默认串联生成 test_case
    p_rec = sub.add_parser(
        "record",
        help="录制操作流程并生成 test_case（pynput 监听人类操作）",
    )
    p_rec.add_argument("--name", default=None, help="流程名（不传则 LLM 起中文名）")
    p_rec.add_argument("--output", default="flows", help="录制产物父目录（默认 flows/）")
    p_rec.add_argument(
        "--no-gen", action="store_true", help="只录制不生成 test_case"
    )
    p_rec.add_argument(
        "--gen-only",
        action="store_true",
        help="跳过录制，对已有 flow 目录重新生成 test_case",
    )
    p_rec.add_argument(
        "--no-trace", action="store_true", help="关闭 trace（默认开启）"
    )

    args = parser.parse_args(argv)

    # 无参数 → 默认 run-all
    if args.command is None:
        args = parser.parse_args(["run-all"] + (argv or []))

    # trace 默认开启（惰性建，atexit 自动收尾）；--no-trace 才主动关闭
    if getattr(args, "no_trace", False):
        from joker_test.trace import set_tracer  # noqa: PLC0415

        set_tracer(None)

    if args.command == "generate-charter":
        return _cmd_generate_charter(args)
    if args.command == "explore":
        return _cmd_explore(args)
    if args.command == "explore-ui":
        return _cmd_explore_ui(args)
    if args.command == "run-smoke":
        return _cmd_run_smoke(args)
    if args.command == "run-all":
        return _cmd_run_all(args)
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "record":
        return _cmd_record(args)
    return 1


def _cmd_generate_charter(args: argparse.Namespace) -> int:
    from joker_test.charter_gen import generate_charters  # noqa: PLC0415
    from joker_test.llm.providers.mock import MockProvider  # noqa: PLC0415

    provider = MockProvider() if args.provider == "mock" else None
    generate_charters(
        args.targets_file, args.game_meta_file, output_dir=args.output_dir,
        target_ids=args.ids, persona_filter=args.personas, provider=provider,
    )
    return 0


def _cmd_explore_ui(args: argparse.Namespace) -> int:
    """界面探索（M6：默认用 FakeBackend 演示，真实接游戏走 AirtestBackend 推迟）。"""
    from joker_test.executor.backends.fake import FakeBackend, ScreenCfg  # noqa: PLC0415
    from joker_test.executor.base import BBox  # noqa: PLC0415
    from joker_test.explorer import UIExplorer  # noqa: PLC0415

    # M6 演示用最小 FakeBackend（真实游戏接入需 M6 后续 + 用户游戏）
    backend = FakeBackend(
        screens={
            "root": ScreenCfg(
                texts_map={args.window: BBox(0.5, 0.5, 0.2, 0.1)},
                bg_pixel=(10, 20, 30),
            ),
        },
        initial_screen="root",
    )
    set_active_backend(backend)
    explorer = UIExplorer(backend, max_depth=args.max_depth, screen_change_timeout=0.5)
    state_map = explorer.explore()
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(state_map.model_dump_json(indent=2), encoding="utf-8")
    print(f"界面地图已写入: {args.output}（{len(state_map.screens)} 个界面）")
    return 0


def _cmd_run_smoke(args: argparse.Namespace) -> int:
    from joker_test.reporters import HtmlReporter, JsonReporter, MultiReporter  # noqa: PLC0415
    from joker_test.runner import run_tests  # noqa: PLC0415

    session = run_tests(args.test_paths, backend_name="fake", game_name=args.game)
    report_dir = Path(args.report_dir)
    multi = MultiReporter([
        JsonReporter(report_dir / "report.json"),
        HtmlReporter(report_dir / "report.html"),
    ])
    multi.on_session_start(session)
    for r in session.results:
        multi.on_test_end(r)
    multi.on_session_end(session)
    summary = multi.finalize()
    print(f"测试完成：通过 {session.passed}，失败 {session.failed}")
    print(f"报告：\n{summary}")
    return 0 if session.failed == 0 else 1


def _cmd_explore(args: argparse.Namespace) -> int:
    """智能探索入口：固化命中检查 + 三模式探索 + 可串联固化/执行。"""
    from joker_test.config import load_config  # noqa: PLC0415
    from joker_test.pipeline import ExploreConfig, build_orchestrator  # noqa: PLC0415

    user_cfg = load_config(args.config)
    cfg = ExploreConfig(
        intent=args.intent,
        mode=args.mode,
        reuse=args.reuse,
        check_reuse=args.check_reuse,
        solidify=args.solidify,
        execute=args.execute,
        game_name=args.game,
        backend_name=args.backend,
        max_explore_steps=args.max_steps,
        explore_strategy=args.strategy,
        plugins=user_cfg.get("plugins", ["ocr"]),
        plugin_path=user_cfg.get("plugin_path"),
    )
    orch = build_orchestrator(cfg)
    result = orch.run(cfg)
    print(f"探索完成：{result.report.summary}")
    if result.reflect.risks:
        print(f"风险提示：{len(result.reflect.risks)} 项")
        for risk in result.reflect.risks:
            print(f"  [{risk.severity}] {risk.category}: {risk.description}")
    print(f"可信度：{result.reflect.confidence_score:.1%}")
    return 0


def _cmd_run_all(args: argparse.Namespace) -> int:
    """一键编排：AgenticOrchestrator 四阶段流水线。"""
    from joker_test.pipeline import ExploreConfig, build_orchestrator  # noqa: PLC0415

    game_meta: dict = {}
    if args.game_meta:
        game_meta = json.loads(Path(args.game_meta).read_text(encoding="utf-8"))
    cfg = ExploreConfig(
        intent=game_meta.get("game_name", "探索式测试"),
        mode="llm",
        game_name=game_meta.get("game_name", "unknown"),
        backend_name="fake" if args.mock else "fake",
    )
    orch = build_orchestrator(cfg, report_dir=args.report_dir)
    result = orch.run(cfg)
    print(f"编排完成：\n{result.report.summary}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    """校验 charter JSON 或 testcase Python（DESIGN §4.6.1，MVP 骨架）。

    charter：校验 schema 必填字段（charter_id/persona/goal 等）
    test：校验 Python 语法（ast.parse）+ ruff
    """
    path = Path(args.path)
    if not path.exists():
        print(f"✗ 文件不存在: {path}")
        return 1

    # 自动判断类型
    vtype = args.type
    if vtype == "auto":
        vtype = "charter" if path.suffix == ".json" else "test"

    if vtype == "charter":
        return _validate_charter(path)
    return _validate_test(path)


def _validate_charter(path: Path) -> int:
    """校验 charter JSON 的必填字段（DESIGN §5.1 schema）。"""
    import json as json_mod  # noqa: PLC0415

    required = {
        "charter_id", "target_id", "persona", "target_system", "goal",
        "exploration_targets", "time_budget_minutes", "env_probing_required",
    }
    try:
        data = json_mod.loads(path.read_text(encoding="utf-8"))
    except json_mod.JSONDecodeError as e:
        print(f"✗ JSON 解析失败: {e}")
        return 1

    missing = required - set(data.keys())
    if missing:
        print(f"✗ 缺少必填字段: {sorted(missing)}")
        return 1
    print(f"✓ charter 校验通过: {path.name}（{len(data)} 字段）")
    return 0


def _validate_test(path: Path) -> int:
    """校验 test Python 文件的语法（ast.parse）。"""
    import ast  # noqa: PLC0415

    code = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        print(f"✗ 语法错误: {e.msg}（行 {e.lineno}）")
        return 1

    # 检查至少有一个 test_ 函数
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    test_funcs = [f for f in func_names if f.startswith("test_")]
    if not test_funcs:
        print("⚠ 未找到 test_ 开头的函数（可能不是测试文件）")
    print(f"✓ test 校验通过: {path.name}（{len(test_funcs)} 个测试函数）")
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    """录制操作流程 → LLM 起名 rename → 默认串联生成 test_case。

    流程：
      1. pynput 全局监听，用户手动操作游戏（Ctrl+C 结束）
      2. LLM 看操作流起中文名，rename 目录成 <时间戳>_<中文名>
      3. 默认串联：LLM 把坐标语义化 + 生成 pytest test_case（--no-gen 跳过）
      4. --gen-only：跳过录制，对已有 flow 目录重新生成
    """
    import datetime  # noqa: PLC0415
    import shutil  # noqa: PLC0415

    from joker_test.flow import FlowNamer, GlobalRecorder  # noqa: PLC0415
    from joker_test.llm.providers.anthropic import (
        AnthropicProvider,  # noqa: PLC0415
        load_env,  # noqa: PLC0415
    )

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 解析 LLM 配置（起名 + 生成都要用）
    cfg = load_env()
    model = cfg.get("MIMO_MODEL", "mimo-v2.5")

    # ---- --gen-only 模式：跳过录制，对已有 flow 重新生成 ----
    if args.gen_only:
        # 找 output_dir 下最新的 flow 目录
        flow_dirs = sorted(
            [d for d in output_dir.iterdir() if d.is_dir() and not d.name.startswith(".")],
            key=lambda p: p.name,
        )
        if not flow_dirs:
            print(f"✗ {output_dir} 下无 flow 目录")
            return 1
        flow_dir = flow_dirs[-1]
        print(f"对 {flow_dir.name} 重新生成 test_case")
        return _generate_from_flow(flow_dir, cfg, model)

    # ---- 正常录制流程 ----
    print("=" * 60)
    print("joker-test 操作录制（pynput 全局监听）")
    print("现在去游戏窗口操作，完成后按 Ctrl+C 结束录制")
    print("=" * 60)

    recorder = GlobalRecorder(output_dir=output_dir, pynput_mode=True)
    recorder.start()

    try:
        import time  # noqa: PLC0415

        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n结束录制，正在处理...")

    flow = recorder.stop()
    flow_yaml = recorder.save_flow_yaml(flow)
    tmp_dir = recorder.flow_dir
    print(f"✓ 录制 {len(flow.steps)} 步 → {flow_yaml}")

    if not flow.steps:
        print("✗ 未录到任何操作，退出")
        return 1

    # ---- LLM 起名 rename ----
    if args.name:
        flow.name = args.name
        flow.description = "（手动指定名称）"
    else:
        print("LLM 起名中...")
          # noqa: PLC0415

        namer_llm = AnthropicProvider()
        namer = FlowNamer(namer_llm)
        name, desc = namer.name_flow(flow, tmp_dir / "screenshots")
        flow.name = name
        flow.description = desc

    # rename 目录：<时间戳>_<中文名>
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = flow.name.replace("/", "_").replace("\\", "_").replace(" ", "_")
    final_dir = output_dir / f"{ts}_{safe_name}"
    if final_dir.exists():
        final_dir = output_dir / f"{ts}_{safe_name}_{flow.steps[0].elapsed_s}"
    shutil.move(str(tmp_dir), str(final_dir))
    # 更新 flow.yaml 到最终目录
    (final_dir / "flow.yaml").write_text(
        _dump_flow_yaml(flow), encoding="utf-8"
    )
    # 写 description.txt
    (final_dir / "description.txt").write_text(
        f"{flow.name}\n{flow.description}\n", encoding="utf-8"
    )
    print(f"✓ 目录: {final_dir.name}")
    print(f"  名称: {flow.name}")
    print(f"  说明: {flow.description}")

    if args.no_gen:
        print("--no-gen 指定，跳过 test_case 生成")
        return 0

    # ---- 串联生成 test_case ----
    return _generate_from_flow(final_dir, cfg, model)


def _generate_from_flow(flow_dir: Path, cfg: dict, model: str) -> int:
    """对已录制的 flow 目录生成 test_case（坐标语义化 + LLM 生成）。"""
    import yaml  # noqa: PLC0415

    from joker_test.flow import RecordedFlow, RecordedFlowGenerator  # noqa: PLC0415
    from joker_test.generator.generator import write_tests_to_dir  # noqa: PLC0415
    from joker_test.llm.providers.anthropic import AnthropicProvider  # noqa: PLC0415
    from joker_test.ocr.providers.rapidocr import RapidOCRProvider  # noqa: PLC0415

    # 读 flow.yaml
    flow_path = flow_dir / "flow.yaml"
    if not flow_path.exists():
        print(f"✗ 找不到 {flow_path}")
        return 1
    data = yaml.safe_load(flow_path.read_text(encoding="utf-8"))
    flow = RecordedFlow(**data)

    # 找 game_meta（尝试几个常见位置）
    repo_root = flow_dir.parent.parent
    game_meta_path = repo_root / "examples" / "spd_game_metadata.json"
    if not game_meta_path.exists():
        game_meta_path = repo_root / "examples" / "game_metadata.json"
    game_meta = (
        json.loads(game_meta_path.read_text(encoding="utf-8"))
        if game_meta_path.exists()
        else {"game_name": "未知游戏", "overview": ""}
    )

    print("生成 test_case 中（LLM 坐标语义化 + 代码生成）...")
      # noqa: PLC0415

    provider = AnthropicProvider()
    ocr = RapidOCRProvider()
    gen = RecordedFlowGenerator(provider=provider, ocr_provider=ocr)
    try:
        tests = gen.generate(flow, flow_dir, game_meta)
    except Exception as e:  # noqa: BLE001
        print(f"✗ 生成失败: {e}")
        import traceback  # noqa: PLC0415

        traceback.print_exc()
        return 1

    if not tests:
        print("✗ LLM 未生成有效测试代码")
        return 1

    # 落盘到 generated_smoke/
    gen_dir = repo_root / "tests" / "generated_smoke"
    test_paths = write_tests_to_dir(tests, gen_dir)
    print(f"✓ 生成 {len(tests)} 个测试文件 → {gen_dir}/")
    for p in test_paths:
        print(f"  - {p.name}")

    print(f"\n下一步: joker-test run-smoke {gen_dir} --game '{game_meta.get('game_name', '')}'")
    return 0


def _dump_flow_yaml(flow) -> str:  # noqa: ANN001
    """把 RecordedFlow dump 成 yaml 字符串。"""
    import yaml  # noqa: PLC0415

    return yaml.dump(
        flow.model_dump(), allow_unicode=True, default_flow_style=False, sort_keys=False
    )


if __name__ == "__main__":
    sys.exit(main())
