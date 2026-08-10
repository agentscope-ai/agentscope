# -*- coding: utf-8 -*-
"""The installed-skill record."""
from pydantic import Field

from ._base import _RecordBase


class SkillRecord(_RecordBase):
    """One skill a user has installed, at the user level rather than in
    any one workspace — the skill counterpart of :class:`MCPRecord`.

    Unlike an MCP, whose whole substance is a config small enough to
    store inline, a skill is a directory of files. Those files are *not*
    kept here: the record holds where the skill came from and what it
    says it does, and the archive is re-fetched from the hub when the
    skill is actually put into a workspace.

    TODO: store the archive so the library survives the hub dropping the
    card or going offline. That needs a blob store, and the one this app
    has is only wired up alongside ``knowledge_base_manager``.
    """

    user_id: str

    name: str
    """The skill name, unique for this user. This is the handle a
    workspace refers to it by, matching how MCP names work."""

    display_name: str | None = Field(default=None)
    """The card's user-facing name, snapshotted at install time."""

    description: str = Field(default="")
    """The card's description, snapshotted at install time."""

    tags: list[str] = Field(default_factory=list)
    """The card's tags, snapshotted at install time."""

    author: str | None = Field(default=None)
    """Who published the skill, snapshotted at install time."""

    icon_url: str | None = Field(default=None)
    """The card's icon, snapshotted at install time. Kept so the library
    looks like the hub listing it came from rather than losing its
    identity the moment it is installed."""

    url: str | None = Field(default=None)
    """The skill's page on the hub's website, snapshotted at install
    time, so the library can still link back to where it came from."""

    markdown: str = Field(default="")
    """The ``SKILL.md`` body, snapshotted at install time. Enough to show
    the user what the skill does without going back to the hub."""

    hub_id: str | None = Field(default=None)
    """The hub this was installed from, or ``None`` when added by hand."""

    card_id: str | None = Field(default=None)
    """The card's id on that hub. Paired with ``hub_id`` it answers
    "where did this come from" and "is there a newer version"."""

    version: str | None = Field(default=None)
    """The card version installed, as reported by the hub at the time."""

    enabled: bool = Field(default=True)
    """Whether the skill should be present in the user's workspaces."""
