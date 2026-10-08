from tgagent.context.tree import build_api_messages
from tgagent.storage.repos import NodeRecord


def node(node_id: int, role: str, *blocks: dict[str, object]) -> NodeRecord:
    content = list(blocks)
    return NodeRecord(
        id=node_id,
        conversation_id=1,
        parent_id=node_id - 1 or None,
        role=role,  # type: ignore[arg-type]
        content=content,  # type: ignore[arg-type]
        has_compaction=any(b.get("type") == "compaction" for b in content),
    )


def text(value: str) -> dict[str, object]:
    return {"type": "text", "text": value}


COMPACTION = {"type": "compaction", "content": "summary", "signature": "sig"}


def test_plain_path_becomes_alternating_messages() -> None:
    path = [node(1, "user", text("q")), node(2, "assistant", text("a")), node(3, "user", text("q2"))]

    assert build_api_messages(path) == [
        {"role": "user", "content": [text("q")]},
        {"role": "assistant", "content": [text("a")]},
        {"role": "user", "content": [text("q2")]},
    ]


def test_history_before_latest_compaction_is_dropped() -> None:
    path = [
        node(1, "user", text("old")),
        node(2, "assistant", COMPACTION, text("first")),
        node(3, "user", text("mid")),
        node(4, "assistant", COMPACTION, text("second")),
        node(5, "user", text("new")),
    ]

    assert build_api_messages(path) == [
        {"role": "assistant", "content": [COMPACTION, text("second")]},
        {"role": "user", "content": [text("new")]},
    ]


def test_blocks_before_compaction_inside_same_node_are_dropped() -> None:
    path = [
        node(1, "assistant", text("before"), COMPACTION, text("after")),
        node(2, "user", text("next")),
    ]

    assert build_api_messages(path)[0] == {"role": "assistant", "content": [COMPACTION, text("after")]}


def test_input_content_is_not_mutated() -> None:
    first = node(1, "assistant", text("before"), COMPACTION)
    build_api_messages([first])

    assert first.content[0] == text("before")
