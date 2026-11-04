# -*- coding: utf-8 -*-
"""The installed-MCP record."""
from pydantic import Field, computed_field

from ._base import _RecordBase
from ....mcp import MCPClient


class MCPRecord(_RecordBase):
    """One MCP a user has installed, at the user level rather than in any
    one workspace.

    This table is the *desired* state: a workspace's ``.mcp`` file is the
    actual state, and the app converges the two. Every MCP a user owns
    has a record here — installing from a hub and adding one by hand both
    write one — because a workspace MCP with no record would be
    indistinguishable from one the user removed.

    The workspace relation is derived, not stored: it joins on the MCP
    name, which is why :attr:`MCPClient.name` must be unique per user.
    """

    user_id: str

    client: MCPClient
    """The connectable instance, ready to hand to ``workspace.add_mcp``.
    Its ``name`` is the user-unique handle the workspace joins on."""

    # mypy cannot see through the decorator pair; the ignore is what
    # pydantic's own docs prescribe for a computed property.
    @computed_field  # type: ignore[misc]
    @property
    def name(self) -> str:
        """``client.name`` lifted to the top level so storage backends can
        index it. Computed rather than stored, so it cannot go stale when
        the client is renamed, and records written before it existed need
        no migration."""
        return self.client.name

    display_name: str | None = Field(default=None)
    """The card's user-facing name, snapshotted at install time."""

    description: str = Field(default="")
    """The card's description, snapshotted at install time."""

    author: str | None = Field(default=None)
    """Who published the MCP, snapshotted at install time."""

    icon_url: str | None = Field(default=None)
    """The card's icon, snapshotted at install time."""

    url: str | None = Field(default=None)
    """The MCP's page on the hub's website, snapshotted at install time."""

    tags: list[str] = Field(default_factory=list)
    """The card's tags, snapshotted at install time.

    The display fields above are copied rather than looked up on read: an
    MCP added by hand has no card to look up, and a hub that has gone
    offline — or dropped the card — would otherwise leave the library
    blank. The cost is that they go stale if the card changes upstream,
    which the version check has to refresh anyway.
    """

    values: dict = Field(default_factory=dict)
    """The answers the user gave the card's ``inputs_schema``, e.g. API
    keys.

    Kept so the config can be re-rendered without the user retyping
    everything — changing one key, or moving to a newer card version.
    The secret is already in ``client.mcp_config`` in plaintext, so
    holding it here too widens nothing; it is simply not projected onto
    any response.
    """

    hub_id: str | None = Field(default=None)
    """The hub this was installed from, or ``None`` when added by hand."""

    card_id: str | None = Field(default=None)
    """The card's id on that hub. Paired with ``hub_id`` it answers
    "where did this come from" and "is there a newer version"."""

    version: str | None = Field(default=None)
    """The card version installed, as reported by the hub at the time."""

    enabled: bool = Field(default=True)
    """Whether the MCP should be present in the user's workspaces. A
    disabled record is kept so re-enabling does not lose its config."""
