# -*- coding: utf-8 -*-
"""Middleware that injects workspace context into the system prompt.

Ported from Java ``WorkspaceContextMiddleware``.

Reads AGENTS.md, MEMORY.md, and knowledge/ files from the workspace and
appends them to the system prompt so the agent sees persistent memory and
domain knowledge at the start of every call.
"""
from __future__ import annotations

import datetime
import platform
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, AsyncGenerator, Callable

from ._base import MiddlewareBase
from .._logging import logger
from ..workspace._path_policy import PathPolicy

if TYPE_CHECKING:
    from ..agent import Agent


class WorkspaceContextMiddleware(MiddlewareBase):
    """Inject workspace memory and knowledge into the system prompt.

    Usage::

        agent = Agent(
            ...,
            middlewares=[
                WorkspaceContextMiddleware(
                    workspace_dir="/tmp/workspace",
                    max_context_tokens=8000,
                ),
            ],
        )

    Args:
        workspace_dir (`str | Path`):
            Root directory for workspace files (memory, sessions, skills).
        project_dir (`str | Path | None`):
            Optional project directory (user's source tree). When given,
            the prompt distinguishes "project" from "workspace".
        max_context_tokens (`int`):
            Token budget for injected context (default 8000).
        additional_files (`list[str]`):
            Extra workspace-relative files to inject (e.g. [".cursorrules"]).
        environment_memory (`str | None`):
            Free-form text appended to the session context block.
    """

    TRUNCATION_NOTICE = "\n\n... (memory truncated — use memory_search for older entries) ...\n"
    DEFAULT_MAX_TOKENS = 8000

    def __init__(
        self,
        workspace_dir: str | Path,
        project_dir: str | Path | None = None,
        max_context_tokens: int = DEFAULT_MAX_TOKENS,
        additional_files: list[str] | None = None,
        environment_memory: str | None = None,
    ) -> None:
        self.workspace_dir = Path(workspace_dir).resolve()
        self.project_dir = Path(project_dir).resolve() if project_dir else None
        self.max_context_tokens = max_context_tokens
        self.additional_files = additional_files or []
        self.environment_memory = environment_memory

    async def on_system_prompt(
        self,
        agent: "Agent",
        current_prompt: str,
    ) -> str:
        """Append workspace context section to the system prompt."""
        section = self._build_workspace_section()
        if not section:
            return current_prompt
        separator = "\n" if current_prompt and not current_prompt.endswith("\n") else ""
        return current_prompt + separator + section

    def _build_workspace_section(self) -> str:
        agents_md = self._read_file("AGENTS.md")
        memory_md = self._read_file("MEMORY.md")
        knowledge_md = self._read_knowledge()
        session_ctx = self._build_session_context()

        additional_block = self._build_additional_context()

        # Token budgeting: truncate MEMORY.md if needed
        fixed_tokens = (
            self._estimate_tokens(session_ctx)
            + self._estimate_tokens(agents_md)
            + self._estimate_tokens(knowledge_md)
            + self._estimate_tokens(additional_block)
        )
        memory_tokens = self._estimate_tokens(memory_md)
        available = self.max_context_tokens - fixed_tokens
        if available > 0 and memory_tokens > available:
            memory_md = self._truncate(memory_md, available)

        workspace_para = self._build_workspace_paragraph()
        loaded_ctx = self._build_loaded_context(
            agents_md, memory_md, knowledge_md, additional_block
        )
        return self._assemble(session_ctx, workspace_para, loaded_ctx)

    def _build_session_context(self) -> str:
        today = datetime.date.today().strftime("%A %b %d, %Y")
        plat = f"{platform.system()} {platform.release()}"
        temp_dir = Path(tempfile.gettempdir())
        parts = [
            f"## AgentStateStore Context",
            f"This is the agent session. We are setting up the context for our chat.",
            f"Today's date is {today}.",
            f"My operating system is: {plat}",
            f"The workspace directory is: {self.workspace_dir}",
            f"The project's temporary directory is: {temp_dir}",
        ]
        if self.environment_memory:
            parts.append(self.environment_memory)
        return "\n".join(parts)

    def _build_workspace_paragraph(self) -> str:
        lines = ["## Workspace"]
        if self.project_dir:
            lines.append(
                f"Project (the user's source tree you're assisting with): {self.project_dir}"
            )
            lines.append(
                f"Workspace (your home base — memory, sessions, skills, runtime data): {self.workspace_dir}"
            )
            lines.append(
                "File tools write project files to the project directory. "
                "Workspace metadata paths are written to the workspace."
            )
            lines.append("Shell commands run with `pwd` set to the project directory.")
        else:
            lines.append(f"Your working directory is: {self.workspace_dir}")
            lines.append(
                "Treat this directory as the single global workspace for file operations."
            )
        lines.append(
            "AGENTS.md defines persona and local conventions — honor them when consistent "
            "with safety and policy."
        )
        return "\n".join(lines)

    def _build_loaded_context(
        self,
        agents_md: str,
        memory_md: str,
        knowledge_md: str,
        additional: str,
    ) -> str:
        sb = ["<loaded_context>"]
        sb.append(self._xml_block("agents_context", agents_md))
        sb.append(self._xml_block("memory_context", memory_md))
        sb.append(self._xml_block("domain_knowledge_context", knowledge_md))
        if additional.strip():
            sb.append(additional)
        sb.append("</loaded_context>")
        return "\n".join(sb)

    def _build_additional_context(self) -> str:
        sb: list[str] = []
        for rel_path in self.additional_files:
            content = self._read_file(rel_path)
            if content.strip():
                tag = rel_path.replace("/", "_").replace(".", "_").lower()
                sb.append(f"  <{tag}>")
                sb.append(self._indent(content.strip(), 2))
                sb.append(f"  </{tag}>")
        return "\n".join(sb)

    def _build_knowledge(self) -> str:
        knowledge_dir = self.workspace_dir / "knowledge"
        if not knowledge_dir.exists():
            return ""
        # Read KNOWLEDGE.md if present
        main_knowledge = self._read_file("knowledge/KNOWLEDGE.md")
        # List all files under knowledge/
        files: list[Path] = []
        if knowledge_dir.is_dir():
            files = sorted(
                [f for f in knowledge_dir.rglob("*") if f.is_file()],
                key=lambda p: str(p),
            )
        lines: list[str] = []
        if main_knowledge.strip():
            lines.append(main_knowledge.strip())
        if files:
            if lines:
                lines.append("")
            lines.append("Knowledge files:")
            for f in files:
                try:
                    rel = f.relative_to(self.workspace_dir)
                    lines.append(f"- {rel}")
                except ValueError:
                    lines.append(f"- {f}")
        return "\n".join(lines)

    def _read_file(self, rel_path: str) -> str:
        path = self.workspace_dir / rel_path
        try:
            if path.exists() and path.is_file():
                return path.read_text(encoding="utf-8")
        except Exception:
            pass
        return ""

    def _read_knowledge(self) -> str:
        return self._build_knowledge()

    @staticmethod
    def _assemble(
        session_context: str,
        workspace_paragraph: str,
        loaded_context: str,
    ) -> str:
        sb: list[str] = []
        if session_context.strip():
            sb.append(session_context.strip())
            sb.append("")
        sb.append(
            "## Domain Knowledge\n"
            "The workspace `knowledge/` tree holds many detailed reference documents. "
            "When the task needs specs, procedures, schemas, or domain facts, "
            "treat that directory as the source of truth.\n\n"
            "## Memory Recall\n"
            "Before answering questions about prior work, decisions, dates, people, or preferences: "
            "run memory_search on MEMORY.md + memory/*.md, then memory_get for needed lines.\n\n"
            "## Memory Persistence\n"
            "You have a persistent MEMORY.md. Update it proactively when user shares preferences, "
            "project context, or decisions. Use edit_file/write_file to append concise bullet points. "
            "Do NOT duplicate existing entries. Memory is also automatically extracted at conversation end."
        )
        if workspace_paragraph:
            sb.append("")
            sb.append(workspace_paragraph)
        sb.append("")
        sb.append("## Workspace Files (Injected)")
        sb.append(
            "The following <loaded_context> was loaded from files in your workspace. "
            "These files contain memory, facts, preferences, guidelines, and user-specific details."
        )
        sb.append("")
        sb.append(loaded_context)
        return "\n".join(sb)

    @staticmethod
    def _xml_block(tag: str, content: str) -> str:
        if not content.strip():
            return f"  <{tag}></{tag}>"
        return f"  <{tag}>\n{WorkspaceContextMiddleware._indent(content.strip(), 2)}\n  </{tag}>"

    @staticmethod
    def _indent(text: str, spaces: int) -> str:
        prefix = " " * spaces
        return "\n".join(prefix + line for line in text.splitlines())

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return len(text) // 4 if text else 0

    @staticmethod
    def _truncate(text: str, max_tokens: int) -> str:
        max_chars = max_tokens * 4
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + WorkspaceContextMiddleware.TRUNCATION_NOTICE
