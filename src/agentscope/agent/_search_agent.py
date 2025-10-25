# -*- coding: utf-8 -*-
"""Lightweight subagent that aggregates search tool responses."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import shortuuid
from pydantic import BaseModel

from ..message import Msg
from ..tool import ToolResponse
from .._utils._common import _execute_async_or_sync_func
from ._subagent_base import SubAgentBase


class SearchQuery(BaseModel):
    """Input payload for SearchAgent."""

    query: str
    context: str | None = None


class SearchAgent(SubAgentBase):
    """Subagent that runs registered search tools and aggregates results."""

    InputModel = SearchQuery

    def __init__(
        self,
        *,
        permissions,
        spec_name: str,
        toolkit=None,
        memory=None,
        tools=None,
        model_override=None,
        ephemeral_memory: bool = True,
    ) -> None:
        super().__init__(
            permissions=permissions,
            spec_name=spec_name,
            toolkit=toolkit,
            memory=memory,
            tools=tools,
            model_override=model_override,
            ephemeral_memory=ephemeral_memory,
        )
        self._results_root = f"/workspace/subagents/{self.spec_name}/"

    async def reply(
        self,
        input_obj: SearchQuery,
        **kwargs: Any,
    ) -> Msg:
        transcripts: list[str] = []
        errors: list[dict[str, Any]] = []

        if input_obj.context:
            transcripts.append(
                "\n".join(
                    [
                        "# Search Context",
                        input_obj.context,
                        "",
                    ],
                ).strip(),
            )

        for registered in self.toolkit.tools.values():
            tool_name = registered.name
            params = (
                registered.extended_json_schema.get("function", {})
                .get("parameters", {})
                .get("properties", {})
            )
            if "query" not in params:
                continue

            try:
                result = await _execute_async_or_sync_func(
                    registered.original_func,
                    query=input_obj.query,
                )
            except Exception as exc:  # pragma: no cover - defensive
                errors.append({"tool": tool_name, "error": str(exc)})
                continue

            transcripts.append(
                self._format_tool_result(
                    tool_name,
                    result if isinstance(result, ToolResponse) else None,
                ),
            )

        if errors:
            transcripts.append(
                "\n".join(
                    [
                        "# Tool Errors",
                        json.dumps(errors, ensure_ascii=False, indent=2),
                    ],
                ),
            )

        body = "\n\n".join(transcripts).strip()
        metadata: dict[str, Any] = {"query": input_obj.query}
        if input_obj.context:
            metadata["context"] = input_obj.context
        if errors:
            metadata["errors"] = errors

        content = body or "No search results were returned."
        if self.filesystem_service is not None and body:
            artifact_path = self._compose_result_path(input_obj.query)
            try:
                self.filesystem_service.write_file(artifact_path, body)
            except Exception as exc:  # pragma: no cover - defensive
                errors.append({"tool": "filesystem", "error": str(exc)})
                metadata["errors"] = errors
            else:
                metadata["artifact_path"] = artifact_path
                content = f"Search results saved to {artifact_path}\n\n{body}".strip()

        return Msg(
            name=self.spec_name,
            content=content,
            role="assistant",
            metadata=metadata if metadata else None,
        )

    def _compose_result_path(
        self,
        query: str,
    ) -> str:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        slug = shortuuid.uuid()[:6]
        safe_query = "".join(ch for ch in query if ch.isalnum() or ch in ("-", "_"))
        safe_query = safe_query[:24] if safe_query else "search"
        filename = f"{timestamp}_{safe_query}_{slug}.md"
        return f"{self._results_root}{filename}"

    @staticmethod
    def _format_tool_result(
        tool_name: str,
        response: ToolResponse | None,
    ) -> str:
        if response is None:
            return f"## {tool_name}\n(No ToolResponse returned.)"

        text_blocks = [
            block.get("text", "")
            for block in response.content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(text_blocks).strip()

        if not text and response.metadata:
            text = json.dumps(response.metadata, ensure_ascii=False, indent=2)

        if not text:
            text = "(No textual content returned.)"

        return f"## {tool_name}\n{text}"


__all__ = ["SearchAgent", "SearchQuery"]
