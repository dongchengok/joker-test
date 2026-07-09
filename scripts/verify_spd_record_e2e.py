"""SPD 端到端验证：LLMExplorer 探索录制 → LLM 起名 → 语义化 → 生成 test_case → 执行 → 报告。

复用已有的探索机制（LLMExplorer），不是手写操作。LLM 自主决策探索 SPD，
探索过程通过 GlobalRecorder 自动录制操作流，然后走完整闭环：
  探索录制 → FlowNamer 起名 → RecordedFlowGenerator 语义化+生成 test_case
  → write_tests_to_dir → run_tests → MultiReporter 报告

需要：
  1. SPD 已启动（或脚本自动启动）
  2. MiMo LLM 可用（.env 配好 MIMO API key）
  3. airtest + RapidOCR 已装

用法：
    python scripts/verify_spd_record_e2e.py
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

WINDOW_TITLE = "Shattered"
RUN_TS = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
OUTPUTS_DIR = REPO / "outputs" / f"verify_explore_{RUN_TS}"


def reset_spd() -> None:
    """重启 SPD 回主菜单（确保从初始状态开始）。"""
    import win32gui  # noqa: PLC0415

    subprocess.run(
        ["taskkill", "/F", "/IM", "Shattered Pixel Dungeon.exe"], capture_output=True
    )
    time.sleep(2)
    exe = str(REPO / ".test-targets" / "SPD" / "Shattered Pixel Dungeon.exe")
    subprocess.Popen([exe], cwd=os.path.dirname(exe))
    for _ in range(20):
        found = [False]

        def _check(h, _):  # noqa: ANN001
            if win32gui.IsWindowVisible(h) and WINDOW_TITLE in win32gui.GetWindowText(h):
                found[0] = True  # noqa: B023
            return True

        win32gui.EnumWindows(_check, None)
        if found[0]:
            break
        time.sleep(1)
    time.sleep(5)  # 等 LibGDX 标题画面过渡到主菜单


def make_llm():
    """构造带 trace 的 MiMo provider（TracingProvider 包装，自动记录 LLM 调用）。

    全局 trace 模式：不传 tracer，首次打点惰性建 Tracer，atexit 自动收尾。
    """
    from joker_test.llm.providers.anthropic import load_env  # noqa: PLC0415
    from joker_test.llm.providers.mimo import MiMoProvider  # noqa: PLC0415
    from joker_test.llm.providers.tracing import TracingProvider  # noqa: PLC0415

    cfg = load_env()
    model = cfg.get("MIMO_MODEL", "mimo-v2.5")
    return TracingProvider(MiMoProvider(), model=model)


print("=" * 70)
print("SPD 端到端验证：LLMExplorer 探索录制 → test_case → 执行 → 报告")
print(f"时间戳：{RUN_TS}")
print(f"输出：{OUTPUTS_DIR}")
print("=" * 70)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

# === 0. 重置 SPD ===
print("\n[0] 重置 SPD 到主菜单...")
reset_spd()
print("✓ SPD 就绪")

# === 1. LLMExplorer 探索 + 自动录制 ===
print("\n[1] LLMExplorer 探索 SPD（LLM 自主决策，自动录制操作流）...")
from joker_test.executor.backends.airtest import AirtestBackend  # noqa: E402
from joker_test.explorer.llm_explorer import LLMExplorer  # noqa: E402
from joker_test.flow import GlobalRecorder  # noqa: E402
from joker_test.ocr.providers.rapidocr import RapidOCRProvider  # noqa: E402

backend = AirtestBackend(window_title=WINDOW_TITLE, ocr=RapidOCRProvider())
recorder = GlobalRecorder(
    output_dir=OUTPUTS_DIR / "flow", backend=backend, pynput_mode=False
)
explorer = LLMExplorer(
    backend=backend,
    llm=make_llm(),
    max_steps=8,  # 探索 8 步上限（充分度判断会提前停：连续 3 步无新界面就停）
    max_stale_steps=3,
    screenshot_dir=str(OUTPUTS_DIR / "explore_screens"),
    recorder=recorder,  # 关键：探索过程自动录制
)

uimap = explorer.explore()
flow = recorder.stop()
flow_yaml = recorder.save_flow_yaml(flow)
backend.close()

print(f"✓ 探索完成：{len(uimap.screens)} 个界面，{sum(len(s.elements) for s in uimap.screens)} 个元素")
print(f"  界面: {explorer._screen_names}")  # noqa: SLF001
print(f"  录制操作流：{len(flow.steps)} 步")
for i, s in enumerate(flow.steps):
    detail = s.text or s.key or f"({s.x:.2f},{s.y:.2f})"
    print(f"    {i + 1}. {s.action}: {detail} ({s.note})")

if not flow.steps:
    print("\n✗ 探索未录到操作（LLM 可能直接 stop 或卡住），退出")
    sys.exit(1)

# === 2. LLM 起名 rename ===
print("\n[2] LLM 起名...")
from joker_test.flow import FlowNamer  # noqa: E402

namer = FlowNamer(make_llm())
name, desc = namer.name_flow(flow, recorder.flow_dir / "screenshots")
flow.name = name
flow.description = desc

ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
safe_name = name.replace("/", "_").replace(" ", "_")
final_dir = OUTPUTS_DIR / f"flow_{ts}_{safe_name}"
shutil.move(str(recorder.flow_dir), str(final_dir))

# 把探索截图复制到 flow 目录（语义化要读）
explore_screens = OUTPUTS_DIR / "explore_screens"
if explore_screens.is_dir():
    flow_shots = final_dir / "screenshots"
    flow_shots.mkdir(parents=True, exist_ok=True)
    for shot in explore_screens.glob("*.png"):
        shutil.copy2(str(shot), str(flow_shots / shot.name))

# 把 step 的 screenshot_after 改成相对路径（相对 final_dir，保证可移植）
for s in flow.steps:
    for field in ("screenshot_before", "screenshot_after"):
        val = getattr(s, field, None)
        if val:
            p = Path(val)
            if p.is_absolute():
                # 找到 final_dir 下同名的文件，改成相对路径
                name = p.name
                if (flow_shots / name).exists():
                    setattr(s, field, f"screenshots/{name}")

# 重写 flow.yaml（更新 name/description + 相对路径）
import yaml  # noqa: PLC0415

(final_dir / "flow.yaml").write_text(
    yaml.dump(flow.model_dump(), allow_unicode=True, default_flow_style=False, sort_keys=False),
    encoding="utf-8",
)
print(f"✓ 目录: {final_dir.name}")
print(f"  名称: {name}")
print(f"  说明: {desc}")

# === 3. 生成 test_case ===
print("\n[3] LLM 生成 test_case（坐标语义化 + 代码生成）...")
from joker_test.flow import RecordedFlowGenerator  # noqa: E402
from joker_test.generator.generator import write_tests_to_dir  # noqa: E402

game_meta_path = REPO / "examples" / "spd_game_metadata.json"
game_meta = (
    json.loads(game_meta_path.read_text(encoding="utf-8"))
    if game_meta_path.exists()
    else {"game_name": "Shattered Pixel Dungeon", "overview": "像素地牢"}
)

gen = RecordedFlowGenerator(provider=make_llm(), ocr_provider=RapidOCRProvider())
try:
    tests = gen.generate(flow, final_dir, game_meta)
except Exception as e:
    print(f"✗ 生成失败: {e}")
    import traceback  # noqa: PLC0415

    traceback.print_exc()
    sys.exit(1)

gen_dir = REPO / "tests" / "generated_smoke"
# 清理旧文件
for old in gen_dir.glob("test_*.py"):
    old.unlink()
for old in gen_dir.glob("*_spec.py"):
    old.unlink()
# 清理旧模板目录（已废弃模板匹配，改用 click_coord 归一化坐标）
gen_templates = gen_dir / "templates"
if gen_templates.is_dir():
    shutil.rmtree(gen_templates)
root_templates = REPO / "templates"
if root_templates.is_dir():
    shutil.rmtree(root_templates)

print(f"\n{'=' * 70}")
print(f"初次生成的 test_case（{len(tests)} 个文件）")
print(f"{'=' * 70}")
# 先落盘看一眼初次生成的代码
test_paths = write_tests_to_dir(tests, gen_dir)
for p in test_paths:
    print(f"\n--- {p.name}（初次生成）---")
    print(p.read_text(encoding="utf-8"))

# === 4. 试跑验证 + 回喂重写 ===
print(f"\n{'=' * 70}")
print("[4] 试跑验证 + 回喂重写（TestCaseVerifier）")
print(f"{'=' * 70}")
os.environ["JOKER_BACKEND"] = "airtest"
os.environ["JOKER_WINDOW"] = WINDOW_TITLE

from joker_test.flow import TestCaseVerifier  # noqa: E402

verifier = TestCaseVerifier(
    reset_fn=reset_spd,
    gen_dir=gen_dir,
    max_retries=2,
    backend_name="airtest",
    game_name="Shattered Pixel Dungeon",
)
tests, history = verifier.verify_and_fix(tests, gen, flow, final_dir, game_meta)

# 展示验证历史
print(f"\n验证历史（{len(history)} 轮）：")
for h in history:
    status = "✓ 全过" if h["failed"] == 0 else f"✗ {h['failed']} 失败"
    print(f"  第 {h['round']} 轮：通过 {h['passed']}/{h['total']} {status}")
    for err in h["errors"]:
        print(f"    └ {err['test']}: {err['error'][:100]}")

# === 5. 最终 test_case + 报告 ===
print(f"\n{'=' * 70}")
print("[5] 最终 test_case（验证后）")
print(f"{'=' * 70}")
# 落盘最终版本
for old in gen_dir.glob("test_*.py"):
    old.unlink()
for old in gen_dir.glob("*_spec.py"):
    old.unlink()
final_test_paths = write_tests_to_dir(tests, gen_dir)
for p in final_test_paths:
    print(f"\n--- {p.name}（最终版）---")
    print(p.read_text(encoding="utf-8"))

# 最终跑一遍出报告
reset_spd()
from joker_test.reporters import HtmlReporter, JsonReporter, MultiReporter  # noqa: E402
from joker_test.runner import run_tests  # noqa: E402

session = run_tests(
    test_paths=[str(p) for p in final_test_paths],
    backend_name="airtest",
    game_name="Shattered Pixel Dungeon",
)

report_dir = OUTPUTS_DIR / "reports"
multi = MultiReporter([
    JsonReporter(str(report_dir / "report.json")),
    HtmlReporter(str(report_dir / "report.html")),
])
multi.on_session_start(session)
for r in session.results:
    multi.on_test_end(r)
multi.on_session_end(session)
multi.finalize()

# === 5. 展示测试报告 ===
print(f"\n{'=' * 70}")
print("测试报告")
print(f"{'=' * 70}")
print(f"会话 ID: {session.id}")
print(f"游戏: {session.game}")
print(f"通过: {session.passed}，失败: {session.failed}，总计: {len(session.results)}")
print("\n用例明细:")
for r in session.results:
    status_icon = "✓" if r.status == "passed" else "✗"
    duration = f"{r.duration:.1f}s" if r.duration else "-"
    print(f"  {status_icon} {r.test.name} [{r.status}] ({duration})")
    if r.error:
        print(f"      错误: {r.error[:150]}")

print("\n报告文件:")
print(f"  JSON: {report_dir / 'report.json'}")
print(f"  HTML: {report_dir / 'report.html'}")

# 展示 report.json 顶层汇总
report_data = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
print(f"\nreport.json 顶层字段: {list(report_data.keys())}")
print(f"  passed={report_data.get('passed')}, failed={report_data.get('failed')}")

# === 结束 ===
print(f"\n{'=' * 70}")
print("端到端验证完成")
print(f"  探索: {len(uimap.screens)} 界面，录制 {len(flow.steps)} 步")
print(f"  名称: {name}")
print(f"  测试: {len(tests)} 文件，通过 {session.passed} 失败 {session.failed}")
print(f"  产物: {final_dir}")
print(f"  报告: {report_dir}")
print(f"{'=' * 70}")
