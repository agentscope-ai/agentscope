# -*- coding: utf-8 -*-
"""The base class for MCP clients in AgentScope."""
from abc import abstractmethod
from typing import Callable, List

import mcp.types

from .._logging import logger
from ..message import ImageBlock, Base64Source, AudioBlock, TextBlock


class MCPClientBase:
    """Base class for MCP clients."""

    def __init__(self, name: str) -> None:
        """Initialize the MCP client with a name.

        Args:
            name (`str`):
                The name to identify the MCP server, which should be unique
                across the MCP servers.
        """
        self.name = name

    @abstractmethod
    async def get_callable_function(
        self,
        func_name: str,
        wrap_tool_result: bool = True,
    ) -> Callable:
        """Get a tool function by its name."""

    @staticmethod
    def _convert_mcp_content_to_as_blocks(
        mcp_content_blocks: list,
    ) -> List[TextBlock | ImageBlock | AudioBlock]:
        """Convert MCP content to AgentScope blocks."""

        as_content: list = []
        for content in mcp_content_blocks:
            if isinstance(content, mcp.types.TextContent):
                as_content.append(
                    TextBlock(
                        type="text",
                        text=content.text,
                    ),
                )
            elif isinstance(content, mcp.types.ImageContent):
                as_content.append(
                    ImageBlock(
                        type="image",
                        source=Base64Source(
                            type="base64",
                            media_type=content.mimeType,
                            data=content.data,
                        ),
                    ),
                )
            elif isinstance(content, mcp.types.AudioContent):
                as_content.append(
                    AudioBlock(
                        type="audio",
                        source=Base64Source(
                            type="base64",
                            media_type=content.mimeType,
                            data=content.data,
                        ),
                    ),
                )
            elif isinstance(content, mcp.types.EmbeddedResource):
                if isinstance(
                    content.resource,
                    mcp.types.TextResourceContents,
                ):
                    as_content.append(
                        TextBlock(
                            type="text",
                            text=content.resource.model_dump_json(indent=2),
                        ),
                    )
                else:
                    # TODO: support the BlobResourceContents in the future,
                    #  which is a base64-encoded string representing the
                    #  binary data
                    logger.error(
                        "Unsupported EmbeddedResource content type: %s. "
                        "Skipping this content.",
                        type(content.resource),
                    )
            else:
                logger.warning(
                    "Unsupported content type: %s. Skipping this content.",
                    type(content),
                )
        return as_content
