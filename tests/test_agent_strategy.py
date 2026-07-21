"""AgentStrategy agent loop 测试（scripted LLM + FakeBackend）。"""
from __future__ import annotations

from typing import Any

from joker_test.executor.backends.fake import FakeBackend, ScreenCfg
from joker_test.executor.base import BBox
from joker_test.explorer.agent_strategy import AgentStrategy
from joker_test.explorer.strategy import ActionResult, ExploreContext, StepDecision
from joker_test.explorer.types import StateMap


class _ScriptedLLM:
    """按队列返回预设回复的 LLM（测试用）。队列耗尽后返回纯文本（终止 loop）。"""

    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self._replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if not self._replies:
            return {"content": [{"type": "text", "text": "（无更多回复）"}]}
        return self._replies.pop(0)


class _FailingLLM:
    """create 恒抛异常的 LLM（测试用）。"""

    def create(self, **kwargs: Any) -> dict[str, Any]:
        raise ConnectionError("endpoint down")


def _tool_reply(*calls: tuple[str, dict]) -> dict[str, Any]:
    """构造含 tool_use blocks 的回复。"""
    return {
        "content": [
            {"type": "tool_use", "id": f"toolu_{i}", "name": name, "input": inp}
            for i, (name, inp) in enumerate(calls)
        ]
    }


def _text_reply(text: str = "看一下界面") -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _make_backend() -> FakeBackend:
    backend = FakeBackend(texts_map={"设置": BBox(0.4, 0.4, 0.2, 0.1)})
    backend.connect()
    return backend


def _make_multi_screen_backend() -> FakeBackend:
    """两屏状态机：click_text("设置") 从 root 切到 settings（背景色不同 → screen_changed）。"""
    backend = FakeBackend(
        screens={
            "root": ScreenCfg(texts_map={"设置": BBox(0.4, 0.4, 0.2, 0.1)}, bg_pixel=(50, 50, 50)),
            "settings": ScreenCfg(texts_map={"音频": BBox(0.4, 0.3, 0.2, 0.1)}, bg_pixel=(200, 0, 0)),
        },
        transitions={("root", "设置"): "settings"},
        initial_screen="root",
    )
    backend.connect()
    return backend


def _ctx(backend: FakeBackend, llm: Any, step: int = 0) -> ExploreContext:
    return ExploreContext(
        step=step, max_steps=30, intent="进入设置界面",
        backend=backend, llm=llm, recorder=None,
    )


def _decide(strategy: AgentStrategy, backend: FakeBackend, llm: Any, step: int = 0) -> StepDecision:
    return strategy.decide(backend.screenshot(), None, _ctx(backend, llm, step))


def test_multi_round_loop_executes_in_order_and_finish() -> None:
    """多轮 loop：get_ocr_text → click → finish 顺序执行，finish 终止并给 stop 决策。"""
    backend = _make_backend()
    llm = _ScriptedLLM([
        _tool_reply(("get_ocr_text", {})),
        _tool_reply(("click", {"x": 0.5, "y": 0.45})),
        _tool_reply(("finish", {"goal_completed": True, "summary": "已进入设置"})),
    ])
    strategy = AgentStrategy(llm=llm, intent="进入设置界面")

    decision = _decide(strategy, backend, llm)

    assert len(llm.calls) == 3  # 三轮 LLM 调用
    assert backend.click_history == [("click", (0.5, 0.45))]  # 动作在 loop 内执行
    assert decision.action == "stop" and decision.stop is True
    assert decision.goal_completed is True
    assert decision.think == "已进入设置"
    assert strategy.should_stop() is True
    # 每轮 create 都带 tools + auto
    assert llm.calls[0]["tools"] is not None
    assert llm.calls[0]["tool_choice"] == {"type": "auto"}


def test_single_round_multiple_tool_calls_merge_one_user_message() -> None:
    """单轮多个 tool_use：都执行，tool_result 合并进同一条 user 消息。"""
    backend = _make_backend()
    llm = _ScriptedLLM([
        _tool_reply(
            ("click", {"x": 0.5, "y": 0.45}),
            ("press_key", {"key": "enter"}),
        ),
        _tool_reply(("finish", {"goal_completed": True, "summary": "完成"})),
    ])
    strategy = AgentStrategy(llm=llm, intent="进入设置界面")

    decision = _decide(strategy, backend, llm)

    assert decision.stop is True
    assert backend.click_history == [("click", (0.5, 0.45))]
    assert backend.key_history == ["enter"]
    # 找含 2 个 tool_result 的 user 消息
    merged = [
        msg for msg in strategy._messages
        if msg["role"] == "user"
        and sum(1 for b in msg["content"] if b.get("type") == "tool_result") == 2
    ]
    assert len(merged) == 1
    ids = {b["tool_use_id"] for b in merged[0]["content"]}
    assert ids == {"call_1", "call_2"}  # tool_use id 被重写为全历史唯一


def test_tool_use_ids_rewritten_unique() -> None:
    """LLM 回复的 tool_use id 重复时（kimi 端点 name:N 计数会撞 id），
    历史中的 id 必须被重写为唯一，且 tool_result 引用重写后的 id。"""
    backend = _make_backend()
    dup_reply = {
        "content": [
            {"type": "tool_use", "id": "get_ocr_text:1", "name": "get_ocr_text", "input": {}},
        ]
    }
    llm = _ScriptedLLM([
        dup_reply,
        dict(dup_reply),  # 第二轮同样的 id
        _tool_reply(("finish", {"goal_completed": True, "summary": "完成"})),
    ])
    strategy = AgentStrategy(llm=llm, intent="进入设置界面")

    _decide(strategy, backend, llm)

    assistant_ids = [
        b["id"]
        for msg in strategy._messages
        if msg["role"] == "assistant"
        for b in msg["content"]
        if b.get("type") == "tool_use"
    ]
    assert len(assistant_ids) == len(set(assistant_ids)), "assistant tool_use id 必须唯一"
    result_ids = [
        b["tool_use_id"]
        for msg in strategy._messages
        if msg["role"] == "user"
        for b in msg["content"]
        if b.get("type") == "tool_result"
    ]
    assert set(result_ids) <= set(assistant_ids), "tool_result 必须引用重写后的 id"


def test_no_tool_use_breaks_loop_and_continues() -> None:
    """LLM 纯文本回复（无 tool_use）→ 结束内层 loop，返回 stop=False 让外壳继续。"""
    backend = _make_backend()
    llm = _ScriptedLLM([_text_reply()])
    strategy = AgentStrategy(llm=llm, intent="进入设置界面")

    decision = _decide(strategy, backend, llm)

    assert decision.action == "stop" and decision.stop is False
    assert decision.think == "本步未执行操作"
    assert strategy.should_stop() is False


def test_stale_count_increments_without_screen_change() -> None:
    """动作执行但界面无变化 → on_action_executed 后 stale+1。"""
    backend = _make_backend()  # 单屏，click 不变背景
    llm = _ScriptedLLM([_tool_reply(("click", {"x": 0.5, "y": 0.45}))])
    strategy = AgentStrategy(llm=llm, intent="进入设置界面")

    decision = _decide(strategy, backend, llm)
    strategy.on_action_executed(decision, ActionResult(success=True, screen_changed=True))

    assert strategy._stale_count == 1  # 忽略外壳 result，以 loop 内工具结果为准


def test_stale_count_resets_on_screen_change() -> None:
    """任一动作为 screen_changed=True → stale 清零。"""
    backend = _make_multi_screen_backend()
    llm = _ScriptedLLM([
        _tool_reply(("click", {"x": 0.1, "y": 0.1})),  # 不触发转移 → 无变化
        _text_reply(),  # 第 1 步第 2 轮：终止内层 loop
        _tool_reply(("click_text", {"text": "设置"})),  # 第 2 步：切屏 → 有变化
    ])
    strategy = AgentStrategy(llm=llm, intent="进入设置界面")

    d1 = _decide(strategy, backend, llm, step=0)
    strategy.on_action_executed(d1, ActionResult(success=True, screen_changed=False))
    assert strategy._stale_count == 1

    d2 = _decide(strategy, backend, llm, step=1)
    strategy.on_action_executed(d2, ActionResult(success=True, screen_changed=False))
    assert strategy._stale_count == 0
    assert backend.current_screen_id == "settings"


def test_should_stop_after_five_stale_steps() -> None:
    """连续 5 步无变化 → should_stop。"""
    strategy = AgentStrategy(llm=_ScriptedLLM([]), intent="进入设置界面")
    strategy._stale_count = 5
    assert strategy.should_stop() is True


def test_finish_without_goal_completed_still_stops() -> None:
    """finish(goal_completed=false) → stop=True 且 should_stop（无法继续场景）。"""
    backend = _make_backend()
    llm = _ScriptedLLM([
        _tool_reply(("finish", {"goal_completed": False, "summary": "找不到入口"})),
    ])
    strategy = AgentStrategy(llm=llm, intent="进入设置界面")

    decision = _decide(strategy, backend, llm)

    assert decision.stop is True
    assert decision.goal_completed is False
    assert strategy.should_stop() is True


def test_images_stripped_from_history() -> None:
    """get_screenshot 的 image 块在进入历史后被替换为文本占位 [截图]。"""
    backend = _make_backend()
    llm = _ScriptedLLM([
        _tool_reply(("get_screenshot", {})),
        _tool_reply(("finish", {"goal_completed": True, "summary": "完成"})),
    ])
    strategy = AgentStrategy(llm=llm, intent="进入设置界面")

    _decide(strategy, backend, llm)

    def _has_image(blocks: list) -> bool:
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "image":
                return True
            inner = b.get("content")
            if isinstance(inner, list) and _has_image(inner):
                return True
        return False

    assert not any(
        _has_image(m["content"]) for m in strategy._messages if isinstance(m["content"], list)
    )
    # 占位文本存在
    placeholders = [
        b for msg in strategy._messages if isinstance(msg["content"], list)
        for b in msg["content"]
        if b.get("type") == "tool_result"
        for ib in b.get("content", [])
        if ib.get("text") == "[截图]"
    ]
    assert len(placeholders) == 1


def test_thinking_blocks_stripped_from_assistant_history() -> None:
    """assistant 回复中的 thinking block 不回传历史（无 signature 会 400）。"""
    backend = _make_backend()
    reply = _tool_reply(("finish", {"goal_completed": True, "summary": "完成"}))
    reply["content"].insert(0, {"type": "thinking", "thinking": "想..."})
    llm = _ScriptedLLM([reply])
    strategy = AgentStrategy(llm=llm, intent="进入设置界面")

    _decide(strategy, backend, llm)

    assistant_msgs = [m for m in strategy._messages if m["role"] == "assistant"]
    assert assistant_msgs
    for m in assistant_msgs:
        assert all(b.get("type") != "thinking" for b in m["content"])


def test_max_agent_rounds_cap() -> None:
    """LLM 一直调工具时，内层 loop 受 max_agent_rounds 限制。"""
    backend = _make_backend()
    llm = _ScriptedLLM([])  # 队列空 → 但下面塞满 click 回复
    llm._replies = [_tool_reply(("click", {"x": 0.5, "y": 0.45})) for _ in range(10)]
    strategy = AgentStrategy(llm=llm, intent="进入设置界面", max_agent_rounds=2)

    decision = _decide(strategy, backend, llm)

    assert len(llm.calls) == 2
    assert len(backend.click_history) == 2
    assert decision.stop is False  # 轮次用完未 finish，外壳继续下一步


def test_llm_failure_returns_stop() -> None:
    """LLM 调用异常 → stop 决策，不抛出。"""
    backend = _make_backend()
    llm = _FailingLLM()
    strategy = AgentStrategy(llm=llm, intent="进入设置界面")

    decision = _decide(strategy, backend, llm)

    assert decision.stop is True
    assert decision.think.startswith("LLM失败")


def test_get_state_map_backend_info() -> None:
    """get_state_map 标记 strategy=agent。"""
    strategy = AgentStrategy(llm=_ScriptedLLM([]), intent="进入设置界面")
    state_map = strategy.get_state_map()
    assert isinstance(state_map, StateMap)
    assert state_map.backend_info == {"strategy": "agent"}


def test_step_message_carries_last_step_summary() -> None:
    """下一步 user 消息带 [上一步执行摘要]。"""
    backend = _make_backend()
    llm = _ScriptedLLM([
        _tool_reply(("click", {"x": 0.5, "y": 0.45})),  # 第 1 步
        _text_reply(),  # 第 1 步结束
        _tool_reply(("finish", {"goal_completed": True, "summary": "完成"})),  # 第 2 步
    ])
    strategy = AgentStrategy(llm=llm, intent="进入设置界面")

    _decide(strategy, backend, llm, step=0)
    _decide(strategy, backend, llm, step=1)

    user_texts = [
        b["text"] for m in strategy._messages if m["role"] == "user"
        for b in m["content"] if b.get("type") == "text"
    ]
    assert any("[上一步执行摘要]" in t and "click" in t for t in user_texts)
