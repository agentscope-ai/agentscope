# -*- coding: utf-8 -*-
"""LocalWorkspace — local-filesystem workspace (no container).

The agent operates directly on a host directory. MCP clients run
on the host as well. Skills are plain subdirectories.

Layout::

    {workdir}/
    ├── .mcp          # persisted MCP client configs (JSON array)
    ├── data/         # offloaded multimodal files
    ├── skills/       # skill subdirectories
    │   ├── .skills   # index file for skill metadata
    │   └── {name}/
    │       └── SKILL.md
    └── sessions/     # per-session context and tool-result files

``workdir`` and ``type`` are the only fields serialised to
``WorkspaceRecord.data``.  ``default_mcps`` and ``skill_paths`` are
service-level defaults that are excluded from serialisation.
"""

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, TypedDict

import aiofiles
import aiofiles.ospath
import frontmatter
from pydantic import AnyUrl

from .._logging import logger
from ..mcp import MCPClient
from ..message import (
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    ToolResultBlock,
    URLSource,
)
from ..skill import Skill
from ..tool import (
    Bash,
    Edit,
    Glob,
    Grep,
    Read,
    ToolBase,
    Write,
)
from .config import MCPServerConfig
from .types import SerializedWorkspaceState
from .workspace_base import WorkspaceBase


# --- TypedDicts for .skills index ---


class _SkillEntry(TypedDict):
    """A single entry in the .skills index file."""

    hash: str
    skill_name: str


class _SkillsFile(TypedDict):
    """Schema of the .skills index file stored inside skills_dir."""

    skills_dir_mtime: float
    skills: dict[str, _SkillEntry]


# --- helpers ---


def _sanitize_dir_name(name: str) -> str:
    return re.sub(r"[^\w一-鿿-]", "_", name)


_DEFAULT_INSTRUCTIONS = (  # noqa: E501
    "<workspace>\n"
    "You have access to a local workspace at {workdir} "
    "with the following structure:\n"
    "\n"
    "```\n"
    "{workdir}\n"
    "├── data/        # offloaded multimodal files (images, etc.)\n"
    "├── skills/      # reusable skills, each in its own subdirectory\n"
    "└── sessions/    # session context and tool results\n"
    "```\n"
    "\n"
    "This workspace is your personal working environment for "
    "completing various tasks.\n"
    "You are responsible for keeping it clean, structured, and "
    "easy to navigate over time.\n"
    "\n"
    "### Project Directory\n"
    "- Create a dedicated subdirectory for each task or project "
    "under the workspace root.\n"
    "- Name the directory concisely and descriptively, e.g. "
    "`20240315_web-scraper`, so it remains identifiable long "
    "after creation.\n"
    "- Always create a `README.md` at the project root documenting:\n"
    "  - What the project is about\n"
    "  - When it was created\n"
    "  - Key decisions or context\n"
    "  - The changes you have made (and when)\n"
    "\n"
    "### Python Environment\n"
    "- Use `uv` to create an isolated virtual environment:\n"
    "  ```shell\n"
    "  uv venv && uv pip install ...\n"
    "  ```\n"
    "</workspace>"
)


# --- LocalWorkspace ---


class LocalWorkspace(WorkspaceBase):
    """Workspace backed by a local directory on the host filesystem.

    Layout::

        {workdir}/
        ├── .mcp          # persisted MCP client configs (JSON)
        ├── data/         # offloaded binary data
        ├── skills/       # skill directories
        │   ├── .skills   # index (hash + agent-facing name)
        │   └── {skill_name}/
        │       └── SKILL.md
        └── sessions/
            └── {session_id}/
                ├── context.jsonl
                └── tool_result-{id}.txt
    """

    def __init__(
        self,
        workdir: str,
        skill_paths: list[str] | None = None,
        default_mcps: list[MCPClient] | None = None,
        instructions: str = _DEFAULT_INSTRUCTIONS,
    ) -> None:
        self._id = uuid.uuid4().hex[:12]
        self._workdir = os.path.abspath(workdir)
        self._skill_paths = list(
            dict.fromkeys(os.path.abspath(p) for p in (skill_paths or [])),
        )
        self._instructions = instructions
        self._default_mcps: list[MCPClient] = list(default_mcps or [])
        self._mcps: list[MCPClient] = []
        self._skills_lock = asyncio.Lock()
        self._offload_lock = asyncio.Lock()

    @property
    def workspace_id(self) -> str:
        return self._id

    @property
    def workdir(self) -> str:
        """Absolute path to the workspace root directory."""
        return self._workdir

    # --- lifecycle ---

    async def initialize(self) -> None:
        """Initialise the workspace.

        MCP state is restored from ``.mcp`` if it exists; otherwise
        ``default_mcps`` are used.  ``skill_paths`` are seeded on first
        use.
        """
        mcp_file = os.path.join(self._workdir, ".mcp")
        if await aiofiles.ospath.exists(mcp_file):
            async with aiofiles.open(
                mcp_file,
                "r",
                encoding="utf-8",
            ) as f:
                self._mcps = [
                    MCPClient.model_validate(m)
                    for m in json.loads(await f.read())
                ]
        else:
            self._mcps = list(self._default_mcps)

        for mcp in self._mcps:
            if mcp.is_stateful and not mcp.is_connected:
                await mcp.connect()

        await self._seed_initial_skills()

    async def reset(self) -> None:
        """Clear session data and offloaded files."""
        sessions_dir = os.path.join(self._workdir, "sessions")
        if os.path.isdir(sessions_dir):
            await asyncio.to_thread(shutil.rmtree, sessions_dir)

        data_dir = os.path.join(self._workdir, "data")
        if os.path.isdir(data_dir):
            await asyncio.to_thread(shutil.rmtree, data_dir)

    async def is_alive(self) -> bool:
        return True

    async def close(self) -> None:
        for mcp in self._mcps:
            if (
                mcp.is_stateful or mcp.mcp_config.type == "stdio_mcp"
            ) and mcp.is_connected:
                await mcp.close()

    # --- instructions ---

    async def get_instructions(self) -> str:
        return self._instructions.format(workdir=self._workdir)

    # --- tool discovery ---

    async def list_tools(self) -> list[ToolBase]:
        return [Bash(), Edit(), Glob(), Grep(), Read(), Write()]

    async def list_mcps(self) -> list[MCPClient]:
        return list(self._mcps)

    # --- MCP persistence ---

    async def _save_mcp_file(self) -> None:
        """Persist the current MCP client list to ``.mcp``."""
        mcp_file = os.path.join(self._workdir, ".mcp")
        try:
            async with aiofiles.open(
                mcp_file,
                "w",
                encoding="utf-8",
            ) as f:
                await f.write(
                    json.dumps(
                        [m.model_dump() for m in self._mcps],
                        indent=2,
                        ensure_ascii=False,
                    ),
                )
        except Exception as e:
            logger.warning(
                "Failed to save .mcp to %s: %s",
                mcp_file,
                e,
            )

    async def add_mcp(self, config: MCPServerConfig) -> None:
        """Add an MCP server from config, connect, and persist."""
        from ..mcp import HttpMCPConfig, StdioMCPConfig

        for existing in self._mcps:
            if existing.name == config.name:
                raise ValueError(
                    f"MCP {config.name!r} already exists. "
                    "Remove it first or use a different name.",
                )

        if config.protocol == "http":
            mcp_cfg = HttpMCPConfig(
                url=config.url,
                headers=config.headers or None,
                timeout=config.timeout,
            )
        else:
            mcp_cfg = StdioMCPConfig(
                command=config.command,
                args=config.args or None,
                env=config.env or None,
            )

        client = MCPClient(
            name=config.name,
            is_stateful=True,
            mcp_config=mcp_cfg,
        )
        await client.connect()
        self._mcps.append(client)
        await self._save_mcp_file()
        logger.info("LocalWorkspace: added MCP %r", config.name)

    async def remove_mcp(self, name: str) -> None:
        """Remove an MCP client by name, disconnect, and persist."""
        for i, mcp in enumerate(self._mcps):
            if mcp.name == name:
                if mcp.is_connected:
                    await mcp.close()
                self._mcps.pop(i)
                await self._save_mcp_file()
                logger.info(
                    "LocalWorkspace: removed MCP %r",
                    name,
                )
                return
        raise KeyError(
            f"MCP {name!r} not found. "
            f"Available: {[m.name for m in self._mcps]}",
        )

    # --- skill discovery (index-based) ---

    async def list_skills(self) -> list[Skill]:
        """List skills using the .skills index.

        Compares skills_dir mtime to detect manual changes; reconciles
        the index when stale.
        """
        skills_dir = os.path.join(self._workdir, "skills")

        if not await aiofiles.ospath.isdir(skills_dir):
            return []

        skills_file = await self._load_skills_file(skills_dir)
        current_mtime = await aiofiles.ospath.getmtime(skills_dir)

        if current_mtime != skills_file["skills_dir_mtime"]:
            skills_file = await self._reconcile_skills_dir(
                skills_dir,
                skills_file,
                current_mtime,
            )

        tasks = [
            self._load_single_skill(
                os.path.join(skills_dir, dir_name),
                entry["skill_name"],
            )
            for dir_name, entry in skills_file["skills"].items()
        ]
        results = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        skills: list[Skill] = []
        for dir_name, result in zip(
            skills_file["skills"],
            results,
        ):
            if isinstance(result, BaseException):
                logger.warning(
                    "Failed to load skill from %s: %s",
                    dir_name,
                    result,
                )
            elif result is not None:
                skills.append(result)
        return skills

    # --- offload ---

    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
        **kwargs: Any,
    ) -> str:
        path = os.path.join(
            self._workdir,
            "sessions",
            session_id,
            "context.jsonl",
        )

        copied_msgs = deepcopy(msgs)
        lines: list[str] = []
        for msg in copied_msgs:
            if not isinstance(msg.content, str):
                content = []
                for block in msg.content:
                    if isinstance(block, DataBlock) and isinstance(
                        block.source,
                        Base64Source,
                    ):
                        content.append(
                            await self._offload_data_block(block),
                        )
                    else:
                        content.append(block)
                msg.content = content
            lines.append(msg.model_dump_json())

        async with self._offload_lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            async with aiofiles.open(
                path,
                mode="a",
                encoding="utf-8",
            ) as f:
                await f.write("\n".join(lines) + "\n")
        return path

    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
        **kwargs: Any,
    ) -> str:
        path = os.path.join(
            self._workdir,
            "sessions",
            session_id,
            f"tool_result-{tool_result.id}.txt",
        )

        parts: list[str] = []
        if isinstance(tool_result.output, str):
            parts.append(tool_result.output)
        else:
            for block in tool_result.output:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
                elif isinstance(block, DataBlock):
                    if isinstance(block.source, Base64Source):
                        d = await self._offload_data_block(block)
                        url = d.source.url
                    else:
                        url = block.source.url
                    parts.append(
                        f"<data url='{url}' name='{block.name}' "
                        f"media_type='{block.source.media_type}'/>",
                    )

        async with self._offload_lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            async with aiofiles.open(
                path,
                mode="w",
                encoding="utf-8",
            ) as f:
                await f.write("".join(parts))
        return path

    # --- export state ---

    async def export_state(self) -> SerializedWorkspaceState:
        """Export the local workspace state for later restoration."""
        return SerializedWorkspaceState(
            backend_type="local",
            payload={
                "workspace_id": self._id,
                "workdir": self._workdir,
            },
        )

    # --- dynamic skill management (index-based) ---

    async def add_skill(self, skill_path: str) -> None:
        """Add a skill, updating the .skills index."""
        skills_dir = os.path.join(self._workdir, "skills")
        os.makedirs(skills_dir, exist_ok=True)

        result = await self._validate_and_hash_skill(skill_path)
        if result is None:
            raise ValueError(
                f"Invalid skill at {skill_path!r}: missing or "
                "malformed SKILL.md (requires 'name' and "
                "'description' fields).",
            )

        _, raw_name, skill_hash = result

        skills_file = await self._load_skills_file(skills_dir)
        existing: dict[str, _SkillEntry] = skills_file["skills"]

        existing_hashes: set[str] = {e["hash"] for e in existing.values()}
        if skill_hash in existing_hashes:
            logger.info(
                "Skill '%s' (hash: %s...) already exists, skipping",
                raw_name,
                skill_hash[:8],
            )
            return

        existing_agent_names: set[str] = {
            e["skill_name"] for e in existing.values()
        }
        existing_dir_names: set[str] = set(existing.keys())

        agent_name = raw_name
        counter = 1
        while agent_name in existing_agent_names:
            agent_name = f"{raw_name} ({counter})"
            counter += 1

        base_dir = _sanitize_dir_name(raw_name)
        dir_name = base_dir
        counter = 1
        while dir_name in existing_dir_names:
            dir_name = f"{base_dir}_{counter}"
            counter += 1

        dest_path = os.path.join(skills_dir, dir_name)

        if not os.path.realpath(dest_path).startswith(
            os.path.realpath(skills_dir) + os.sep,
        ):
            raise ValueError(
                f"Skill path {skill_path!r} resolves outside " "skills_dir.",
            )

        await asyncio.to_thread(
            shutil.copytree,
            skill_path,
            dest_path,
            dirs_exist_ok=False,
        )

        logger.info(
            "Copied skill '%s' (agent name: '%s') from %s to %s",
            raw_name,
            agent_name,
            skill_path,
            dest_path,
        )

        existing[dir_name] = {
            "hash": skill_hash,
            "skill_name": agent_name,
        }
        skills_file["skills"] = existing
        skills_file["skills_dir_mtime"] = await aiofiles.ospath.getmtime(
            skills_dir,
        )
        await self._save_skills_file(skills_dir, skills_file)

    async def remove_skill(self, name: str) -> None:
        """Remove a skill by agent-facing name, updating the index."""
        skills_dir = os.path.join(self._workdir, "skills")

        if not await aiofiles.ospath.isdir(skills_dir):
            logger.warning(
                "Skills directory does not exist; cannot remove skill %r",
                name,
            )
            return

        skills_file = await self._load_skills_file(skills_dir)
        existing: dict[str, _SkillEntry] = skills_file["skills"]

        target_dir: str | None = None
        for dir_name, entry in existing.items():
            if entry["skill_name"] == name:
                target_dir = dir_name
                break

        if target_dir is None:
            logger.warning(
                "Skill %r not found in workspace",
                name,
            )
            return

        skill_dir_path = os.path.join(skills_dir, target_dir)
        if await aiofiles.ospath.isdir(skill_dir_path):
            await asyncio.to_thread(
                shutil.rmtree,
                skill_dir_path,
            )
            logger.info(
                "Removed skill '%s' from %s",
                name,
                skill_dir_path,
            )
        else:
            logger.warning(
                "Skill directory %r not found on disk; "
                "removing index entry",
                skill_dir_path,
            )

        del existing[target_dir]
        skills_file["skills"] = existing
        skills_file["skills_dir_mtime"] = await aiofiles.ospath.getmtime(
            skills_dir,
        )
        await self._save_skills_file(skills_dir, skills_file)

    # --- internal: .skills index I/O ---

    async def _load_skills_file(
        self,
        skills_dir: str,
    ) -> _SkillsFile:
        """Load the .skills index, returning empty if absent."""
        path = os.path.join(skills_dir, ".skills")
        if not await aiofiles.ospath.exists(path):
            return {"skills_dir_mtime": 0.0, "skills": {}}

        try:
            async with aiofiles.open(
                path,
                "r",
                encoding="utf-8",
            ) as f:
                data = json.loads(await f.read())
            return _SkillsFile(
                skills_dir_mtime=float(
                    data.get("skills_dir_mtime", 0.0),
                ),
                skills=data.get("skills", {}),
            )
        except Exception as e:
            logger.warning(
                "Failed to load .skills from %s: %s",
                path,
                e,
            )
            return {"skills_dir_mtime": 0.0, "skills": {}}

    async def _save_skills_file(
        self,
        skills_dir: str,
        data: _SkillsFile,
    ) -> None:
        """Persist the .skills index file."""
        path = os.path.join(skills_dir, ".skills")
        try:
            async with aiofiles.open(
                path,
                "w",
                encoding="utf-8",
            ) as f:
                await f.write(
                    json.dumps(
                        data,
                        indent=2,
                        ensure_ascii=False,
                    ),
                )
        except Exception as e:
            logger.warning(
                "Failed to save .skills to %s: %s",
                path,
                e,
            )

    # --- internal: reconciliation ---

    async def _reconcile_skills_dir(
        self,
        skills_dir: str,
        skills_file: _SkillsFile,
        current_mtime: float,
    ) -> _SkillsFile:
        """Reconcile the .skills index after directory changes.

        Handles manually deleted and manually added subdirectories.
        """
        existing: dict[str, _SkillEntry] = skills_file["skills"]
        original_mtime = skills_file["skills_dir_mtime"]

        def _list_dirs() -> set[str]:
            return {
                d
                for d in os.listdir(skills_dir)
                if os.path.isdir(os.path.join(skills_dir, d))
                and not d.startswith(".")
            }

        actual_dirs = await asyncio.to_thread(_list_dirs)
        indexed_dirs = set(existing.keys())
        updated = False

        for removed in indexed_dirs - actual_dirs:
            logger.info(
                "Skill directory '%s' removed, updating index",
                removed,
            )
            del existing[removed]
            updated = True

        existing_agent_names: set[str] = {
            e["skill_name"] for e in existing.values()
        }
        existing_hashes: set[str] = {e["hash"] for e in existing.values()}

        for new_dir in actual_dirs - indexed_dirs:
            skill_path = os.path.join(skills_dir, new_dir)
            result = await self._validate_and_hash_skill(
                skill_path,
            )
            if result is None:
                continue

            _, raw_name, skill_hash = result

            if skill_hash in existing_hashes:
                logger.info(
                    "Manually added skill '%s' already tracked "
                    "by hash, skipping",
                    new_dir,
                )
                continue

            agent_name = raw_name
            counter = 1
            while agent_name in existing_agent_names:
                agent_name = f"{raw_name} ({counter})"
                counter += 1

            entry: _SkillEntry = {
                "hash": skill_hash,
                "skill_name": agent_name,
            }
            existing[new_dir] = entry
            existing_agent_names.add(agent_name)
            existing_hashes.add(skill_hash)
            updated = True
            logger.info(
                "Manually added skill '%s' indexed as agent name '%s'",
                new_dir,
                agent_name,
            )

        skills_file["skills"] = existing
        skills_file["skills_dir_mtime"] = current_mtime

        if updated or current_mtime != original_mtime:
            await self._save_skills_file(skills_dir, skills_file)

        return skills_file

    # --- internal: initial skill seeding ---

    async def _seed_initial_skills(self) -> None:
        """Seed skills from ``skill_paths`` on first use."""
        skills_dir = os.path.join(self._workdir, "skills")
        os.makedirs(skills_dir, exist_ok=True)

        skills_file = await self._load_skills_file(skills_dir)
        existing: dict[str, _SkillEntry] = skills_file["skills"]

        existing_hashes: set[str] = {e["hash"] for e in existing.values()}
        existing_agent_names: set[str] = {
            e["skill_name"] for e in existing.values()
        }
        existing_dir_names: set[str] = set(existing.keys())

        updated = False
        for skill_path in self._skill_paths:
            result = await self._validate_and_hash_skill(skill_path)
            if result is None:
                continue

            _, raw_name, skill_hash = result

            if skill_hash in existing_hashes:
                logger.info(
                    "Skill '%s' (hash: %s...) already exists, skipping",
                    raw_name,
                    skill_hash[:8],
                )
                continue

            agent_name = raw_name
            counter = 1
            while agent_name in existing_agent_names:
                agent_name = f"{raw_name} ({counter})"
                counter += 1

            base_dir = _sanitize_dir_name(raw_name)
            dir_name = base_dir
            counter = 1
            while dir_name in existing_dir_names:
                dir_name = f"{base_dir}_{counter}"
                counter += 1

            dest_path = os.path.join(skills_dir, dir_name)

            if not os.path.realpath(dest_path).startswith(
                os.path.realpath(skills_dir) + os.sep,
            ):
                logger.warning(
                    "Skill '%s' resolves outside skills_dir, skipping",
                    raw_name,
                )
                continue

            try:
                await asyncio.to_thread(
                    shutil.copytree,
                    skill_path,
                    dest_path,
                    dirs_exist_ok=False,
                )
            except Exception as e:
                logger.warning(
                    "Failed to copy skill '%s' from %s: %s",
                    raw_name,
                    skill_path,
                    e,
                )
                continue

            logger.info(
                "Copied skill '%s' (agent name: '%s') from %s to %s",
                raw_name,
                agent_name,
                skill_path,
                dest_path,
            )

            entry: _SkillEntry = {
                "hash": skill_hash,
                "skill_name": agent_name,
            }
            existing[dir_name] = entry
            existing_hashes.add(skill_hash)
            existing_agent_names.add(agent_name)
            existing_dir_names.add(dir_name)
            updated = True

        if updated:
            skills_file["skills"] = existing
            skills_file["skills_dir_mtime"] = await aiofiles.ospath.getmtime(
                skills_dir,
            )
            await self._save_skills_file(skills_dir, skills_file)

    # --- internal: skill validation ---

    async def _validate_skill(
        self,
        skill_path: str,
    ) -> tuple[str, str, str] | None:
        """Validate a skill directory.

        Returns (name, description, skill_md_content) or None.
        """
        skill_md_path = os.path.join(skill_path, "SKILL.md")

        try:
            if not await aiofiles.ospath.isfile(skill_md_path):
                logger.warning(
                    "Invalid skill at %s: SKILL.md not found",
                    skill_path,
                )
                return None

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
                    "Invalid skill at %s: SKILL.md missing "
                    "required fields (name or description)",
                    skill_path,
                )
                return None

            return str(name), str(description), content_str

        except Exception as e:
            logger.warning(
                "Failed to validate skill at %s: %s",
                skill_path,
                e,
            )
            return None

    async def _validate_and_hash_skill(
        self,
        skill_path: str,
    ) -> tuple[str, str, str] | None:
        """Validate a skill and compute its hash.

        Returns (skill_path, skill_name, skill_hash) or None.
        """
        validation_result = await self._validate_skill(skill_path)
        if validation_result is None:
            return None

        skill_name, _, skill_md_content = validation_result

        skill_hash = hashlib.sha256(
            skill_md_content.encode("utf-8"),
        ).hexdigest()

        return skill_path, skill_name, skill_hash

    async def _load_single_skill(
        self,
        skill_dir: str,
        skill_name: str,
    ) -> Skill | None:
        """Load a single Skill using the agent-facing name from
        the .skills index."""
        skill_md_path = os.path.join(skill_dir, "SKILL.md")

        try:
            if not await aiofiles.ospath.isfile(skill_md_path):
                return None

            updated_at = await aiofiles.ospath.getmtime(
                skill_md_path,
            )

            async with aiofiles.open(
                skill_md_path,
                "r",
                encoding="utf-8",
            ) as f:
                content_str = await f.read()
                content = frontmatter.loads(content_str)

            description = content.get("description")
            if not description:
                logger.warning(
                    "SKILL.md in %s is missing 'description'. Skipping.",
                    skill_dir,
                )
                return None

            return Skill(
                name=skill_name,
                description=str(description),
                dir=skill_dir,
                markdown=content.content,
                updated_at=updated_at,
            )

        except Exception as e:
            logger.warning(
                "Failed to load skill from %s: %s",
                skill_dir,
                e,
            )
            return None

    # --- internal: data offload ---

    async def _offload_data_block(
        self,
        data_block: DataBlock,
    ) -> DataBlock:
        if isinstance(data_block.source, URLSource):
            return data_block
        h = hashlib.sha256(
            data_block.source.data.encode(),
        ).hexdigest()
        ext = (
            mimetypes.guess_extension(
                data_block.source.media_type,
            )
            or ".bin"
        )
        path = os.path.join(self._workdir, "data", f"{h}{ext}")
        if not await aiofiles.ospath.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            async with aiofiles.open(path, "wb") as f:
                await f.write(
                    base64.b64decode(data_block.source.data),
                )
        return DataBlock(
            id=data_block.id,
            name=data_block.name,
            source=URLSource(
                url=AnyUrl(Path(path).as_uri()),
                media_type=data_block.source.media_type,
            ),
        )
