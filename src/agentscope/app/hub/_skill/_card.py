# -*- coding: utf-8 -*-
"""The skill card models."""
from typing import Self

from pydantic import BaseModel, Field, model_validator


class SkillCard(BaseModel):
    """A single skill listing on a hub.

    Unlike an :class:`MCPCard` there is nothing to configure: installing
    a skill copies its files into the workspace, so no input form and no
    template substitution are involved.

    :attr:`id` addresses the card on its hub — together with
    :attr:`hub_id` it names the card globally; :attr:`name` is the
    skill's own name.
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
        description="The skill name.",
    )

    description: str = Field(
        default="",
        title="Description",
        description="The user-facing description of the skill.",
    )

    display_name: str | None = Field(
        default=None,
        title="Display Name",
        description="The user-facing name, falling back to ``name``.",
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
        description="Who published the skill, when the hub says.",
    )

    icon_url: str | None = Field(
        default=None,
        title="Icon URL",
        description=(
            "An image representing the skill, for the card and the "
            "detail view. ``None`` when the hub offers none — the UI is "
            "expected to fall back rather than show a broken image."
        ),
    )

    installs: int | None = Field(
        default=None,
        title="Installs",
        description=(
            "How many times this skill has been installed. ``None`` when "
            "the hub does not count installs, which is not the same as "
            "zero — render it only when set."
        ),
    )

    downloads: int | None = Field(
        default=None,
        title="Downloads",
        description=(
            "How many times this skill has been downloaded. ``None`` "
            "when the hub does not count downloads."
        ),
    )

    url: str | None = Field(
        default=None,
        title="URL",
        description=(
            "The skill's page on the hub's website, for a 'view on the "
            "hub' link. ``None`` when the hub has no web presence."
        ),
    )

    markdown: str | None = Field(
        default=None,
        title="Markdown",
        description=(
            "The ``SKILL.md`` body. Left ``None`` when browsing so the "
            "catalog does not fetch one file per card; populated by "
            "``get_skill``."
        ),
    )

    metadata: dict = Field(
        default_factory=dict,
        title="Metadata",
        description="The hub-specific extra fields carried verbatim.",
    )

    @model_validator(mode="after")
    def _default_id_to_name(self) -> Self:
        """Fall back to ``name`` when the hub exposes no separate id."""
        if not self.id:
            self.id = self.name
        return self


class SkillHubPage(BaseModel):
    """One page of skill cards."""

    cards: list[SkillCard] = Field(
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
