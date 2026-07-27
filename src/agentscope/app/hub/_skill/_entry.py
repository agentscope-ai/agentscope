# -*- coding: utf-8 -*-
""""""
from dataclasses import dataclass, field


@dataclass
class SkillEntry:
    """The skill entry."""

    name: str

    description: str

    display_name: str | None = None

    tags: list[str] = field(default_factory=list)

    version: str | None = None

    metadata: dict = field(default_factory=dict)
