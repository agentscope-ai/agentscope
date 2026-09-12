# -*- coding: utf-8 -*-
"""Schemas for installing and editing MCPs from a hub."""
from pydantic import BaseModel, Field

from ...storage import MCPRecord


class InstallMCPRequest(BaseModel):
    """The body of an MCP install call."""

    name: str | None = Field(
        default=None,
        description=(
            "The name to install under, defaulting to the card's name. "
            "Must match ``[a-zA-Z0-9_-]+``; use it to resolve a clash "
            "with an MCP already in the library."
        ),
    )
    values: dict = Field(
        default_factory=dict,
        description=(
            "The answers to the card's ``inputs_schema``, e.g. API keys."
        ),
    )


class MCPView(BaseModel):
    """One installed MCP, as shown in the user's library.

    The rendered ``mcp_config`` is deliberately left out: it carries the
    inputs the user filled in at install time, API keys included, and
    nothing on this screen needs them.
    """

    id: str = Field(description="The installed-MCP record id.")
    name: str = Field(description="The MCP name, unique for this user.")
    is_stateful: bool = Field(description="Whether it needs a session.")
    enabled: bool = Field(description="Whether the user has it turned on.")
    display_name: str | None = Field(
        default=None,
        description="The card's user-facing name at install time.",
    )
    description: str = Field(
        default="",
        description="The card's description at install time.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="The card's tags at install time.",
    )
    author: str | None = Field(
        default=None,
        description="Who published the MCP, at install time.",
    )
    icon_url: str | None = Field(
        default=None,
        description="The card's icon at install time.",
    )
    url: str | None = Field(
        default=None,
        description="The MCP's page on the hub's website.",
    )
    hub_id: str | None = Field(
        default=None,
        description="The hub it came from, or null when added by hand.",
    )
    card_id: str | None = Field(
        default=None,
        description="The card's id on that hub.",
    )
    version: str | None = Field(
        default=None,
        description="The card version installed.",
    )

    @classmethod
    def from_record(cls, record: MCPRecord) -> "MCPView":
        """Project a stored record onto its secret-free view.

        Args:
            record (`MCPRecord`):
                The stored record.

        Returns:
            `MCPView`:
                The view safe to return to the client.
        """
        return cls(
            id=record.id,
            name=record.client.name,
            is_stateful=record.client.is_stateful,
            enabled=record.enabled,
            display_name=record.display_name,
            description=record.description,
            tags=record.tags,
            author=record.author,
            icon_url=record.icon_url,
            url=record.url,
            hub_id=record.hub_id,
            card_id=record.card_id,
            version=record.version,
        )


class UpdateMCPRequest(BaseModel):
    """The body of a library edit. Omitted fields are left alone."""

    name: str | None = Field(
        default=None,
        description=(
            "A new name. Must match ``[a-zA-Z0-9_-]+`` and stay unique "
            "for this user."
        ),
    )
    values: dict | None = Field(
        default=None,
        description=(
            "New answers to the card's ``inputs_schema``, merged over "
            "the stored ones — send only the keys that changed. The "
            "config is re-rendered from the card, so this needs the "
            "originating hub to still be registered and reachable."
        ),
    )
    enabled: bool | None = Field(
        default=None,
        description="Whether the MCP is turned on.",
    )
