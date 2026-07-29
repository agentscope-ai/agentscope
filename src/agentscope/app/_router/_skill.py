# -*- coding: utf-8 -*-
"""Skill router — the user's own library of installed skills.

The skill counterpart of :mod:`._mcp`: this is the user-level collection
an install lands in, distinct from ``/workspace/skill``, which manages
the skills present in one session's workspace.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..deps import get_current_user_id, get_storage
from ..storage import SkillRecord, StorageBase

skill_router = APIRouter(prefix="/skill", tags=["skill"])


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


@skill_router.get("")
async def list_skills(
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> list[SkillView]:
    """Return every skill the user has installed, ordered by name."""
    records = await storage.list_installed_skills(user_id)
    return sorted(
        (SkillView.from_record(r) for r in records),
        key=lambda view: view.name,
    )


@skill_router.get("/{skill_id}")
async def get_skill(
    skill_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> SkillRecord:
    """Return one installed skill, including its ``SKILL.md`` body."""
    record = await storage.get_installed_skill(user_id, skill_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No installed skill with id {skill_id!r}.",
        )
    return record


@skill_router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    skill_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> None:
    """Remove a skill from the user's library.

    Workspaces that already hold this skill keep their copy — the files
    were extracted into the workspace, and this record was only where
    they came from.
    """
    if not await storage.delete_installed_skill(user_id, skill_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No installed skill with id {skill_id!r}.",
        )
