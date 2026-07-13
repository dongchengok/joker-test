"""ConversationStrategy 测试（对齐 Open-AutoGLM）。"""
from __future__ import annotations

from unittest.mock import MagicMock

from joker_test.explorer.conversation_strategy import ConversationStrategy
from joker_test.explorer.strategy import ExploreContext


def test_decide_strips_image_from_history() -> None:
    """LLM 响应后，最后一条 user 消息不含图像。"""
    mock_llm = MagicMock()
    mock_llm.create.return_value = {
        "content": [
            {
                "type": "tool_use",
                "name": "execute_action",
                "input": {"action": "click", "target": "开始"},
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
