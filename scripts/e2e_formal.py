"""正式端到端流程：LLM 生成 testcase → pytest 真执行 → 测试报告。

完整流程（用户要求）：
1. 多层识别探索真 SPD（OCR + LLM 识图）
2. MiMo 生成 testcase（改进 prompt：状态管理 + 鲁棒断言）
3. pytest 真执行（JOKER_BACKEND=airtest，含选择角色界面）
4. 生成 JSON + HTML 报告

前置：SPD 已启动 + .env 配好 MIMO_API_KEY
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.environ.setdefault("JOKER_BACKEND", "native")

# Step 0: 确认 SPD
print("=" * 60)
print("正式端到端：LLM 生成 → pytest 真执行 → 报告")
print("=" * 60)
from joker_test.executor.window import wait_for_window  # noqa: E402

if not wait_for_window("Shattered", timeout=10.0):
    print("✗ SPD 未启动！")
    sys.exit(1)
print("✓ SPD 运行中")

# Step 1: 多层识别探索
print("\n[1/4] 多层识别探索真 SPD（OCR + LLM 识图）...")
from joker_test.executor.backends.factory import create_native_backend  # noqa: E402
from joker_test.llm.providers.anthropic import AnthropicProvider
from joker_test.ocr.providers.rapidocr import RapidOCRProvider  # noqa: E402
from joker_test.perception import PerceptionEngine  # noqa: E402

mimo = AnthropicProvider(max_tokens=4096)
ocr = RapidOCRProvider()
backend = create_native_backend(window_title="Shattered", ocr=ocr)
backend.connect()
print("✓ 已连接 SPD")

# 探索主菜单——用直接截图+OCR 构造 UIMap（不走 UIExplorer 的 DFS，
# 因为 DFS 的 backtrack 会 press_key("escape")，SPD 主菜单 escape 可能关游戏）
from joker_test.explorer.detection import compute_fingerprint  # noqa: E402
from joker_test.explorer.types import Screen, UIElement, UIMap  # noqa: E402

time.sleep(1)
frame = backend.screenshot()
ocr_results = ocr.readtext(frame)
elements = [
    UIElement(type="button", text=r.text, bbox=r.bbox)
    for r in ocr_results if r.text
]
uimap = UIMap(
    screens=[Screen(
        id="root", elements=elements, exits=[],
        entry=None, fingerprint=compute_fingerprint(elements),
    )],
    root_screen_id="root",
    explored_at="2026-07-07",
    backend_info={"type": f"{type(backend).__name__}+RapidOCR"},
)
print(f"✓ OCR 探索：{len(uimap.screens)} 界面, {len(elements)} 元素")
print(f"  OCR 文本: {[e.text[:30] for e in elements[:6]]}")

# LLM 识图补充理解（多层识别）——探索后等窗口稳定再截
time.sleep(2)  # 等窗口从前台切回稳定
frame = None
for _attempt in range(5):
    frame = backend.screenshot()
    if frame.size > 0:
        break
    time.sleep(0.5)
engine = PerceptionEngine(ocr=ocr, llm=mimo, use_llm=True)
perception = engine.perceive(frame, "这是游戏的主菜单界面。请描述界面布局和可交互元素。")
print(f"✓ LLM 识图：{perception.description[:80]}")
print(f"  LLM 识别元素: {len(perception.ui_elements)} 个")

# 保存 UIMap
uimap_path = REPO / "reports" / "e2e_formal" / "uimap.json"
uimap_path.parent.mkdir(parents=True, exist_ok=True)
uimap_path.write_text(uimap.model_dump_json(indent=2), encoding="utf-8")

# Step 2: MiMo 生成 testcase
print("\n[2/4] MiMo 生成 testcase（改进 prompt：状态管理 + 鲁棒断言）...")
from joker_test.generator import SmokeTestGenerator, write_tests_to_dir  # noqa: E402

game_meta = json.loads(
    (REPO / "examples" / "e2e_launch_quit" / "game_metadata.json").read_text(encoding="utf-8")
)
gen = SmokeTestGenerator(mimo)
try:
    tests = gen.generate(uimap, game_meta)
except Exception as e:
    print(f"生成失败: {e}")
    raise
gen_dir = REPO / "tests" / "generated_smoke"
gen_paths = write_tests_to_dir(tests, str(gen_dir))
print(f"✓ 生成 {len(tests)} 份 testcase")

# 看生成的测试预览
for t in tests:
    print(f"  {t.test_filename}（{len(t.test_code.splitlines())} 行）:")
    for line in t.test_code.splitlines():
        if line.strip().startswith("def test_"):
            print(f"    {line.strip()}")

# Step 3: pytest 真执行
print("\n[3/4] pytest 真执行（JOKER_BACKEND=native，真 SPD）...")
from joker_test.runner import run_tests  # noqa: E402

# 执行生成的测试 + 手写真测试
test_paths = [str(p) for p in gen_paths] + ["tests/real/"]
session = run_tests(test_paths, backend_name="native", game_name="SPD-正式端到端")
print(f"✓ 执行完成：通过 {session.passed}，失败 {session.failed}，共 {len(session.results)}")
for r in session.results:
    icon = "✓" if r.status == "passed" else "✗"
    err = (r.error or "")[:100] if r.status == "failed" else ""
    print(f"  {icon} {r.test.name} [{r.status}] {err}")

# Step 4: 报告
print("\n[4/4] 生成测试报告...")
from joker_test.reporters import HtmlReporter, JsonReporter, MultiReporter  # noqa: E402

report_dir = REPO / "reports" / "e2e_formal"
multi = MultiReporter([
    JsonReporter(report_dir / "report.json"),
    HtmlReporter(report_dir / "report.html"),
])
multi.on_session_start(session)
for r in session.results:
    multi.on_test_end(r)
multi.on_session_end(session)
multi.finalize()

print(f"\n{'=' * 60}")
print("正式端到端完成")
print(f"{'=' * 60}")
print(f"  探索: OCR {len(uimap.screens)} 界面 + LLM 识图 {len(perception.ui_elements)} 元素")
print(f"  生成: MiMo 生成 {len(tests)} testcase")
print(f"  执行: {session.passed} 通过 / {session.failed} 失败（真 SPD）")
print(f"  报告: {report_dir}/report.json + report.html")
