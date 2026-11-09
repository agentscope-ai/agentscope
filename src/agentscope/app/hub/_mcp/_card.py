# -*- coding: utf-8 -*-
"""The MCP card models."""
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from ....mcp import StdioMCPConfig, HttpMCPConfig


class MCPCard(BaseModel):
    """A single MCP listing on a hub.

    A card is a *template*, not a connectable client:
    :attr:`config_template` carries ``${...}`` placeholders whose values
    the user supplies at install time, described by
    :attr:`inputs_schema`.

    Two identifiers, deliberately separate:

    - :attr:`id` addresses the card on its hub and is what
      :attr:`ref` embeds. It may be any opaque string.
    - :attr:`name` is the default name of the *installed* client, which
      must match ``[a-zA-Z0-9_-]+`` because it composes the model-facing
      tool name ``mcp__{name}__{tool}``. Cards are not rejected for a
      bad name — the install call is, so browsing a registry with odd
      slugs still works.
    """

    hub_id: str = Field(
        title="Hub ID",
        description="The id of the hub this card came from.",
    )

    id: str = Field(
        default="",
        title="ID",
        description=(
            "The identifier addressing this card on its hub. Defaults to "
            "``name`` for registries that expose no separate id."
        ),
    )

    name: str = Field(
        title="Name",
        description="The default name of the installed MCP client.",
    )

    display_name: str | None = Field(
        default=None,
        title="Display Name",
        description="The user-facing name, falling back to ``name``.",
    )

    description: str = Field(
        default="",
        title="Description",
        description="The user-facing description of the MCP.",
    )

    tags: list[str] = Field(
        default_factory=list,
        title="Tags",
        description="The tags categorizing this card.",
    )

    version: str | None = Field(
        default=None,
        title="Version",
        description="The card version reported by the hub.",
    )

    updated_at: float | None = Field(
        default=None,
        title="Updated At",
        description="The last-updated timestamp (Unix epoch seconds).",
    )

    author: str | None = Field(
        default=None,
        title="Author",
        description="Who published the MCP, when the hub says.",
    )

    icon_url: str | None = Field(
        default=None,
        title="Icon URL",
        description=(
            "An image representing the MCP. ``None`` when the hub offers "
            "none — the UI is expected to fall back rather than show a "
            "broken image."
        ),
    )

    url: str | None = Field(
        default=None,
        title="URL",
        description=(
            "The MCP's page on the hub's website, for a 'view on the "
            "hub' link. ``None`` when the hub has no web presence."
        ),
    )

    readme: str | None = Field(
        default=None,
        title="Readme",
        description=(
            "The server's long-form documentation, shown in the detail "
            "view where a skill shows its ``SKILL.md``. ``None`` when "
            "the hub publishes none."
        ),
    )

    installs: int | None = Field(
        default=None,
        title="Installs",
        description=(
            "How many times this MCP has been installed. ``None`` when "
            "the hub does not count installs, which is not the same as "
            "zero — render it only when set."
        ),
    )

    downloads: int | None = Field(
        default=None,
        title="Downloads",
        description=(
            "How many times this MCP has been downloaded. ``None`` when "
            "the hub does not count downloads."
        ),
    )

    is_stateful: bool = Field(
        default=True,
        title="Stateful",
        description=(
            "Whether the installed client requires explicit connect() "
            "and close(). Forwarded to ``MCPClient.is_stateful``."
        ),
    )

    auth: Literal["none", "inputs"] = Field(
        default="inputs",
        title="Auth",
        description=(
            "How the user authenticates: ``none`` for public servers, "
            "``inputs`` for the ``inputs_schema`` form. Reserved as a "
            "discriminator so an ``oauth`` flow can be added later "
            "without breaking the API."
        ),
    )

    inputs_schema: dict = Field(
        default_factory=dict,
        title="Inputs Schema",
        description=(
            "A JSON Schema (``type: object``) describing the values the "
            "user must supply. Secret fields carry the standard "
            "``writeOnly: true`` and ``format: password`` keywords so "
            "the frontend renders them masked."
        ),
    )

    config_template: StdioMCPConfig | HttpMCPConfig = Field(
        discriminator="type",
        title="Config Template",
        description=(
            "The MCP config whose string leaves may hold ``${name}`` "
            "placeholders referring to ``inputs_schema`` properties."
        ),
    )

    @model_validator(mode="after")
    def _default_id_to_name(self) -> Self:
        """Fall back to ``name`` when the hub exposes no separate id."""
        if not self.id:
            self.id = self.name
        return self


class MCPHubPage(BaseModel):
    """One page of MCP cards."""

    cards: list[MCPCard] = Field(
        title="Cards",
        description="The cards on this page.",
    )

    next_cursor: str | None = Field(
        default=None,
        title="Next Cursor",
        description=(
            "The opaque cursor fetching the following page, or ``None`` "
            "when this is the last one."
        ),
    )
