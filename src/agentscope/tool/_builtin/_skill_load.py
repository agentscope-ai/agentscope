# -*- coding: utf-8 -*-
"""Built-in tool to load a skill resource by skill_id and path.

Ported from Java ``SkillLoadTool``.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .._base import ToolBase
from .._response import ToolChunk
from ..._logging import logger
from ...message import TextBlock, ToolResultState
from ...permission import PermissionContext, PermissionDecision, PermissionBehavior

if TYPE_CHECKING:
    from ...skill import SkillCatalog, SkillEntry


class SkillLoadTool(ToolBase):
    """Built-in tool to load a skill resource by skill_id and path.

    Usage::

        load_skill(skill_id="my-skill_workspace", path="SKILL.md")
        load_skill(skill_id="my-skill_workspace", path="scripts/run.py")
    """

    name: str = "load_skill"
    description = (
        "Load and return a skill resource by its skill id and a path relative to "
        "the skill root.\n\n"
        'Use path="SKILL.md" to load the skill\'s markdown documentation.\n'
        "Use exact resource paths listed by the skill, e.g. references/guide.md.\n"
        "Do not use '.', './', directories, or absolute paths."
    )
    is_read_only: bool = True
    is_concurrency_safe: bool = True

    def __init__(self, catalog_ref: Callable[[], "SkillCatalog"]) -> None:
        self._catalog_ref = catalog_ref

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Skill loader is always allowed to be called.",
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        snapshot = self._catalog_ref()
        return {
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": "Unique skill identifier.",
                    "enum": snapshot.ids(),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Exact resource path within the skill. "
                        "Use 'SKILL.md' for the skill's instructions."
                    ),
                },
            },
            "required": ["skill_id", "path"],
        }

    async def __call__(self, skill_id: str, path: str) -> ToolChunk:
        from ...skill import SkillCatalog

        catalog = self._catalog_ref()
        entry = catalog.get(skill_id)
        if entry is None:
            return ToolChunk(
                content=[TextBlock(text=f"Skill not found: '{skill_id}'.")],
                state=ToolResultState.ERROR,
            )

        skill = entry.skill

        # 1. Special case: SKILL.md returns the parsed markdown
        if path == "SKILL.md":
            return self._format_skill_markdown(entry)

        # 2. In-memory resources map
        if path in skill.resources:
            return self._format_resource(entry, path, skill.resources[path])

        # 3. Filesystem fallback (if files_root is set)
        if entry.files_root:
            full_path = Path(entry.files_root) / path
            if full_path.exists() and full_path.is_file():
                try:
                    content = full_path.read_text(encoding="utf-8")
                    return self._format_resource(entry, path, content)
                except Exception as e:
                    return ToolChunk(
                        content=[TextBlock(text=f"Failed to read {path}: {e}")],
                        state=ToolResultState.ERROR,
                    )

        # 4. Not found
        return ToolChunk(
            content=[TextBlock(text=self._format_not_found(entry, path))],
            state=ToolResultState.ERROR,
        )

    def _format_skill_markdown(self, entry: "SkillEntry") -> ToolChunk:
        skill = entry.skill
        lines = [
            f"Successfully loaded skill: {skill.skill_id}",
            "",
            f"Name: {skill.name}",
            f"Description: {skill.description}",
            f"Source: {skill.dir}",
        ]
        if entry.files_root:
            lines.append(f"Files root: {entry.files_root}")
        lines.extend([
            "",
            "Content (SKILL.md):",
            "````markdown",
            skill.markdown,
            "````",
        ])
        return ToolChunk(content=[TextBlock(text="\n".join(lines))])

    def _format_resource(self, entry: "SkillEntry", path: str, content: str) -> ToolChunk:
        lines = [
            f"Successfully loaded resource from skill: {entry.skill.skill_id}",
            f"Resource path: {path}",
        ]
        if entry.files_root:
            lines.append(f"Files root: {entry.files_root}")
        lines.extend([
            "",
            f"Content ({path}):",
            "````",
            content,
            "````",
        ])
        return ToolChunk(content=[TextBlock(text="\n".join(lines))])

    def _format_not_found(self, entry: "SkillEntry", missing_path: str) -> str:
        skill = entry.skill
        available: set[str] = {"SKILL.md"}
        available.update(skill.resources.keys())
        if entry.files_root:
            root = Path(entry.files_root)
            if root.exists():
                for f in root.rglob("*"):
                    if f.is_file():
                        available.add(str(f.relative_to(root)))

        lines = [
            f"Resource not found: '{missing_path}' in skill '{skill.skill_id}'.",
            "",
            "Available resources:",
        ]
        for i, p in enumerate(sorted(available), 1):
            lines.append(f"{i}. {p}")
        return "\n".join(lines)
