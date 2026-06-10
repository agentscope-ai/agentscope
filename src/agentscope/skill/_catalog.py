# -*- coding: utf-8 -*-
"""Skill catalog and prompt builder ported from Java harness.

Provides:
- SkillCatalog: immutable snapshot of visible skills keyed by skill_id
- SkillEntry: per-skill wrapper with resources and files_root
- SkillPromptBuilder: renders <available_skills> XML block for system prompt
- SkillRuntime: aggregates catalog, load tool, and prompt builder
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .._logging import logger

if TYPE_CHECKING:
    from ..tool import Toolkit


# ------------------------------------------------------------------
# SkillEntry
# ------------------------------------------------------------------
@dataclass
class SkillEntry:
    """Per-skill wrapper with harness-only metadata.

    Args:
        skill: The underlying Skill instance.
        files_root: Absolute path root for shell execution; may be None.
    """

    skill: "Skill"
    files_root: str | None = None


# ------------------------------------------------------------------
# SkillCatalog
# ------------------------------------------------------------------
class SkillCatalog:
    """Immutable snapshot of skills visible to a single prompt pass."""

    def __init__(self, entries: dict[str, SkillEntry]) -> None:
        self._entries = dict(entries)

    @classmethod
    def empty(cls) -> "SkillCatalog":
        return cls({})

    @classmethod
    def from_entries(cls, ordered: list[SkillEntry]) -> "SkillCatalog":
        d: dict[str, SkillEntry] = {}
        for e in ordered:
            if e is None:
                continue
            d[e.skill.skill_id] = e
        return cls(d)

    def get(self, skill_id: str) -> SkillEntry | None:
        return self._entries.get(skill_id)

    def all(self) -> list[SkillEntry]:
        return list(self._entries.values())

    def ids(self) -> list[str]:
        return list(self._entries.keys())

    def is_empty(self) -> bool:
        return not self._entries

    def size(self) -> int:
        return len(self._entries)


# ------------------------------------------------------------------
# SkillPromptBuilder
# ------------------------------------------------------------------
class SkillPromptBuilder:
    """Renders the <available_skills> system-prompt block from a catalog."""

    INDENT = "  "
    _XML_TAG_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")

    DEFAULT_HEADER = (
        "\n## Available Skills\n\n"
        "<usage>\n"
        "Skills provide specialized capabilities and domain knowledge. "
        "Use them when they match your current task.\n\n"
        "How to use skills:\n"
        '- Load skill: load_skill(skill_id="<skill-id>", path="SKILL.md")\n'
        "- The skill will be activated and its documentation loaded\n"
        "- Additional resources can be loaded with the same tool and other paths\n\n"
        "Example:\n"
        '1. load_skill(skill_id="data-analysis_workspace", path="SKILL.md")\n'
        "2. Follow the instructions returned by the skill\n\n"
        "Metadata is rendered as XML under each <skill> element.\n"
        "</usage>\n\n"
        "<available_skills>\n\n"
    )

    DEFAULT_CODE_EXECUTION = (
        "\n## Code Execution\n\n"
        "<code_execution>\n"
        "You have access to shell tools. Each skill in <available_skills> "
        "includes a <files-root> element giving the absolute path to that skill's files.\n\n"
        "Workflow:\n"
        "1. After loading a skill, look at its <files-root>\n"
        "2. List its files:    ls <files-root>/\n"
        "3. Run scripts:       python3 <files-root>/scripts/<script-name>\n"
        "4. Always use absolute paths derived from <files-root>\n"
        "5. If a script exists for the task, run it directly\n"
        "</code_execution>\n"
    )

    def __init__(
        self,
        header: str | None = None,
        code_execution_instruction: str | None = None,
        expose_all_metadata: bool = True,
    ) -> None:
        self.header = header if header else self.DEFAULT_HEADER
        self.code_execution = (
            code_execution_instruction
            if code_execution_instruction
            else self.DEFAULT_CODE_EXECUTION
        )
        self.expose_all_metadata = expose_all_metadata

    def render(self, catalog: SkillCatalog) -> str:
        if catalog.is_empty():
            return ""
        sb: list[str] = []
        any_entry = False
        any_with_files_root = False

        for entry in catalog.all():
            if not any_entry:
                sb.append(self.header)
                any_entry = True
            self._append_skill(sb, entry)
            if entry.files_root:
                any_with_files_root = True

        if not any_entry:
            return ""
        sb.append("</available_skills>\n")
        if any_with_files_root:
            sb.append(self.code_execution)
        return "".join(sb)

    def _append_skill(self, sb: list[str], entry: SkillEntry) -> None:
        skill = entry.skill
        sb.append("<skill>\n")
        meta = self._metadata_view(skill)
        for key, value in meta.items():
            if value is None:
                continue
            self._append_xml_node(sb, key, value, 1)
        self._append_xml_node(sb, "skill-id", skill.skill_id, 1)
        if entry.files_root:
            self._append_xml_node(sb, "files-root", entry.files_root, 1)
        sb.append("</skill>\n\n")

    def _metadata_view(self, skill: "Skill") -> dict[str, Any]:
        if self.expose_all_metadata:
            return dict(skill.metadata)
        return {"name": skill.name, "description": skill.description}

    def _append_xml_node(
        self, sb: list[str], key: str, value: Any, indent_level: int
    ) -> None:
        if value is None:
            return
        indent = self.INDENT * indent_level
        valid_tag = bool(self._XML_TAG_NAME.fullmatch(key))
        open_tag = f"<{key}>" if valid_tag else f'<entry key="{self._escape_xml(key)}">'
        close_tag = f"</{key}>" if valid_tag else "</entry>"

        if isinstance(value, dict):
            sb.append(f"{indent}{open_tag}\n")
            for k, v in value.items():
                self._append_xml_node(sb, str(k), v, indent_level + 1)
            sb.append(f"{indent}{close_tag}\n")
        elif isinstance(value, list):
            sb.append(f"{indent}{open_tag}\n")
            for item in value:
                self._append_xml_node(sb, "item", item, indent_level + 1)
            sb.append(f"{indent}{close_tag}\n")
        else:
            sb.append(
                f"{indent}{open_tag}"
                f"{self._escape_xml(str(value))}"
                f"{close_tag}\n"
            )

    @staticmethod
    def _escape_xml(value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )


# ------------------------------------------------------------------
# SkillRuntime
# ------------------------------------------------------------------
class SkillRuntime:
    """Aggregates SkillCatalog, SkillLoadTool, and SkillPromptBuilder.

    Usage::

        runtime = SkillRuntime()
        catalog = SkillCatalog.from_entries([
            SkillEntry(skill=my_skill, files_root="/skills/my-skill"),
        ])
        runtime.install(catalog, agent.toolkit)
        prompt_extra = runtime.render_prompt(catalog)
    """

    def __init__(
        self,
        prompt_builder: SkillPromptBuilder | None = None,
    ) -> None:
        self._catalog: SkillCatalog = SkillCatalog.empty()
        self._tool_installed: bool = False
        self._load_tool: Any = None
        self._prompt_builder = prompt_builder or SkillPromptBuilder()

    @property
    def current_catalog(self) -> SkillCatalog:
        return self._catalog

    @property
    def load_tool(self) -> Any:
        if self._load_tool is None:
            from ..tool._builtin._skill_load import SkillLoadTool
            self._load_tool = SkillLoadTool(lambda: self._catalog)
        return self._load_tool

    def install(self, catalog: SkillCatalog, toolkit: "Toolkit | None" = None) -> None:
        """Update catalog and add load_skill tool to toolkit's basic group (idempotent)."""
        self._catalog = catalog if catalog else SkillCatalog.empty()
        if toolkit is None or self._tool_installed:
            return
        try:
            basic_group = toolkit.tool_groups[0]
            names = [t.name for t in basic_group.tools]
            if self.load_tool.name in names:
                # Remove existing to avoid duplicates
                basic_group.tools = [
                    t for t in basic_group.tools if t.name != self.load_tool.name
                ]
            basic_group.tools.append(self.load_tool)
            self._tool_installed = True
        except Exception as e:
            logger.warning("Failed to register %s: %s", self.load_tool.name, e)

    def render_prompt(self, catalog: SkillCatalog | None = None) -> str:
        """Render the <available_skills> prompt block."""
        cat = catalog if catalog else self._catalog
        return self._prompt_builder.render(cat)
