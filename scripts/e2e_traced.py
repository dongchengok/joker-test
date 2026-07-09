"""端到端流程编排（要求1-4 全覆盖，带完整 trace）。

四个阶段：
1. 生成测试目标 charter（charter_gen + MiMo）
2. 探索 SPD + LLM 生成 test_case（explorer + generator + MiMo）
3. executor 执行 test_case + 产出报告
4. 全程 trace（程序行为 + LLM 行为 + prompt 调试 dump）

用法：
    python scripts/e2e_traced.py
    # trace 输出到 traces/e2e_<timestamp>/
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

# === 1. 配置 ===
RUN_TS = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
OUTPUTS_DIR = REPO / "outputs" / f"e2e_{RUN_TS}"
REPORTS_DIR = REPO / "reports" / f"e2e_{RUN_TS}"

TARGETS_FILE = REPO / "examples" / "spd_targets.json"
GAME_META_FILE = REPO / "examples" / "spd_game_metadata.json"
CHARTER_DIR = OUTPUTS_DIR / "charters"
TESTCASES_DIR = OUTPUTS_DIR / "generated_tests"

WINDOW_TITLE = "Shattered"

print("=" * 70)
print("joker-test 端到端流程（带 trace）")
print(f"时间戳：{RUN_TS}")
print(f"输出：{OUTPUTS_DIR}")
print(f"报告：{REPORTS_DIR}")
print("=" * 70)

# === 2. 基础设施 ===
# 全局 trace 模式：不用手动建 Tracer，首次打点惰性建，atexit 自动收尾。
# 这里显式建一个是为了拿 trace_dir 存探索截图（trace 子目录）。
from joker_test.trace import Tracer, set_tracer  # noqa: E402

tracer = Tracer(output_dir=REPO / "traces", name="e2e")
set_tracer(tracer)  # 注册全局 + atexit 自动 finalize
TRACE_DIR = tracer.trace_dir  # 实际子目录路径（后续存探索截图用）
print(f"Trace：{TRACE_DIR}")
for d in [OUTPUTS_DIR, REPORTS_DIR, CHARTER_DIR, TESTCASES_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def wait_spd_window(timeout: float = 30.0) -> bool:
    """等 SPD 窗口就绪。"""
    import win32gui  # noqa: PLC0415

    for _ in range(int(timeout)):
        found = [False]
        def _check(h, _):  # noqa: ANN001
            if win32gui.IsWindowVisible(h) and WINDOW_TITLE in win32gui.GetWindowText(h):
                found[0] = True  # noqa: B023
            return True
        win32gui.EnumWindows(_check, None)
        if found[0]:
            return True
        time.sleep(1)
    return False


def make_llm():
    """构造带 trace 的 MiMo provider（TracingProvider 包装，自动记录 LLM 调用）。

    全局 trace 模式：不传 tracer，自动进全局 tracer。
    """
    # 读 .env 配置
    from joker_test.llm.providers.anthropic import load_env  # noqa: PLC0415
    from joker_test.llm.providers.mimo import MiMoProvider  # noqa: PLC0415
    from joker_test.llm.providers.tracing import TracingProvider  # noqa: PLC0415
    cfg = load_env()
    model = cfg.get("MIMO_MODEL", "mimo-v2.5")
    return TracingProvider(MiMoProvider(), model=model)


# === 阶段 0：检查 SPD 窗口 ===
print("\n[阶段 0] 检查 SPD 窗口...")
with tracer.stage("check_spd"):
    if not wait_spd_window():
        tracer.log_error("SPD 窗口未就绪", {"window": WINDOW_TITLE})
        print("✗ SPD 未启动。请先运行 .test-targets/SPD/'Shattered Pixel Dungeon.exe'")
        print(tracer.finalize())
        sys.exit(1)
    tracer.log_event("spd_ready", {"window": WINDOW_TITLE})
    print("✓ SPD 窗口就绪")


# === 阶段 1：生成测试目标 charter ===
print("\n[阶段 1] 生成测试目标 charter（MiMo）...")
with tracer.stage("charter_gen"):
    from joker_test.charter_gen import generate_charters  # noqa: PLC0415

    llm = make_llm()
    tracer.log_event("llm_provider_ready", {"model": "mimo-v2.5"})

    try:
        generate_charters(
            targets_file=str(TARGETS_FILE),
            game_meta_file=str(GAME_META_FILE),
            output_dir=str(CHARTER_DIR),
            target_ids=[1],  # 只跑 target 1（主菜单到地牢入口）
            persona_filter=["急躁鬼"],  # 只用 1 个 persona，省时间
            batch=1,
            provider=llm,
        )
        charters = list(CHARTER_DIR.glob("*.json"))
        tracer.log_event("charters_generated", {"count": len(charters),
                                                  "files": [f.name for f in charters]})
        print(f"✓ 生成 {len(charters)} 个 charter")
        for f in charters:
            print(f"  - {f.name}")
    except Exception as e:
        tracer.log_error(f"charter 生成失败: {e}", {"traceback": str(e.__cause__)[:500]})
        print(f"✗ charter 生成失败: {e}")
        import traceback  # noqa: PLC0415
        traceback.print_exc()


# === 阶段 2：探索 SPD + LLM 生成 test_case ===
print("\n[阶段 2] 探索 SPD + LLM 生成 test_case...")
with tracer.stage("explore_and_generate"):
    from joker_test.executor.backends.airtest import AirtestBackend  # noqa: PLC0415
    from joker_test.explorer.llm_explorer import LLMExplorer  # noqa: PLC0415
    from joker_test.generator.generator import (  # noqa: PLC0415
        SmokeTestGenerator,
        write_tests_to_dir,
    )
    from joker_test.ocr.providers.rapidocr import RapidOCRProvider  # noqa: PLC0415

    explore_backend = AirtestBackend(window_title=WINDOW_TITLE, ocr=RapidOCRProvider())
    explore_backend.connect()
    tracer.log_event("backend_connected", {"backend": "airtest", "window": WINDOW_TITLE})

    try:
        # 2a. LLM 驱动探索（看截图 + 决策，充分探索）
        explore_llm = make_llm()
        explorer = LLMExplorer(
            backend=explore_backend,
            llm=explore_llm,
            max_steps=3,
            screenshot_dir=str(TRACE_DIR / "explore_screens"),
        )
        uimap = explorer.explore()
        tracer.log_event("uimap_explored", {
            "screens": len(uimap.screens),
            "elements": sum(len(s.elements) for s in uimap.screens),
            "screen_names": uimap.backend_info.get("screen_names", []),
        })
        print(f"✓ 探索完成：{len(uimap.screens)} 界面，"
              f"{sum(len(s.elements) for s in uimap.screens)} 元素")
        for s in uimap.screens:
            names = uimap.backend_info.get("screen_names", [])
            idx = uimap.screens.index(s)
            nm = names[idx] if idx < len(names) else s.id
            print(f"  - {nm}: {[e.text for e in s.elements if e.text]}")

        # 存 UIMap
        uimap_path = OUTPUTS_DIR / "uimap.json"
        uimap_path.write_text(uimap.model_dump_json(indent=2), encoding="utf-8")
        tracer.log_event("uimap_saved", {"path": str(uimap_path)})

    except Exception as e:
        tracer.log_error(f"探索失败: {e}")
        print(f"✗ 探索失败: {e}")
        import traceback  # noqa: PLC0415
        traceback.print_exc()
        uimap = None
    finally:
        explore_backend.close()

    # 2b. LLM 生成 test_case（基于 UIMap）
    if uimap and uimap.screens:
        try:
            game_meta = json.loads(GAME_META_FILE.read_text(encoding="utf-8"))
            gen_llm = make_llm()
            gen = SmokeTestGenerator(provider=gen_llm)
            tests = gen.generate(uimap, game_meta)
            tracer.log_event("testcases_generated", {
                "count": len(tests),
                "names": [t.test_filename for t in tests],
            })
            print(f"✓ 生成 {len(tests)} 个测试文件")

            test_paths = write_tests_to_dir(tests, str(TESTCASES_DIR))
            for p in test_paths:
                tracer.log_event("testcase_written", {
                    "path": str(p),
                    "lines": len(p.read_text(encoding="utf-8").splitlines()),
                })
                print(f"  - {p.name}")
        except Exception as e:
            tracer.log_error(f"testcase 生成失败: {e}")
            print(f"✗ testcase 生成失败: {e}")
            import traceback  # noqa: PLC0415
            traceback.print_exc()


# === 阶段 2.5：重置游戏状态（探索后回到主菜单） ===
# SPD 角色选择界面 escape 回不去主菜单，最可靠的方式是重启游戏
print("\n[阶段 2.5] 重置游戏状态（重启 SPD 回主菜单）...")
with tracer.stage("reset_game"):
    import subprocess  # noqa: PLC0415
    import time as _time  # noqa: PLC0415

    import win32gui  # noqa: PLC0415

    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "Shattered Pixel Dungeon.exe"],
            capture_output=True,
        )
        _time.sleep(2)
        exe = str(REPO / ".test-targets" / "SPD" / "Shattered Pixel Dungeon.exe")
        subprocess.Popen([exe], cwd=os.path.dirname(exe))
        # 等窗口就绪
        for _ in range(20):
            found = [False]
            def _check(h, _):  # noqa: ANN001
                if win32gui.IsWindowVisible(h) and WINDOW_TITLE in win32gui.GetWindowText(h):
                    found[0] = True  # noqa: B023
                return True
            win32gui.EnumWindows(_check, None)
            if found[0]:
                break
            _time.sleep(1)
        _time.sleep(3)  # 额外等 LibGDX 初始化
        tracer.log_event("game_reset", {"method": "restart"})
        print("✓ SPD 已重启回主菜单")
    except Exception as e:
        tracer.log_error(f"重置失败: {e}")
        print(f"⚠ 重置失败: {e}")


# === 阶段 3：executor 执行 test_case + 报告 ===
print("\n[阶段 3] 执行 test_case + 生成报告...")
with tracer.stage("run_tests"):
    from joker_test.reporters.html import HtmlReporter  # noqa: PLC0415
    from joker_test.reporters.json import JsonReporter  # noqa: PLC0415
    from joker_test.reporters.multi import MultiReporter  # noqa: PLC0415
    from joker_test.runner import run_tests  # noqa: PLC0415

    test_paths = list(TESTCASES_DIR.glob("test_*.py"))
    if not test_paths:
        tracer.log_error("无测试文件可执行")
        print("✗ 无测试文件")
    else:
        # 用真实 airtest backend 跑（JOKER_BACKEND=airtest）
        os.environ["JOKER_BACKEND"] = "airtest"
        os.environ["JOKER_WINDOW"] = WINDOW_TITLE
        session = run_tests(
            test_paths=[str(p) for p in test_paths],
            backend_name="airtest",
            game_name="Shattered Pixel Dungeon",
        )

        # 报告
        json_rep = JsonReporter(str(REPORTS_DIR / "report.json"))
        html_rep = HtmlReporter(str(REPORTS_DIR / "report.html"))
        multi = MultiReporter([json_rep, html_rep])
        multi.on_session_start(session)
        for r in session.results:
            multi.on_test_end(r)
        multi.on_session_end(session)
        summary = multi.finalize()

        tracer.log_event("tests_executed", {
            "passed": session.passed, "failed": session.failed,
            "total": len(session.results),
        })
        print(f"✓ 执行完成：通过 {session.passed}，失败 {session.failed}")
        print(f"  报告：{summary}")


# === 结束 ===
print("\n" + "=" * 70)
print("端到端流程完成")
summary = tracer.finalize()
print(f"Trace 摘要：{json.dumps(summary, ensure_ascii=False, indent=2)}")
print("=" * 70)
