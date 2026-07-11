from __future__ import annotations

from agent_harness.domain.messages import CanonicalMessage, ToolCall
from agent_harness.providers.deepseek import DeepSeekProvider


def test_deepseek_reasoning_content_round_trips(monkeypatch):
    """Verify that DeepSeek reasoning_content is parsed and sent on the next request."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    provider = DeepSeekProvider(base_url="https://example.invalid")
    raw = {
        "id": "response_1",
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "我需要先读取文件。",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "list_files", "arguments": '{"path":"."}'},
                        }
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }

    parsed = provider._parse_response(raw)
    serialized = provider._to_provider_message(parsed.assistant_message)

    assert parsed.assistant_message.reasoning_content == "我需要先读取文件。"
    assert parsed.provider_metadata["reasoning_content"] == "我需要先读取文件。"
    assert serialized["reasoning_content"] == "我需要先读取文件。"
    assert serialized["tool_calls"][0]["id"] == "call_1"


def test_deepseek_to_provider_message_preserves_existing_tool_call_reasoning(monkeypatch):
    """Verify that assistant messages with tool calls keep reasoning_content when serialized."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    provider = DeepSeekProvider(base_url="https://example.invalid")
    message = CanonicalMessage(
        role="assistant",
        reasoning_content="分析工具参数。",
        tool_calls=[ToolCall(id="call_2", name="read_file", arguments={"path": "a.py"})],
    )

    payload = provider._to_provider_message(message)

    assert payload["reasoning_content"] == "分析工具参数。"
    assert payload["tool_calls"][0]["function"]["name"] == "read_file"
