# -*- coding: utf-8 -*-
"""Schemas for skills installed from a hub."""
from pydantic import BaseModel, Field

from ...storage import SkillRecord


class SkillView(BaseModel):
    """One installed skill, as shown in the user's library."""

    id: str = Field(description="The installed-skill record id.")
    name: str = Field(description="The skill name, unique for this user.")
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
        description="Who published the skill, at install time.",
    )
    icon_url: str | None = Field(
        default=None,
        description="The card's icon at install time.",
    )
    url: str | None = Field(
        default=None,
        description="The skill's page on the hub's website.",
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
    def from_record(cls, record: SkillRecord) -> "SkillView":
        """Project a stored record onto its list view.

        The ``SKILL.md`` body is left out — it is long enough to bloat a
        list response, and only a detail view needs it.

        Args:
            record (`SkillRecord`):
                The stored record.

        Returns:
            `SkillView`:
                The view shown in the library list.
        """
        return cls(
            id=record.id,
            name=record.name,
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
