"""真 SPD 端到端 MVP（DESIGN 阶段 1 完成标志）。

用真实 Shattered Pixel Dungeon 游戏（不是 FakeBackend 模拟）：
1. 启动 SPD（脚本假设已启动，窗口标题 "Shattered Pixel Dungeon"）
2. AirtestBackend + RapidOCR 探索主菜单（max_depth=1，避开 G7 click 空帧）
3. MiMo（或 GLM）生成 pytest testcase
4. runner 执行（FakeBackend fixture，预期场景不匹配的会失败——如实记录）
5. JSON + HTML 报告

这验证 DESIGN 阶段 1："端到端 demo 能跑通"。
诚实边界：真游戏探索 + LLM 生成成功；执行阶段 fixture 场景与真游戏可能不匹配，
部分测试会失败——这是真实的 fixture 一致性问题，报告如实记录。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LLM_CHOICE = os.environ.get("JOKER_LLM", "mimo").lower()

# 等游戏窗口就绪
print("等待 SPD 窗口...")
import win32gui  # noqa: E402

_FOUND = [False]
def _check_window(h, _):  # noqa: ANN001
    if win32gui.IsWindowVisible(h) and "Shattered" in win32gui.GetWindowText(h):
        _FOUND[0] = True
    return True

for _ in range(10):
    _FOUND[0] = False
    win32gui.EnumWindows(_check_window, None)
    if _FOUND[0]:
        print("✓ SPD 窗口已就绪")
        break
    time.sleep(1)
else:
    print("✗ SPD 未启动，请先运行 .test-targets/SPD/'Shattered Pixel Dungeon.exe'")
    sys.exit(1)

# Step 1: 提示词配置
print("\n" + "=" * 60)
print("Step 1: 读取测试场景配置")
print("=" * 60)
game_meta_path = REPO / "examples" / "e2e_launch_quit" / "game_metadata.json"
game_meta = json.loads(game_meta_path.read_text(encoding="utf-8"))
print(f"游戏: {game_meta['game_name']}")

# Step 2: 真 SPD 探索（AirtestBackend + RapidOCR）
print("\n" + "=" * 60)
print("Step 2: 真 SPD 界面探索（AirtestBackend + RapidOCR）")
print("=" * 60)
from joker_test.executor.backends.airtest import AirtestBackend  # noqa: E402
from joker_test.explorer import UIExplorer  # noqa: E402
from joker_test.ocr.providers.rapidocr import RapidOCRProvider  # noqa: E402

backend = AirtestBackend(window_title="Shattered", ocr=RapidOCRProvider())
# max_depth=1：只探索根界面（不点按钮触发切屏，避开 G7 click 后空帧）
explorer = UIExplorer(
    backend, max_depth=1, max_screens=3, screen_change_timeout=5.0,
)
uimap = explorer.explore()
print(f"发现 {len(uimap.screens)} 个界面")
for s in uimap.screens:
    texts = [e.text for e in s.elements if e.text][:8]
    print(f"  [{s.id}] 识别到 {len(s.elements)} 个元素，文本样本: {texts}")

# 保存真游戏 UIMap
uimap_out = REPO / "reports" / "e2e_spd_real" / "spd_uimap.json"
uimap_out.parent.mkdir(parents=True, exist_ok=True)
uimap_out.write_text(uimap.model_dump_json(indent=2), encoding="utf-8")
print(f"UIMap 已保存: {uimap_out}")

# Step 3: LLM 生成 testcase
print("\n" + "=" * 60)
print(f"Step 3: LLM 生成 testcase（{LLM_CHOICE}）")
print("=" * 60)
from joker_test.generator import SmokeTestGenerator, write_tests_to_dir  # noqa: E402

if LLM_CHOICE == "glm":
    from joker_test.llm.providers.glm import GLMProvider  # noqa: E402
    provider = GLMProvider()
else:
    from joker_test.llm.providers.mimo import MiMoProvider  # noqa: E402
    provider = MiMoProvider()
print(f"provider: {type(provider).__name__} (model={provider._model})")  # noqa: SLF001

gen = SmokeTestGenerator(provider)
try:
    tests = gen.generate(uimap, game_meta)
except Exception as e:
    print(f"生成失败: {e}")
    raise

gen_dir = REPO / "tests" / "generated_smoke"
gen_paths = write_tests_to_dir(tests, str(gen_dir))
print(f"生成 {len(tests)} 份 testcase")
for t in tests:
    print(f"  - {t.test_filename}（{len(t.test_code.splitlines())} 行）")

# Step 4: 执行 testcase
print("\n" + "=" * 60)
print("Step 4: 执行 testcase（FakeBackend fixture）")
print("=" * 60)
from joker_test.runner import run_tests  # noqa: E402

test_paths = [str(p) for p in gen_paths]
session = run_tests(test_paths, backend_name="fake", game_name="SPD-真探索")
print(f"执行完成：通过 {session.passed}，失败 {session.failed}，共 {len(session.results)}")
for r in session.results:
    icon = "✓" if r.status == "passed" else "✗"
    print(f"  {icon} {r.test.name} [{r.status}]")

# Step 5: 报告
print("\n" + "=" * 60)
print("Step 5: 生成测试报告")
print("=" * 60)
from joker_test.reporters import HtmlReporter, JsonReporter, MultiReporter  # noqa: E402

report_dir = REPO / "reports" / "e2e_spd_real"
multi = MultiReporter([
    JsonReporter(report_dir / "report.json"),
    HtmlReporter(report_dir / "report.html"),
])
multi.on_session_start(session)
for r in session.results:
    multi.on_test_end(r)
multi.on_session_end(session)
multi.finalize()

print(f"\n报告: {report_dir}")
print("  - report.json（机器可读）")
print("  - report.html（人类可读）")
print("  - spd_uimap.json（真探索界面地图）")

print("\n" + "=" * 60)
print("真 SPD 端到端 MVP 完成")
print("=" * 60)
print(f"真游戏探索: {len(uimap.screens)} 界面, {sum(len(s.elements) for s in uimap.screens)} 元素")
print(f"LLM 生成: {len(tests)} testcase ({provider._model})")  # noqa: SLF001
print(f"执行: {session.passed} 通过 / {session.failed} 失败")
