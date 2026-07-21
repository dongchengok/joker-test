"""完整真游戏端到端：MiMo + 真 SPD（探索+生成+真执行+报告）。

与 e2e_spd_real.py 的关键差异：执行阶段用真 AirtestBackend（不是 FakeBackend）。
通过设置 JOKER_BACKEND=airtest 让 pytest fixture 连真 SPD。

前置：
1. SPD 已启动（窗口标题含 "Shattered"）
2. .env 配好 MIMO_API_KEY
3. pip install -e .[airtest,ocr]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 确保生成的测试执行时用真 airtest backend
os.environ.setdefault("JOKER_BACKEND", "native")
os.environ.setdefault("JOKER_LLM", "mimo")

print("=" * 60)
print("完整真游戏端到端：MiMo + 真 SPD")
print("=" * 60)

# Step 1: 确认 SPD 在运行
print("\n[0/5] 确认 SPD 运行中...")
from joker_test.executor.window import wait_for_window  # noqa: E402

if not wait_for_window("Shattered", timeout=10.0):
    print("✗ SPD 未启动（Mac: java -jar ShatteredPD.jar；Win: .test-targets/SPD/*.exe）")
    sys.exit(1)
print("✓ SPD 运行中")

# Step 2: 真 SPD 探索
print("\n[1/5] 真 SPD 界面探索（AirtestBackend + RapidOCR）...")
from joker_test.executor.backends.factory import create_native_backend  # noqa: E402
from joker_test.explorer import UIExplorer  # noqa: E402
from joker_test.ocr.providers.rapidocr import RapidOCRProvider  # noqa: E402

backend = create_native_backend(window_title="Shattered", ocr=RapidOCRProvider())
backend.connect()
explorer = UIExplorer(backend, max_depth=1, max_screens=3, screen_change_timeout=5.0)
uimap = explorer.explore()
print(f"✓ 发现 {len(uimap.screens)} 界面, {sum(len(s.elements) for s in uimap.screens)} 元素")

# 保存 UIMap
uimap_path = REPO / "reports" / "e2e_spd_full_real" / "uimap.json"
uimap_path.parent.mkdir(parents=True, exist_ok=True)
uimap_path.write_text(uimap.model_dump_json(indent=2), encoding="utf-8")

# Step 3: MiMo 生成 testcase
print("\n[2/5] MiMo 生成 testcase...")
from joker_test.generator import SmokeTestGenerator, write_tests_to_dir  # noqa: E402
from joker_test.llm.providers.anthropic import AnthropicProvider

game_meta = json.loads(
    (REPO / "examples" / "e2e_launch_quit" / "game_metadata.json").read_text(encoding="utf-8")
)
gen = SmokeTestGenerator(AnthropicProvider())
tests = gen.generate(uimap, game_meta)
gen_dir = REPO / "tests" / "generated_smoke"
gen_paths = write_tests_to_dir(tests, str(gen_dir))
print(f"✓ 生成 {len(tests)} 份 testcase")

# Step 4: 真执行（关键：JOKER_BACKEND=native 让 pytest 连真 SPD）
print("\n[3/5] 真游戏执行（JOKER_BACKEND=native，pytest 连真 SPD）...")
print("  注意：测试在真游戏上跑，可能因 G7 截图/OCR 延迟有不确定性")
from joker_test.runner import run_tests  # noqa: E402

test_paths = [str(p) for p in gen_paths]
session = run_tests(test_paths, backend_name="native", game_name="SPD-真执行")
print(f"✓ 执行完成：通过 {session.passed}，失败 {session.failed}，共 {len(session.results)}")
for r in session.results:
    icon = "✓" if r.status == "passed" else "✗"
    err_preview = (r.error or "")[:80] if r.error else ""
    print(f"  {icon} {r.test.name} [{r.status}] {err_preview}")

# Step 5: 报告
print("\n[4/5] 生成报告...")
from joker_test.reporters import HtmlReporter, JsonReporter, MultiReporter  # noqa: E402

report_dir = REPO / "reports" / "e2e_spd_full_real"
multi = MultiReporter([
    JsonReporter(report_dir / "report.json"),
    HtmlReporter(report_dir / "report.html"),
])
multi.on_session_start(session)
for r in session.results:
    multi.on_test_end(r)
multi.on_session_end(session)
multi.finalize()

print("\n[5/5] 完成")
print(f"  UIMap: {uimap_path}")
print(f"  报告: {report_dir}/report.json + report.html")
print(f"  结果: {session.passed} 通过 / {session.failed} 失败（真 SPD + MiMo 全程驱动）")
