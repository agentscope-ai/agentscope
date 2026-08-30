# -*- coding: utf-8 -*-
"""Read a DingTalk knowledge document as bounded Markdown text."""

import json
import re
from typing import Any

from pydantic import Field

from .....message import TextBlock
from .....tool import ParamsBase, ToolChunk
from ._base import _DingTalkKnowledgeToolBase, _failure

_MAX_CONTENT_CHARS = 20_000


class _ReadKnowledgeDocumentParams(ParamsBase):
    node_id: str = Field(
        min_length=1,
        max_length=512,
        description="ALIDOC node id returned by ListKnowledgeNodes.",
    )
    start_index: int = Field(
        default=0,
        ge=0,
        description="First document block index to read.",
    )
    max_blocks: int = Field(
        default=50,
        ge=1,
        le=100,
        description="Maximum number of document blocks to read.",
    )


class ReadKnowledgeDocument(_DingTalkKnowledgeToolBase):
    """Read a plain DingTalk knowledge document for the current sender."""

    name: str = "ReadKnowledgeDocument"
    description: str = """Read text from a DingTalk ALIDOC knowledge document.

Use a file ``node_id`` returned by ``ListKnowledgeNodes``. The result contains
Markdown text and may contain ``next_start_index`` for long documents. This
minimal reader handles headings, paragraphs, lists and quotes; tables and
other rich blocks are represented by placeholders."""
    input_schema: dict = _ReadKnowledgeDocumentParams.model_json_schema()

    async def __call__(
        self,
        node_id: str,
        start_index: int = 0,
        max_blocks: int = 50,
    ) -> ToolChunk:
        """Read a bounded range of document blocks.

        Args:
            node_id (`str`): DingTalk Wiki node id.
            start_index (`int`): First block index to read.
            max_blocks (`int`): Maximum number of blocks to request.

        Returns:
            `ToolChunk`: Document metadata, Markdown and continuation index.
        """
        try:
            node, blocks = await self._channel.read_knowledge_document(
                self._channel_user_id,
                node_id,
                start_index,
                start_index + max_blocks - 1,
            )
        except RuntimeError as exc:
            return _failure(str(exc))
        if not node:
            return _failure("DingTalk returned no metadata for this node.")
        if str(node.get("type") or "").upper() != "FILE":
            return _failure(
                "The selected DingTalk knowledge node is not a document.",
            )
        category = str(node.get("category") or "").upper()
        if category and category != "ALIDOC":
            return _failure(
                f"The selected DingTalk document type '{category}' is not "
                "supported by the minimal reader.",
            )

        markdown, consumed, unsupported, content_truncated = _render_blocks(
            blocks,
        )
        next_start_index: int | None = None
        if consumed:
            last_index = int(consumed[-1].get("index", start_index))
            if content_truncated or len(blocks) >= max_blocks:
                next_start_index = last_index + 1
        statistical_info = node.get("statisticalInfo")
        statistical_info = (
            statistical_info if isinstance(statistical_info, dict) else {}
        )
        result = {
            "document": {
                "node_id": node.get("nodeId", node_id),
                "workspace_id": node.get("workspaceId", ""),
                "name": node.get("name", ""),
                "url": node.get("url", ""),
                "modified_time": node.get("modifiedTime", ""),
                "word_count": statistical_info.get("wordCount"),
            },
            "markdown": markdown,
            "start_index": start_index,
            "returned_blocks": len(consumed),
            "next_start_index": next_start_index,
            "unsupported_block_types": sorted(unsupported),
        }
        return ToolChunk(
            content=[
                TextBlock(text=json.dumps(result, ensure_ascii=False)),
            ],
        )


def _render_blocks(
    blocks: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], set[str], bool]:
    """Render supported blocks within the tool's output budget."""
    parts: list[str] = []
    consumed: list[dict[str, Any]] = []
    unsupported: set[str] = set()
    total = 0
    for block in sorted(blocks, key=_block_index):
        rendered, block_type, supported = _render_block(block)
        if not supported:
            unsupported.add(block_type)
        added = len(rendered) + (2 if parts else 0)
        if total + added > _MAX_CONTENT_CHARS:
            return "\n\n".join(parts), consumed, unsupported, True
        parts.append(rendered)
        consumed.append(block)
        total += added
    return "\n\n".join(parts), consumed, unsupported, False


def _render_block(block: dict[str, Any]) -> tuple[str, str, bool]:
    """Render one known DingTalk block and mark unsupported rich blocks."""
    block_type = str(block.get("blockType") or "unknown")
    detail = block.get(block_type)
    detail = detail if isinstance(detail, dict) else {}
    text = str(detail.get("text") or block.get("text") or "")
    if block_type == "heading":
        level_value = str(detail.get("level") or "1")
        match = re.search(r"([1-6])$", level_value)
        level = int(match.group(1)) if match else 1
        return f"{'#' * level} {text}".rstrip(), block_type, True
    if block_type == "paragraph":
        return text, block_type, True
    if block_type == "unorderedList":
        return f"- {text}".rstrip(), block_type, True
    if block_type == "orderedList":
        return f"1. {text}".rstrip(), block_type, True
    if block_type == "blockquote":
        return f"> {text}".rstrip(), block_type, True
    if block_type == "table":
        rows = detail.get("rowSize", "?")
        columns = detail.get("colSize", "?")
        return f"[Table: {rows} rows × {columns} columns]", block_type, False
    return f"[Unsupported block: {block_type}]", block_type, False


def _block_index(block: dict[str, Any]) -> int:
    """Return a sortable block index with malformed values at the end."""
    try:
        return int(block.get("index", 2**31 - 1))
    except (TypeError, ValueError):
        return 2**31 - 1
