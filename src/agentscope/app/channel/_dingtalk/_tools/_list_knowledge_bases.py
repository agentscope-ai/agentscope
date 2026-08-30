# -*- coding: utf-8 -*-
"""List knowledge bases visible to the current DingTalk user."""

import json

from pydantic import Field

from .....message import TextBlock
from .....tool import ParamsBase, ToolChunk
from ._base import _DingTalkKnowledgeToolBase, _failure


class _ListKnowledgeBasesParams(ParamsBase):
    limit: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum number of knowledge bases to return.",
    )
    next_token: str = Field(
        default="",
        max_length=2048,
        description="Pagination token returned by a previous call.",
    )


class ListKnowledgeBases(_DingTalkKnowledgeToolBase):
    """List DingTalk knowledge bases readable by the current sender."""

    name: str = "ListKnowledgeBases"
    description: str = """List DingTalk knowledge bases the current user can
read.

Use the returned ``root_node_id`` with ``ListKnowledgeNodes`` to browse a
knowledge base. If ``next_token`` is non-empty, call this tool again with that
token to continue. Access is evaluated as the current DingTalk sender; no user
identity can be supplied by the model."""
    input_schema: dict = _ListKnowledgeBasesParams.model_json_schema()

    async def __call__(
        self,
        limit: int = 20,
        next_token: str = "",
    ) -> ToolChunk:
        """List knowledge bases visible to the bound DingTalk user.

        Args:
            limit (`int`): Maximum result count.
            next_token (`str`): Optional DingTalk pagination token.

        Returns:
            `ToolChunk`: JSON-encoded knowledge bases and pagination token.
        """
        try:
            result = await self._channel.list_knowledge_bases(
                self._channel_user_id,
                limit,
                next_token,
            )
        except RuntimeError as exc:
            return _failure(str(exc))
        items = [
            {
                "workspace_id": item.get("workspaceId", ""),
                "name": item.get("name", ""),
                "description": item.get("description", ""),
                "root_node_id": item.get("rootNodeId", ""),
                "url": item.get("url", ""),
                "type": item.get("type", ""),
                "permission_role": item.get("permissionRole", ""),
            }
            for item in result["knowledge_bases"]
            if isinstance(item, dict) and item.get("workspaceId")
        ]
        return ToolChunk(
            content=[
                TextBlock(
                    text=json.dumps(
                        {
                            "knowledge_bases": items,
                            "next_token": result["next_token"],
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
        )
