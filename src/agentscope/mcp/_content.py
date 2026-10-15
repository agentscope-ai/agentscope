# -*- coding: utf-8 -*-
"""MCP content conversion shared by local tools and standalone gateways."""
from typing import Any

import mcp.types
from pydantic import AnyUrl, ValidationError

from .._logging import logger


def _convert_mcp_content(content_blocks: list) -> list[dict[str, Any]]:
    """Return AgentScope block dictionaries without importing tool modules."""
    blocks: list[dict[str, Any]] = []
    for content in content_blocks:
        if isinstance(content, mcp.types.TextContent):
            blocks.append({"type": "text", "text": content.text})
        elif isinstance(
            content,
            (mcp.types.ImageContent, mcp.types.AudioContent),
        ):
            blocks.append(
                {
                    "type": "data",
                    "source": {
                        "type": "base64",
                        "media_type": content.mime_type,
                        "data": content.data,
                    },
                },
            )
        elif isinstance(content, mcp.types.EmbeddedResource):
            if isinstance(content.resource, mcp.types.TextResourceContents):
                blocks.append(
                    {
                        "type": "text",
                        "text": content.resource.model_dump_json(
                            by_alias=True,
                            indent=2,
                        ),
                    },
                )
            else:
                logger.error(
                    "Unsupported EmbeddedResource content type: %s.",
                    type(content.resource),
                )
        elif isinstance(
            content,
            (mcp.types.ResourceLink, mcp.types.ResourceContents),
        ):
            media_type = content.mime_type
            if isinstance(content, mcp.types.ResourceLink):
                media_type = media_type or "application/octet-stream"
            try:
                url = str(AnyUrl(str(content.uri)))
            except ValidationError:
                message = f"<unconvertible MCP resource URI: {content.uri}>"
                logger.warning(message)
                blocks.append(
                    {
                        "type": "text",
                        "text": message,
                    },
                )
                continue
            blocks.append(
                {
                    "type": "data",
                    "source": {
                        "type": "url",
                        "media_type": media_type,
                        "url": url,
                    },
                },
            )
        else:
            logger.warning(
                "Unsupported content type: %s. Skipping this content.",
                type(content),
            )
    return blocks
