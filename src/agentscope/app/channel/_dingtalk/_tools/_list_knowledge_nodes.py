# -*- coding: utf-8 -*-
"""Browse child nodes in a DingTalk knowledge base."""

import json

from pydantic import Field

from .....message import TextBlock
from .....tool import ParamsBase, ToolChunk
from ._base import _DingTalkKnowledgeToolBase, _failure


class _ListKnowledgeNodesParams(ParamsBase):
    parent_node_id: str = Field(
        min_length=1,
        max_length=512,
        description="Knowledge-base root node id or folder node id.",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=100,
        description="Maximum number of direct child nodes to return.",
    )
    next_token: str = Field(
        default="",
        max_length=2048,
        description="Pagination token returned by a previous call.",
    )


class ListKnowledgeNodes(_DingTalkKnowledgeToolBase):
    """List direct children of one DingTalk knowledge node."""

    name: str = "ListKnowledgeNodes"
    description: str = """Browse a DingTalk knowledge base one level at a time.

Pass a ``root_node_id`` from ``ListKnowledgeBases`` or a folder's ``node_id``.
Nodes with ``has_children=true`` can be browsed again. File nodes whose
``category`` is ``ALIDOC`` can be read with ``ReadKnowledgeDocument``. Use a
returned ``next_token`` to continue the same directory listing."""
    input_schema: dict = _ListKnowledgeNodesParams.model_json_schema()

    async def __call__(
        self,
        parent_node_id: str,
        limit: int = 50,
        next_token: str = "",
    ) -> ToolChunk:
        """List children visible below ``parent_node_id``.

        Args:
            parent_node_id (`str`): Root or folder node id.
            limit (`int`): Maximum result count.
            next_token (`str`): Optional DingTalk pagination token.

        Returns:
            `ToolChunk`: JSON-encoded child nodes and pagination token.
        """
        try:
            result = await self._channel.list_knowledge_nodes(
                self._channel_user_id,
                parent_node_id,
                limit,
                next_token,
            )
        except RuntimeError as exc:
            return _failure(str(exc))
        items = [
            {
                "node_id": item.get("nodeId", ""),
                "workspace_id": item.get("workspaceId", ""),
                "name": item.get("name", ""),
                "type": item.get("type", ""),
                "category": item.get("category", ""),
                "has_children": bool(item.get("hasChildren")),
                "url": item.get("url", ""),
                "modified_time": item.get("modifiedTime", ""),
                "permission_role": item.get("permissionRole", ""),
            }
            for item in result["nodes"]
            if isinstance(item, dict) and item.get("nodeId")
        ]
        return ToolChunk(
            content=[
                TextBlock(
                    text=json.dumps(
                        {
                            "nodes": items,
                            "next_token": result["next_token"],
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
        )
