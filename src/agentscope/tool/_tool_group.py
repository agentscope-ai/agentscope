# -*- coding: utf-8 -*-
"""The tool group class in AgentScope for serialization."""

from typing import Any, Literal
from pydantic import BaseModel, Field

from ..mcp import MCPClient
from ._base import ToolBase
from ._registry import ToolRegistry


class ToolGroup(BaseModel):
    """A group of related tools, mcps, and skills that an agent can activate,
    deactivate and use together. The tool group is activated by the meta tool
    `ResetTools`.

    In high-code scenarios, the tools argument accepts any child classes for
    ToolBase class, and the tool groups supports serialization. To support
    deserialization, you need to register the child class first.
    """

    name: Literal["basic"] | str = Field(
        title="Group Name",
        description="The name of the tool group.",
    )
    """Note the "basic" group is special and represents the default tool group
    that will be always be activated for the agent."""

    description: str = Field(
        title="Group Description",
        description="The description of the tool group.",
    )
    """A description of the tool group from an agent-oriented perspective,
    outlining its capabilities and the conditions under which it should be
    activated."""

    instructions: str | None = Field(
        default=None,
        title="Group Instructions",
        description="The instructions that will be contained when this tool "
        "group is activated.",
    )
    """Instructions included in the meta tool's result upon activation of
    this tool group, guiding the agent on how to properly use the meta tool."""

    tools: list[ToolBase] | None = Field(
        default=None,
        title="Tools",
        description="The tools in this group.",
    )
    """The tools in this group."""

    skills: list[str] | None = Field(
        default=None,
        title="Skills",
        description="The skills in this group.",
    )
    """The skills in this group."""

    mcps: list[MCPClient] | None = Field(
        default=None,
        title="MCP Clients",
        description="The mcps in this group, whose tools will be attached to "
        "this group.",
    )
    """The mcps in this group."""

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        """Override model_dump to serialize tools using ToolRegistry.

        Returns:
            `dict[str, Any]`:
                The serialized tool group configuration.
        """
        data = super().model_dump(**kwargs)

        # Serialize tools using ToolRegistry
        if self.tools:
            data["tools"] = [
                ToolRegistry.serialize_tool(tool) for tool in self.tools
            ]

        # Serialize MCPs (save their configuration)
        if self.mcps:
            data["mcps"] = [
                mcp.to_config() if hasattr(mcp, "to_config") else {}
                for mcp in self.mcps
            ]

        return data

    @classmethod
    def model_validate(cls, obj: Any, **kwargs: Any) -> "ToolGroup":
        """Override model_validate to deserialize tools using ToolRegistry.

        Args:
            obj (`Any`):
                The object to validate and deserialize.

        Returns:
            `ToolGroup`:
                The deserialized tool group instance.
        """
        if isinstance(obj, dict):
            # Deserialize tools using ToolRegistry
            if "tools" in obj and obj["tools"]:
                obj["tools"] = [
                    ToolRegistry.deserialize_tool(tool_config)
                    for tool_config in obj["tools"]
                ]

            # Deserialize MCPs
            if "mcps" in obj and obj["mcps"]:
                obj["mcps"] = [
                    MCPClient.from_config(mcp_config)
                    if hasattr(MCPClient, "from_config")
                    else MCPClient(**mcp_config)
                    for mcp_config in obj["mcps"]
                ]

        return super().model_validate(obj, **kwargs)
