# -*- coding: utf-8 -*-
"""Skill repository backed by AbstractFilesystem with lazy resource access.

Skills are discovered by globbing ``SKILL.md`` under *skills_relative_dir*.
Only the SKILL.md text is read at registration time; resources are fetched
on demand through :meth:`resources_for`.

Deletes are non-destructive: skill directories are moved under
``.archive/<name>-<ts>/`` rather than removed.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from ..filesystem import AbstractFilesystem
from ..filesystem._models import ReadResult
from ..skill import Skill
from .._logging import logger

SKILL_FILE = "SKILL.md"
ARCHIVE_PREFIX = ".archive"
DEFAULT_SOURCE = "workspace"


class WorkspaceSkillRepository:
    """Filesystem-backed skill repository.

    Args:
        filesystem: Backing filesystem (non-null).
        skills_relative_dir: Relative directory holding ``<skill>/SKILL.md``.
        context_supplier: Callable returning the runtime context dict on each call.
        source: Source identifier attached to loaded skills.
        writable: Whether :meth:`save` and :meth:`delete` are permitted.
    """

    def __init__(
        self,
        filesystem: AbstractFilesystem,
        skills_relative_dir: str,
        context_supplier: Callable[[], dict[str, Any]],
        *,
        source: str = DEFAULT_SOURCE,
        writable: bool = False,
    ) -> None:
        self._fs = filesystem
        self._skills_dir = skills_relative_dir
        self._context_supplier = context_supplier
        self._source = source or DEFAULT_SOURCE
        self._writable = writable

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    async def get_skill(self, name: str) -> Skill | None:
        if not name:
            return None
        for skill in await self.get_all_skills():
            if skill.name == name:
                return skill
        return None

    async def get_all_skill_names(self) -> list[str]:
        skills = await self.get_all_skills()
        return [s.name for s in skills]

    async def get_all_skills(self) -> list[Skill]:
        ctx = self._current_context()
        try:
            glob = await self._fs.glob(ctx, f"**/{SKILL_FILE}", self._skills_dir)
        except Exception as e:
            logger.debug("Filesystem glob for skills failed: %s", e)
            return []
        if not glob.paths:
            return []

        skills: list[Skill] = []
        for path in glob.paths:
            if not path:
                continue
            if _has_metadata_ancestor(path, self._skills_dir):
                continue
            try:
                rr = await self._fs.read(ctx, path, 0, 0)
                content = rr.content
                if not content:
                    continue
                skill = _parse_skill_md(content, self._source, path)
                if skill:
                    skills.append(skill)
            except Exception as e:
                logger.warning("Failed to load skill from '%s': %s", path, e)
        return skills

    async def skill_exists(self, name: str) -> bool:
        return await self.get_skill(name) is not None

    # ------------------------------------------------------------------
    # Lazy resources
    # ------------------------------------------------------------------

    async def resources_for(self, skill_name: str) -> dict[str, str]:
        if not skill_name:
            return {}
        ctx = self._current_context()
        skill_dir = self._skill_dir_relative(skill_name)
        try:
            glob = await self._fs.glob(ctx, "**/*", skill_dir)
        except Exception as e:
            logger.debug("SkillResources.list() failed: %s", e)
            return {}

        resources: dict[str, str] = {}
        for path in glob.paths:
            rel = _relative_to(path, skill_dir)
            if not rel or rel == SKILL_FILE:
                continue
            try:
                rr = await self._fs.read(ctx, path, 0, 0)
                if rr.content is not None:
                    resources[rel] = rr.content
            except Exception as e:
                logger.debug("Failed to read skill resource %s: %s", path, e)
        return resources

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    async def save(self, skills: list[Skill], *, force: bool = False) -> bool:
        if not self._writable:
            logger.warning("WorkspaceSkillRepository is read-only; save() ignored")
            return False
        if not skills:
            return False
        all_ok = True
        for skill in skills:
            if not skill or not skill.name:
                all_ok = False
                continue
            if not force and await self.skill_exists(skill.name):
                logger.debug("Skill '%s' already exists; skipping", skill.name)
                all_ok = False
                continue
            try:
                await self._write_skill(skill)
            except Exception as e:
                logger.warning("Failed to save skill '%s': %s", skill.name, e)
                all_ok = False
        return all_ok

    async def delete(self, skill_name: str) -> bool:
        if not self._writable:
            logger.warning("WorkspaceSkillRepository is read-only; delete() ignored")
            return False
        if not skill_name:
            return False
        existing = await self.get_skill(skill_name)
        if existing is None:
            return False
        ctx = self._current_context()
        src = self._skill_dir_relative(skill_name)
        dst = self._archive_dest_relative(skill_name)
        try:
            await self._fs.move(ctx, src, dst)
            return True
        except Exception as e:
            logger.warning("Exception archiving skill '%s': %s", skill_name, e)
            return False

    # ------------------------------------------------------------------
    # Sub-file operations
    # ------------------------------------------------------------------

    async def read_skill_file(self, skill_name: str, rel_path: str) -> str | None:
        if not skill_name or not rel_path:
            return None
        path = f"{self._skill_dir_relative(skill_name)}/{rel_path}"
        try:
            rr = await self._fs.read(self._current_context(), path, 0, 0)
            return rr.content
        except Exception as e:
            logger.debug("readSkillFile(%s, %s) failed: %s", skill_name, rel_path, e)
            return None

    async def write_skill_file(
        self, skill_name: str, rel_path: str, content: str,
    ) -> bool:
        if not self._writable:
            return False
        if not skill_name or not rel_path or content is None:
            return False
        path = f"{self._skill_dir_relative(skill_name)}/{rel_path}"
        try:
            await self._fs.write(self._current_context(), path, content)
            return True
        except Exception as e:
            logger.warning("writeSkillFile(%s, %s) failed: %s", skill_name, rel_path, e)
            return False

    async def delete_skill_file(self, skill_name: str, rel_path: str) -> bool:
        if not self._writable:
            return False
        if not skill_name or not rel_path:
            return False
        path = f"{self._skill_dir_relative(skill_name)}/{rel_path}"
        try:
            await self._fs.delete(self._current_context(), path)
            return True
        except Exception as e:
            logger.warning("deleteSkillFile(%s, %s) failed: %s", skill_name, rel_path, e)
            return False

    def resolve_skill_root(self, skill_name: str) -> str:
        return self._skill_dir_relative(skill_name)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def filesystem(self) -> AbstractFilesystem:
        return self._fs

    @property
    def skills_relative_dir(self) -> str:
        return self._skills_dir

    @property
    def is_writeable(self) -> bool:
        return self._writable

    def set_writeable(self, writeable: bool) -> None:
        self._writable = writeable

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _current_context(self) -> dict[str, Any]:
        try:
            ctx = self._context_supplier()
            return ctx if ctx is not None else {}
        except Exception:
            return {}

    async def _write_skill(self, skill: Skill) -> None:
        ctx = self._current_context()
        skill_md = _to_markdown(skill)
        skill_dir = self._skill_dir_relative(skill.name)
        skill_md_path = f"{skill_dir}/{SKILL_FILE}"
        await self._fs.write(ctx, skill_md_path, skill_md)
        if skill.resources:
            for rel_path, content in skill.resources.items():
                if not rel_path or ".." in rel_path or rel_path.startswith("/"):
                    continue
                target = f"{skill_dir}/{rel_path}"
                await self._fs.write(ctx, target, content)

    def _skill_dir_relative(self, name: str) -> str:
        return f"{self._skills_dir}/{name}"

    def _archive_dest_relative(self, name: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"{self._skills_dir}/{ARCHIVE_PREFIX}/{name}-{ts}"


def _parse_skill_md(content: str, source: str, path: str) -> Skill | None:
    """Parse a SKILL.md into a Skill dataclass."""
    from yaml import safe_load

    if not content.startswith("---"):
        return None

    try:
        _, rest = content.split("---", 1)
        parts = rest.split("---", 1)
        if len(parts) < 2:
            return None
        frontmatter_text = parts[0].strip()
        body = parts[1].strip()
        meta = safe_load(frontmatter_text) or {}
        name = meta.get("name", "")
        description = meta.get("description", "")
        if not name or not description:
            return None
        return Skill(
            name=name,
            description=description,
            dir=path,
            markdown=body,
            updated_at=time.time(),
            metadata={k: v for k, v in meta.items() if k not in ("name", "description")},
            resources={},
        )
    except Exception as e:
        logger.debug("Failed to parse SKILL.md at %s: %s", path, e)
        return None


def _to_markdown(skill: Skill) -> str:
    """Reassemble a skill back into markdown with YAML frontmatter."""
    from yaml import dump

    ordered: dict[str, Any] = {"name": skill.name, "description": skill.description}
    if skill.metadata:
        for k, v in skill.metadata.items():
            if k not in ("name", "description"):
                ordered[k] = v
    fm = dump(ordered, default_flow_style=False, allow_unicode=True).strip()
    body = skill.markdown or ""
    return f"---\n{fm}\n---\n{body}\n"


def _has_metadata_ancestor(path: str, base: str) -> bool:
    """Return True if the path lives inside a metadata subtree (_*, .*)."""
    if path is None or base is None:
        return False
    normalized = path.replace("\\", "/")
    b = base.replace("\\", "/")
    if not b or b == ".":
        trimmed = normalized[1:] if normalized.startswith("/") else normalized
        slash = trimmed.find("/")
        if slash <= 0:
            return False
        first = trimmed[0]
        return first == "_" or first == "."
    if b.startswith("/"):
        b = b[1:]
    if b.endswith("/"):
        b = b[:-1]
    marker = "/" + b + "/"
    idx = normalized.find(marker)
    if idx < 0:
        if normalized.startswith(b + "/"):
            after = len(b) + 1
            if after >= len(normalized):
                return False
            return normalized[after] in "_."
        return False
    after = idx + len(marker)
    if after >= len(normalized):
        return False
    return normalized[after] in "_."


def _relative_to(path: str, skill_dir: str) -> str:
    """Strip skill_dir prefix from path."""
    norm_path = path.replace("\\", "/")
    norm_dir = skill_dir.replace("\\", "/")
    if norm_dir.startswith("/"):
        norm_dir = norm_dir[1:]
    if norm_dir.endswith("/"):
        norm_dir = norm_dir[:-1]
    marker = "/" + norm_dir + "/"
    idx = norm_path.rfind(marker)
    if idx >= 0:
        return norm_path[idx + len(marker):]
    prefix = norm_dir + "/"
    if norm_path.startswith(prefix):
        return norm_path[len(prefix):]
    return norm_path
