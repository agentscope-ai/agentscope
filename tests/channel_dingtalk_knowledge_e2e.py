# -*- coding: utf-8 -*-
"""Manual live E2E check for DingTalk knowledge-base tools.

This runner is intentionally not collected by pytest. It waits for one real
DingTalk robot message, binds the three knowledge tools to that sender, finds
the configured test document, and verifies a marker in its content::

    export DINGTALK_CLIENT_ID=ding...
    export DINGTALK_CLIENT_SECRET=...
    uv run python tests/channel_dingtalk_knowledge_e2e.py
"""

import argparse
import asyncio
import json
import os
from typing import Any, cast

from agentscope.app.channel import DingTalkChannel
from agentscope.app.channel._base import (
    ChannelConfirmationResultEvent,
    ChannelEvent,
)
from agentscope.app.channel._dingtalk._tools import (
    ListKnowledgeBases,
    ListKnowledgeNodes,
    ReadKnowledgeDocument,
)
from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import BackendBase, ToolChunk

_DEFAULT_KNOWLEDGE_BASE = "AgentScope E2E KB"
_DEFAULT_DOCUMENT = "AgentScope E2E Runbook"
_DEFAULT_MARKER = "DINGTALK_KB_E2E_PASS_20260830"
_MAX_VISITED_NODES = 200


def _required_environment(name: str) -> str:
    """Read one required environment variable without printing its value."""
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"Set {name} before running this E2E check.")
    return value


def _channel() -> DingTalkChannel:
    """Create a real channel using credentials from the environment."""
    return DingTalkChannel(
        "dingtalk-knowledge-e2e",
        DingTalkChannel.Credentials(
            client_id=_required_environment("DINGTALK_CLIENT_ID"),
            client_secret=_required_environment("DINGTALK_CLIENT_SECRET"),
        ),
        DingTalkChannel.Config(only_at_reply=False),
    )


def _tool_payload(chunk: ToolChunk) -> dict[str, Any]:
    """Decode a knowledge-tool result or raise its visible error."""
    block = cast(TextBlock, chunk.content[0])
    if chunk.state is ToolResultState.ERROR:
        raise RuntimeError(block.text)
    payload = json.loads(block.text)
    if not isinstance(payload, dict):
        raise RuntimeError("The knowledge tool returned an invalid payload.")
    return payload


def _same_name(actual: str, expected: str) -> bool:
    """Compare DingTalk document names while tolerating ``.adoc``."""
    actual = actual.strip().removesuffix(".adoc").strip()
    expected = expected.strip().removesuffix(".adoc").strip()
    return actual.casefold() == expected.casefold()


async def _find_knowledge_base(
    tool: ListKnowledgeBases,
    expected_name: str,
) -> dict[str, Any]:
    """Find the configured knowledge base across all result pages."""
    next_token = ""
    while True:
        payload = _tool_payload(await tool(50, next_token))
        for item in payload.get("knowledge_bases", []):
            if _same_name(str(item.get("name") or ""), expected_name):
                return item
        next_token = str(payload.get("next_token") or "")
        if not next_token:
            raise RuntimeError(
                f"Knowledge base {expected_name!r} is not visible to the "
                "message sender.",
            )


async def _find_document(
    tool: ListKnowledgeNodes,
    root_node_id: str,
    expected_name: str,
) -> dict[str, Any]:
    """Breadth-first browse folders until the configured document is found."""
    pending = [root_node_id]
    visited: set[str] = set()
    observed_nodes: list[str] = []
    while pending and len(visited) < _MAX_VISITED_NODES:
        parent_node_id = pending.pop(0)
        if parent_node_id in visited:
            continue
        visited.add(parent_node_id)
        next_token = ""
        while True:
            payload = _tool_payload(
                await tool(parent_node_id, 100, next_token),
            )
            for node in payload.get("nodes", []):
                node_id = str(node.get("node_id") or "")
                if not node_id:
                    continue
                observed_nodes.append(
                    f"{node.get('name')!r} ({node_id})",
                )
                if _same_name(str(node.get("name") or ""), expected_name):
                    return node
                if node.get("has_children"):
                    pending.append(node_id)
            next_token = str(payload.get("next_token") or "")
            if not next_token:
                break
    raise RuntimeError(
        f"Document {expected_name!r} was not found below the knowledge-base "
        f"root {root_node_id!r} after visiting {len(visited)} folders. "
        f"Visible nodes: {', '.join(observed_nodes) or '<none>'}.",
    )


async def _check_knowledge(
    channel: DingTalkChannel,
    event: ChannelEvent,
    knowledge_base_name: str,
    document_name: str,
    marker: str,
) -> None:
    """Run the three knowledge tools as the real inbound sender."""
    backend = cast(BackendBase, object())
    list_bases = ListKnowledgeBases(
        channel,
        backend,
        event.channel_user_id,
    )
    list_nodes = ListKnowledgeNodes(
        channel,
        backend,
        event.channel_user_id,
    )
    read_document = ReadKnowledgeDocument(
        channel,
        backend,
        event.channel_user_id,
    )

    knowledge_base = await _find_knowledge_base(
        list_bases,
        knowledge_base_name,
    )
    print(
        "PASS: knowledge base visible: "
        f"{knowledge_base['name']} ({knowledge_base['workspace_id']}), "
        f"root={knowledge_base['root_node_id']}",
        flush=True,
    )
    document = await _find_document(
        list_nodes,
        str(knowledge_base["root_node_id"]),
        document_name,
    )
    print(
        f"PASS: document node found: {document['name']} "
        f"({document['node_id']})",
        flush=True,
    )
    result = _tool_payload(
        await read_document(str(document["node_id"]), 0, 100),
    )
    markdown = str(result.get("markdown") or "")
    if marker not in markdown:
        raise RuntimeError(
            f"Document content did not contain marker {marker!r}.",
        )
    print(f"PASS: document marker found: {marker}", flush=True)


async def _run(args: argparse.Namespace) -> int:
    """Listen for one message and run the live knowledge checks."""
    channel = _channel()
    completed = asyncio.Event()
    succeeded = False

    async def emit(
        event: ChannelEvent | ChannelConfirmationResultEvent,
    ) -> None:
        nonlocal succeeded
        if not isinstance(event, ChannelEvent) or completed.is_set():
            return
        try:
            await _check_knowledge(
                channel,
                event,
                args.knowledge_base,
                args.document,
                args.marker,
            )
            succeeded = True
            await channel.send_message_to(
                event.chat_id,
                "**AgentScope DingTalk Knowledge E2E PASS**\n\n"
                f"Found marker: `{args.marker}`",
            )
        except Exception as exc:  # pylint: disable=broad-except
            print(f"FAIL: {type(exc).__name__}: {exc}", flush=True)
        finally:
            completed.set()

    listener = asyncio.create_task(channel.start_listening(emit))
    try:
        for _ in range(150):
            if channel.status.state == "connected":
                print(
                    "READY: send any direct message to the DingTalk bot",
                    flush=True,
                )
                break
            if channel.status.state == "failed":
                print("FAIL: DingTalk Stream connection failed", flush=True)
                return 1
            await asyncio.sleep(0.2)
        else:
            print("FAIL: DingTalk Stream connection timed out", flush=True)
            return 1
        try:
            await asyncio.wait_for(completed.wait(), timeout=args.timeout)
        except TimeoutError:
            print(
                "FAIL: no DingTalk message received before timeout",
                flush=True,
            )
            return 1
        return 0 if succeeded else 1
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)
        await channel.aclose()


def _parse_args() -> argparse.Namespace:
    """Parse non-secret test-data options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--knowledge-base",
        default=_DEFAULT_KNOWLEDGE_BASE,
    )
    parser.add_argument("--document", default=_DEFAULT_DOCUMENT)
    parser.add_argument("--marker", default=_DEFAULT_MARKER)
    parser.add_argument("--timeout", type=float, default=300.0)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run(_parse_args())))
