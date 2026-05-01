# -*- coding: utf-8 -*-
"""The base class for MCP clients in AgentScope."""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING, List

import mcp.types

if TYPE_CHECKING:
    from ..tool import MCPTool
else:
    MCPTool = Any


@dataclass
class MCPSkill:
    """Summary information about a skill available on an MCP server."""

    name: str
    """The name of the skill."""

    description: str
    """The description of the skill."""

    uri: str
    """The URI of the skill instruction resource."""


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
    async def get_tool(
        self,
        name: str,
    ) -> MCPTool:
        """Get a tool object by its name.

        Args:
            name (`str`):
                The name of the tool to get.

        Returns:
            `MCPTool`:
                A tool object that implements ToolProtocol.
        """

    @abstractmethod
    async def list_skills(self) -> List[MCPSkill]:
        """List all skills available on the MCP server."""

    @staticmethod
    def _extract_skills_from_resources(
        resources: list[mcp.types.Resource],
    ) -> List[MCPSkill]:
        """Extract MCP skills from MCP resources.

        Args:
            resources (`list[mcp.types.Resource]`):
                The MCP resources to extract skills from.

        Returns:
            `List[MCPSkill]`:
                A list of available MCP skills.
        """
        skills = []
        for resource in resources:
            uri = str(resource.uri)
            if uri.startswith("skill://") and uri.endswith("/SKILL.md"):
                name = uri[len("skill://") :].rsplit("/", 1)[0]
                skills.append(
                    MCPSkill(
                        name=name,
                        description=resource.description or "",
                        uri=uri,
                    ),
                )

        return skills
