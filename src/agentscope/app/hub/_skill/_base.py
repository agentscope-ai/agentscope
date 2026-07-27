# -*- coding: utf-8 -*-
""""""
from abc import ABC, abstractmethod

from ....skill import Skill


class SkillHubBase(ABC):
    """The Skill Hub base class, responsible for get available skills from
    the providers."""

    @abstractmethod
    async def list_skills(
        self,
        user_id: str,
    ) -> list[Skill]:
        """Get all the available skills from the provider.

        Args:
            user_id (`str`):
                The user identifier to query the skill.
        """
