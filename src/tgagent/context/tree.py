from collections.abc import Sequence
from typing import Any

from tgagent.storage.repos import NodeRecord


def build_api_messages(path: Sequence[NodeRecord]) -> list[dict[str, Any]]:
    """Turn a root-to-leaf node path into Messages API ``messages``.

    Everything before the latest ``compaction`` block is dropped: the API ignores it anyway,
    and the compaction block may legitimately open the list as an assistant message.
    """
    start = max((i for i, node in enumerate(path) if node.has_compaction), default=0)
    messages: list[dict[str, Any]] = []
    for index, node in enumerate(path[start:]):
        content = list(node.content)
        if index == 0 and node.has_compaction:
            last = max(i for i, block in enumerate(content) if block.get("type") == "compaction")
            content = content[last:]
        messages.append({"role": node.role, "content": content})
    return messages
