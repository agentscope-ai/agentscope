# -*- coding: utf-8 -*-
"""The local skill loader class."""
import asyncio
import os

import aiofiles
import aiofiles.ospath
import frontmatter

from ._base import SkillLoaderBase
from ..._logging import logger
from .._types import Skill


class LocalSkillLoader(SkillLoaderBase):
    """The skill loader that loads skills from a local directory."""

    def __init__(self, directory: str, scan_subdir: bool = False) -> None:
        """Initialize the loader with the directory.

        Args:
            directory (`str`):
                The directory to load skills from.
            scan_subdir (`bool`, defaults to False):
                Whether to scan subdirectories. Defaults to False (only
                scan current directory).
        """
        self.directory = os.path.abspath(directory)
        self.scan_subdir = scan_subdir
        self._cache: dict[str, Skill] = {}

    async def _load_single_skill(self, skill_root: str) -> Skill | None:
        """Load a single skill from a skill root directory.

        Args:
            skill_root (`str`): The skill root directory containing SKILL.md.

        Returns:
            `Skill | None`: A Skill object or None if loading failed.
        """
        skill_md_path = os.path.join(skill_root, "SKILL.md")

        try:
            # Check if SKILL.md exists
            if not await aiofiles.ospath.isfile(skill_md_path):
                return None

            # Get file modification time
            updated_at = await aiofiles.ospath.getmtime(skill_md_path)

            # Check cache: if cached skill exists and updated_at matches,
            # return cached
            if skill_root in self._cache:
                cached_skill = self._cache[skill_root]
                if cached_skill.updated_at == updated_at:
                    return cached_skill

            # Read and parse SKILL.md
            async with aiofiles.open(
                skill_md_path,
                "r",
                encoding="utf-8",
            ) as f:
                content_str = await f.read()
                content = frontmatter.loads(content_str)

            name = content.get("name")
            description = content.get("description")

            if not name or not description:
                logger.warning(
                    "SKILL.md in %s is missing required fields "
                    "(name or description). Skipping.",
                    skill_root,
                )
                return None

            skill = Skill(
                name=str(name),
                description=str(description),
                dir=skill_root,
                markdown=content.content,
                updated_at=updated_at,
            )

            # Update cache
            self._cache[skill_root] = skill

            return skill

        except Exception as e:
            logger.warning(
                "Failed to load skill from %s: %s",
                skill_root,
                str(e),
            )
            return None

    async def list_skills(self) -> list[Skill]:
        """List all the available skills from the directory.

        This method will:
        1. Search for SKILL.md in the current directory
        2. If scan_subdir is True, search for SKILL.md in all subdirectories
        3. Load all SKILL.md files concurrently

        Returns:
            `list[Skill]`: A list of Skill objects.
        """
        try:
            # Check if directory exists
            if not await aiofiles.ospath.isdir(self.directory):
                logger.warning(
                    "Skill directory %s does not exist.",
                    self.directory,
                )
                return []

            # Find all directories containing SKILL.md
            def _find_skill_dirs() -> list[str]:
                """Find all directories containing SKILL.md file."""
                dirs = []

                if os.path.isfile(os.path.join(self.directory, "SKILL.md")):
                    dirs.append(self.directory)

                if self.scan_subdir:
                    for root, _, filenames in os.walk(self.directory):
                        if root == self.directory:
                            continue
                        if "SKILL.md" in filenames:
                            dirs.append(root)

                return dirs

            skill_dirs = await asyncio.to_thread(_find_skill_dirs)

            if not skill_dirs:
                logger.info(
                    "No SKILL.md files found in %s",
                    self.directory,
                )
                return []

            # Load all skills concurrently
            tasks = [
                self._load_single_skill(skill_dir) for skill_dir in skill_dirs
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Filter out None results and exceptions
            skills: list = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(
                        "Failed to load skill from %s: %s",
                        skill_dirs[i],
                        str(result),
                    )
                elif result is not None:
                    skills.append(result)

            return skills

        except Exception as e:
            logger.warning(
                "Failed to list skills from directory %s: %s",
                self.directory,
                str(e),
            )
            return []
