"""端到端测试：用 ConversationStrategy（对齐 Open-AutoGLM）探索 SPD 到设置音量界面。

流程：启动 SPD → 原生 backend 连接 → ConversationStrategy 探索
      （截图→perception→ReAct决策→执行→重复）→ 不固化 → 生成报告
游戏被误退出（如主菜单 escape）时自动重启续跑（LLMExplorer recovery 钩子）。

使用：python scripts/e2e_spd_explore_conversation.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from joker_test.config import load_config
from joker_test.executor import set_active_backend
from joker_test.executor.backends.factory import create_native_backend
from joker_test.executor.window import wait_for_window
from joker_test.explorer.conversation_strategy import ConversationStrategy
from joker_test.explorer.llm_explorer import LLMExplorer
from joker_test.flow.recorder import GlobalRecorder
from joker_test.llm.providers.anthropic import AnthropicProvider, load_env
from joker_test.ocr.providers.rapidocr import RapidOCRProvider
from joker_test.plugins.manager import PluginManager
from joker_test.plugins.ocr import OCRPlugin


def main() -> int:
    print("=" * 60)
    print("SPD 端到端探索测试（ConversationStrategy / Open-AutoGLM 模式）")
    print("=" * 60)

    cfg = load_env()
    model = cfg.get("MIMO_MODEL", "mimo-v2.5")
    print(f"LLM: AnthropicProvider model={model}")

    # 从配置文件读取 thinking 设置（默认 enabled=true, budget_tokens=8000）
    config = load_config()
    thinking_cfg = config.get("llm", {}).get("thinking", {})
    thinking_enabled = thinking_cfg.get("enabled", True)
    thinking_budget = thinking_cfg.get("budget_tokens", 8000)
    print(f"Thinking: enabled={thinking_enabled}, budget_tokens={thinking_budget}")

    # 用内置 trace，让 trace 记录 LLM 调用
    provider = AnthropicProvider(
        thinking_enabled=thinking_enabled,
        thinking_budget_tokens=thinking_budget,
    )

    ocr = RapidOCRProvider()

    window_title = "Shattered Pixel Dungeon"
    print(f"连接 SPD: {window_title} ...")
    backend = create_native_backend(window_title=window_title, ocr=ocr)
    backend.connect()
    set_active_backend(backend)
    print("✓ SPD 已连接")

    def restart_spd() -> None:
        """游戏被误退出（如主菜单 escape 退出进程）时重启续跑（recovery 钩子）。"""
        print("⚠ 游戏窗口丢失，重启 SPD 续跑...")
        if sys.platform == "darwin":
            subprocess.run(["pkill", "-f", "DesktopLauncher"], capture_output=True)
            time.sleep(2)
            subprocess.Popen(["bash", str(REPO / "scripts" / "start_spd_mac.sh")])
        else:
            subprocess.run(
                ["taskkill", "/F", "/IM", "Shattered Pixel Dungeon.exe"],
                capture_output=True,
            )
            time.sleep(2)
            exe = str(REPO / ".test-targets" / "SPD" / "Shattered Pixel Dungeon.exe")
            subprocess.Popen([exe], cwd=str(Path(exe).parent))
        if not wait_for_window("Shattered", timeout=30.0):
            raise RuntimeError("SPD 重启失败（窗口未出现）")
        time.sleep(5)  # 等标题界面加载
        backend.connect()
        print("✓ SPD 已重启并重连")

    intent = (
        "进入游戏设置界面，找到音频设置，将音乐音量从10降低到约5。"
        "注意：绝对不要点击全屏模式。SPD 的音量设置在音频设置里，不在显示设置里。"
        "完成后输出 goal_completed=true。"
    )
    flow_dir = REPO / "flows" / f"e2e_conversation_{int(time.time())}"
    flow_dir.mkdir(parents=True, exist_ok=True)

    recorder = GlobalRecorder(output_dir=flow_dir, backend=backend, pynput_mode=False)
    recorder.start()

    # OCRPlugin：提供 OCR 文字+坐标注入 + 语义变化校验（判断操作是否有效）
    plugin_manager = PluginManager([OCRPlugin()])

    strategy = ConversationStrategy(
        llm=provider,
        intent=intent,
        max_conversation_tokens=16000,
        plugin_manager=plugin_manager,
    )

    explorer = LLMExplorer(
        backend=backend,
        llm=provider,
        strategy=strategy,
        max_steps=20,
        recorder=recorder,
        screenshot_dir=flow_dir / "screenshots",
        plugin_manager=plugin_manager,
        recovery=restart_spd,
    )

    print(f"\n探索意图: {intent}")
    print("策略: conversation（对齐 Open-AutoGLM）")
    print("最大步数: 20")
    print("-" * 60)

    try:
        state_map = explorer.explore()
        print("\n✓ 探索完成")
        print(f"  发现界面: {len(state_map.screens)}")
        for s in state_map.screens:
            print(f"    - {s.name or s.id} ({len(s.elements)} 元素)")
    except Exception as e:
        import traceback

        print(f"\n✗ 探索失败: {e}")
        traceback.print_exc()
        backend.close()
        return 1

    flow = recorder.stop()
    if flow.steps:
        recorder.save_flow_yaml(flow)
        print(f"\n✓ 录制 {len(flow.steps)} 步 → {flow_dir}")
    else:
        print("\n⚠ 未录到操作")

    backend.close()

    # 等 trace 写盘
    from joker_test.trace import trace_finalize

    trace_finalize()

    # 生成报告
    report = {
        "intent": intent,
        "strategy": "conversation",
        "screens_found": len(state_map.screens),
        "screen_names": [s.name or s.id for s in state_map.screens],
        "flow_steps": len(flow.steps),
        "flow_dir": str(flow_dir),
    }

    report_path = flow_dir / "explore_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n✓ 报告 → {report_path}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
