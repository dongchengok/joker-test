"""ConversationStrategy 测试（对齐 Open-AutoGLM）。"""
from __future__ import annotations

from unittest.mock import MagicMock

from joker_test.explorer.conversation_strategy import ConversationStrategy
from joker_test.explorer.strategy import ExploreContext


def test_decide_strips_image_from_history() -> None:
    """LLM 响应后，最后一条 user 消息的图像块被剥离。"""
    mock_llm = MagicMock()
    mock_llm.simple_converse.return_value = {
        "content": [
            {
                "type": "text",
                "text": (
                    "<think>看到主菜单</think>"
                    '<answer>{"action":"click_text","target":"开始"}</answer>'
                ),
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
    assert decision.action == "click_text"


def test_assistant_repackaged_as_think_answer() -> None:
    """assistant 消息被重新封装为 <think>/<answer> 格式。"""
    mock_llm = MagicMock()
    mock_llm.simple_converse.return_value = {
        "content": [
            {
                "type": "text",
                "text": '<think>推理</think><answer>{"action":"stop"}</answer>',
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
    content = assistant_msgs[-1]["content"]
    assert "<think>" in content and "<answer>" in content


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
