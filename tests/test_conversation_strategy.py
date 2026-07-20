"""ConversationStrategy 测试（对齐 Open-AutoGLM）。"""
from __future__ import annotations

from unittest.mock import MagicMock

from joker_test.explorer.conversation_strategy import ConversationStrategy
from joker_test.explorer.strategy import ExploreContext


def test_decide_strips_image_from_history() -> None:
    """LLM 响应后，最后一条 user 消息不含图像，assistant 消息含结构化 XML。"""
    mock_llm = MagicMock()
    mock_llm.create.return_value = {
        "content": [
            {
                "type": "tool_use",
                "name": "execute_action",
                "input": {
                    "action": "click", "target": "开始",
                    "think": "需要点击开始按钮",
                },
            }
        ]
    }
    strategy = ConversationStrategy(llm=mock_llm, intent="测试")
    ctx = ExploreContext(
        step=0, max_steps=10, intent="测试", backend=None, llm=mock_llm
    )
    decision = strategy.decide(screenshot=b"fake_img", perception=None, ctx=ctx)

    last_user = [m for m in strategy._messages if m["role"] == "user"][-1]
    has_image = any(
        b.get("type") == "image" or b.get("type") == "image_url"
        for b in last_user["content"]
    )
    assert not has_image, "历史截图应被剥离"
    assert decision.action == "click"

    # assistant 消息含结构化 XML
    last_asst = [m for m in strategy._messages if m["role"] == "assistant"][-1]
    asst_text = last_asst["content"][0]["text"]
    assert "<action>click</action>" in asst_text
    assert "<target>开始</target>" in asst_text


def test_assistant_message_added_to_history() -> None:
    """assistant 消息被追加到历史。"""
    mock_llm = MagicMock()
    mock_llm.create.return_value = {
        "content": [
            {
                "type": "tool_use",
                "name": "execute_action",
                "input": {"action": "stop", "target": ""},
            }
        ]
    }
    strategy = ConversationStrategy(llm=mock_llm, intent="测试")
    ctx = ExploreContext(
        step=0, max_steps=10, intent="测试", backend=None, llm=mock_llm
    )
    strategy.decide(screenshot=b"img", perception=None, ctx=ctx)

    assistant_msgs = [m for m in strategy._messages if m["role"] == "assistant"]
    assert len(assistant_msgs) >= 1


def test_should_stop_on_goal_completed() -> None:
    strategy = ConversationStrategy(llm=MagicMock(), intent="测试")
    strategy._goal_completed = True
    assert strategy.should_stop() is True


def test_should_stop_on_token_limit() -> None:
    strategy = ConversationStrategy(
        llm=MagicMock(), intent="测试", max_conversation_tokens=10
    )
    strategy._messages.append(
        {"role": "user", "content": [{"type": "text", "text": "x" * 100}]}
    )
    assert strategy.should_stop() is True


def test_base_prompt_guides_goal_completion() -> None:
    """_BASE_PROMPT has goal completion guidance, no think/answer format, no preset judgments."""
    from joker_test.explorer.conversation_strategy import _BASE_PROMPT

    assert "goal_completed" in _BASE_PROMPT
    assert "<think>推理</think>" not in _BASE_PROMPT  # thinking handled by API
    assert "<answer>" not in _BASE_PROMPT
    assert "点击轨道" not in _BASE_PROMPT
    # Slider guidance: OCR may be stale, visually locate handle
    assert "视觉定位手柄" in _BASE_PROMPT


def test_on_action_executed_sets_last_action_info() -> None:
    """on_action_executed sets _last_action_info (pure facts), no feedback judgment."""
    from joker_test.explorer.strategy import ActionResult, StepDecision

    strategy = ConversationStrategy(llm=MagicMock(), intent="测试")
    decision = StepDecision(think="测试", action="click", target="设置", x=0.31, y=0.76)
    result = ActionResult(success=True, screen_changed=True, pixel_diff_ratio=0.4)

    strategy.on_action_executed(decision, result)

    # observation is pure fact line (action + diff), no judgment
    assert "click" in strategy._last_action_info
    assert "0.4" in strategy._last_action_info
    assert "无效" not in strategy._last_action_info
    assert "swipe" not in strategy._last_action_info
    # _build_step_text no longer has feedback prefix
    ctx = ExploreContext(step=1, max_steps=10, intent="测试", backend=None, llm=MagicMock())
    text = strategy._build_step_text(None, ctx)
    assert "上一步反馈" not in text
    assert strategy._last_action_info == ""  # consumed after injection
