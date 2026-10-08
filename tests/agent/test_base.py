from tgagent.agent.base import as_text_block, plain_text


def test_plain_text_reads_every_provider_format() -> None:
    content = [
        {"type": "text", "text": "neutral"},
        {"type": "thinking", "thinking": "hidden"},
        {"type": "compaction", "content": "summary"},
        {"type": "message", "content": [{"type": "output_text", "text": "openai", "annotations": []}]},
        {"text": "gemini"},
        {"text": "gemini thought", "thought": True},
        {"role": "assistant", "content": "chat"},
        {"type": "function_call_output", "call_id": "c", "output": "tool"},
        {"inline_data": {"mime_type": "image/png", "data": "AA=="}},
    ]

    assert plain_text(content) == "neutral\nsummary\nopenai\ngemini\nchat"


def test_compaction_summary_is_replayed_as_text() -> None:
    block = as_text_block({"type": "compaction", "content": "итоги"})

    assert block == {"type": "text", "text": "<conversation_summary>\nитоги\n</conversation_summary>"}
    assert as_text_block({"type": "text", "text": "x"}) == {"type": "text", "text": "x"}
