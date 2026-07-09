"""端到端测试流程（GLM 驱动）：进入退出游戏。

5 步串起：
1. 提示词配置（examples/e2e_launch_quit/game_metadata.json）
2. 探索器产出 UIMap（FakeBackend 模拟"主菜单→游戏→返回"场景）
3. GLMProvider 生成 pytest testcase
4. runner 执行 testcase
5. reporters 出 JSON + HTML 报告

用法：python scripts/e2e_launch_quit.py
（需先配好 .env 的 ZHIPU_API_KEY）
"""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 选择 LLM provider（通过环境变量 JOKER_LLM=glm|mimo，默认 mimo 因为它是全模态）
LLM_CHOICE = os.environ.get("JOKER_LLM", "mimo").lower()

# Step 1: 读提示词配置
print("=" * 60)
print("Step 1: 读取测试场景配置")
print("=" * 60)
game_meta_path = REPO / "examples" / "e2e_launch_quit" / "game_metadata.json"
game_meta = json.loads(game_meta_path.read_text(encoding="utf-8"))
print(f"游戏: {game_meta['game_name']}")
print(f"测试焦点: {len(game_meta['test_focus'])} 条")
for f in game_meta["test_focus"]:
    print(f"  - {f}")

# Step 2: 探索器产出 UIMap（FakeBackend 模拟主菜单↔游戏场景）
print("\n" + "=" * 60)
print("Step 2: 探索游戏界面（FakeBackend 模拟进入退出场景）")
print("=" * 60)
from joker_test.executor.backends.fake import FakeBackend, ScreenCfg
from joker_test.executor.base import BBox
from joker_test.explorer import UIExplorer

backend = FakeBackend(
    screens={
        "main_menu": ScreenCfg(
            texts_map={
                "Start": BBox(0.4, 0.5, 0.2, 0.08),
                "About": BBox(0.4, 0.65, 0.2, 0.05),
                "Settings": BBox(0.4, 0.75, 0.2, 0.05),
            },
            bg_pixel=(15, 25, 35),
        ),
        "game": ScreenCfg(
            texts_map={
                "Resume": BBox(0.4, 0.4, 0.2, 0.08),
                "Main Menu": BBox(0.4, 0.55, 0.2, 0.05),
            },
            bg_pixel=(80, 50, 30),
        ),
    },
    transitions={
        ("main_menu", "Start"): "game",
        ("game", "Main Menu"): "main_menu",
        ("game", "key@escape"): "main_menu",
    },
    initial_screen="main_menu",
)
explorer = UIExplorer(backend, max_depth=3, screen_change_timeout=0.5)
uimap = explorer.explore()
print(f"发现 {len(uimap.screens)} 个界面")
for s in uimap.screens:
    texts = [e.text for e in s.elements if e.text]
    print(f"  [{s.id}] 按钮: {texts}，exits: {len(s.exits)}")

# Step 3: GLMProvider 生成 testcase
print("\n" + "=" * 60)
print("Step 3: GLM 生成 pytest testcase（真 LLM）")
print("=" * 60)
from joker_test.generator import SmokeTestGenerator, write_tests_to_dir
from joker_test.llm.providers.glm import GLMProvider
from joker_test.llm.providers.mimo import MiMoProvider

gen_dir = REPO / "tests" / "generated_smoke"
# 选择 provider（默认 MiMo 全模态，可切 GLM）
if LLM_CHOICE == "glm":
    provider = GLMProvider()
    print(f"使用 GLMProvider（模型: {provider._model}）")  # noqa: SLF001
else:
    provider = MiMoProvider()
    print(f"使用 MiMoProvider（模型: {provider._model}，全模态）")  # noqa: SLF001
gen = SmokeTestGenerator(provider)
try:
    tests = gen.generate(uimap, game_meta)
except Exception as e:
    print(f"GLM 生成失败: {e}")
    print("（可能是 GLM 回复格式不符合代码块解析，检查 prompt 模板）")
    raise

gen_paths = write_tests_to_dir(tests, str(gen_dir))
print(f"生成 {len(tests)} 份 testcase，落盘到 {gen_dir}")
for t in tests:
    print(f"  - {t.test_filename}（{len(t.test_code.splitlines())} 行）")

# Step 4: runner 执行 testcase
print("\n" + "=" * 60)
print("Step 4: 执行 testcase")
print("=" * 60)
from joker_test.runner import run_tests

# 执行生成的 + 手写 smoke
test_paths = [str(p) for p in gen_paths] + ["tests/smoke"]
session = run_tests(test_paths, backend_name="fake", game_name=game_meta["game_name"])
print(f"执行完成：通过 {session.passed}，失败 {session.failed}，共 {len(session.results)}")
for r in session.results:
    status_icon = "✓" if r.status == "passed" else "✗"
    print(f"  {status_icon} {r.test.module}.{r.test.name} [{r.status}]")

# Step 5: 生成报告
print("\n" + "=" * 60)
print("Step 5: 生成测试报告")
print("=" * 60)
from joker_test.reporters import HtmlReporter, JsonReporter, MultiReporter

report_dir = REPO / "reports" / "e2e_launch_quit"
multi = MultiReporter([
    JsonReporter(report_dir / "report.json"),
    HtmlReporter(report_dir / "report.html"),
])
multi.on_session_start(session)
for r in session.results:
    multi.on_test_end(r)
multi.on_session_end(session)
summary = multi.finalize()
print(f"报告已生成:\n{summary}")

print("\n" + "=" * 60)
print("端到端流程完成")
print("=" * 60)
print(f"游戏: {game_meta['game_name']}")
print(f"探索: {len(uimap.screens)} 界面")
print(f"生成: {len(tests)} testcase（GLM 驱动）")
print(f"执行: {session.passed} 通过 / {session.failed} 失败")
print(f"报告: {report_dir}")
