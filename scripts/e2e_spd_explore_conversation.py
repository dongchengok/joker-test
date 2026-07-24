"""端到端测试：用 AgentStrategy（agent loop + 工具化感知/执行）探索 SPD 到设置音量界面。

流程：启动 SPD → 原生 backend 连接 → AgentStrategy 探索
      （agent loop：LLM 按需调 get_screenshot/get_ocr_text/click/... 工具）→ 不固化 → 生成报告
游戏被误退出（如主菜单 escape）时自动重启续跑（LLMExplorer recovery 钩子）。

使用：python scripts/e2e_spd_explore_conversation.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from joker_test.config import load_config
from joker_test.executor import set_active_backend
from joker_test.executor.backends.factory import create_native_backend
from joker_test.executor.window import wait_for_window
from joker_test.explorer.agent_strategy import AgentStrategy
from joker_test.explorer.llm_explorer import LLMExplorer
from joker_test.flow.recorder import GlobalRecorder
from joker_test.llm.providers.anthropic import AnthropicProvider, load_env
from joker_test.ocr.providers.rapidocr import RapidOCRProvider
from joker_test.plugins.manager import PluginManager
from joker_test.plugins.ocr import OCRPlugin

# finish 完成验证门的判据文件（唯一事实来源，不信 LLM 自报）
_SPD_SETTINGS = Path.home() / "Library/Application Support/Shattered Pixel Dungeon/settings.xml"


def _music_vol() -> int | None:
    """读 SPD settings.xml 的 music_vol（不存在返回 None）。"""
    import re

    if not _SPD_SETTINGS.exists():
        return None
    m = re.search(
        r'<entry key="music_vol">(\d+)</entry>',
        _SPD_SETTINGS.read_text(encoding="utf-8"),
    )
    return int(m.group(1)) if m else None


def make_finish_gate() -> Callable[[], str | None]:
    """finish 完成验证门：goal_completed=true 时校验 settings.xml music_vol ∈ {4,5}。

    Returns:
        callable() -> str | None（None=通过，str=打回原因）。
    """

    def gate() -> str | None:
        vol = _music_vol()
        if vol in (4, 5):
            return None
        return f"settings.xml 的 music_vol={vol}（要求 4 或 5），目标实际未达成"

    return gate


def main() -> int:
    print("=" * 60)
    print("SPD 端到端探索测试（AgentStrategy / agent loop 模式）")
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
        """游戏被误退出（如主菜单 escape 退出进程）时重启续跑（recovery 钩子）。

        pkill 后等 5s 再启动：实测 pkill 后 2s 内重启 GLFW 显示器探测会偶发
        NPE（glfwGetMonitorPos 空指针）崩掉。启动后校验截图健康，不健康再试一次。
        """
        from joker_test.executor.coords import analyze_screenshot

        for attempt in range(2):
            print(f"⚠ 游戏窗口丢失，重启 SPD 续跑（第 {attempt + 1} 次）...")
            if sys.platform == "darwin":
                subprocess.run(["pkill", "-f", "DesktopLauncher"], capture_output=True)
                subprocess.run(["pkill", "-f", "run into an error"], capture_output=True)
                time.sleep(5)
                subprocess.Popen(["bash", str(REPO / "scripts" / "start_spd_mac.sh")])
            else:
                subprocess.run(
                    ["taskkill", "/F", "/IM", "Shattered Pixel Dungeon.exe"],
                    capture_output=True,
                )
                time.sleep(5)
                exe = str(REPO / ".test-targets" / "SPD" / "Shattered Pixel Dungeon.exe")
                subprocess.Popen([exe], cwd=str(Path(exe).parent))
            if not wait_for_window("Shattered", timeout=30.0):
                continue
            time.sleep(5)  # 等标题界面加载
            backend.connect()
            health = analyze_screenshot(backend.screenshot())
            if "失败" not in health and "异常" not in health:
                print("✓ SPD 已重启并重连")
                return
            print(f"⚠ 重启后画面不健康（{health}），再试一次")
        raise RuntimeError("SPD 重启失败（两次尝试后画面仍不健康）")

    intent = (
        "进入游戏设置界面，找到音频设置，将音乐音量从10降低到约5。"
        "注意：绝对不要点击全屏模式。SPD 的音量设置在音频设置里，不在显示设置里。"
        "如果表层界面没有设置入口，就向游戏更深处探索（游戏内通常有菜单）。"
        "完成后调用 finish(goal_completed=true)。"
    )
    flow_dir = REPO / "flows" / f"e2e_agent_{int(time.time())}"
    flow_dir.mkdir(parents=True, exist_ok=True)

    recorder = GlobalRecorder(output_dir=flow_dir, backend=backend, pynput_mode=False)
    recorder.start()

    # OCRPlugin：提供 OCR 文字+坐标注入 + 语义变化校验（判断操作是否有效）
    plugin_manager = PluginManager([OCRPlugin()])

    strategy = AgentStrategy(
        llm=provider,
        intent=intent,
        max_conversation_tokens=16000,
        plugin_manager=plugin_manager,
        finish_gate=make_finish_gate(),
    )

    explorer = LLMExplorer(
        backend=backend,
        llm=provider,
        strategy=strategy,
        max_steps=30,
        recorder=recorder,
        screenshot_dir=flow_dir / "screenshots",
        plugin_manager=plugin_manager,
        recovery=restart_spd,
    )

    print(f"\n探索意图: {intent}")
    print("策略: agent（工具化感知/执行，agent loop）")
    print("最大步数: 30")
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

    # 生成报告（含 settings.xml 判据——唯一事实来源）
    final_vol = _music_vol()
    criterion_met = final_vol in (4, 5)
    report = {
        "intent": intent,
        "strategy": "agent",
        "screens_found": len(state_map.screens),
        "screen_names": [s.name or s.id for s in state_map.screens],
        "flow_steps": len(flow.steps),
        "flow_dir": str(flow_dir),
        "criterion": {"music_vol": final_vol, "met": criterion_met},
    }

    report_path = flow_dir / "explore_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n✓ 报告 → {report_path}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(
        f"\n判据：settings.xml music_vol={final_vol} → "
        + ("✓ 达成（4/5）" if criterion_met else "✗ 未达成（要求 4 或 5）")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
