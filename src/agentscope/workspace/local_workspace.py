# -*- coding: utf-8 -*-
"""LocalWorkspace — local-filesystem workspace (no container).

The agent operates directly on a host directory. MCP clients run
on the host as well. Skills are plain subdirectories.

Layout::

    {workdir}/
    ├── .mcp          # persisted MCP client configs (JSON array)
    ├── data/         # offloaded multimodal files
    ├── skills/       # skill subdirectories
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
from copy import deepcopy
from pathlib import Path
from typing import Any

import aiofiles
import aiofiles.ospath
import frontmatter
from pydantic import AnyUrl, Field, PrivateAttr

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
from .types import ExecutionResult, SerializedWorkspaceState
from .workspace_base import WorkspaceBase

# --- helpers ---


def _sanitize_dir_name(name: str) -> str:
    """Replace non-word characters (except CJK) with underscores."""
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

    The agent operates directly on a host directory.  MCP clients
    run on the host as well.  Skills are plain subdirectories.

    Args:
        workdir: Absolute path to the workspace root directory.
            Will be normalized to an absolute path during
            construction.
        instructions: Workspace-specific system prompt fragment
            returned by :meth:`get_instructions`.  May contain
            a ``{workdir}`` placeholder that is filled at runtime.
        skill_paths: List of local directory paths containing
            skills to seed on first initialization.  These are
            copied into ``{workdir}/skills/`` if not already
            present.  Excluded from serialisation.
        default_mcps: List of :class:`MCPClient` instances to use
            when no persisted ``.mcp`` file exists yet.  Excluded
            from serialisation.

    Layout::

        {workdir}/
        ├── .mcp          # persisted MCP client configs (JSON)
        ├── data/         # offloaded binary data
        ├── skills/       # skill directories
        │   └── {skill_name}/
        │       └── SKILL.md
        └── sessions/
            └── {session_id}/
                ├── context.jsonl
                └── tool_result-{id}.txt
    """

    # ── serializable configuration fields ─────────────────────────

    workdir: str = Field(
        description="Absolute path to the workspace root directory.",
    )
    instructions: str = _DEFAULT_INSTRUCTIONS

    # ── init-only fields (excluded from serialisation) ────────────

    skill_paths: list[str] = Field(default_factory=list, exclude=True)
    default_mcps: list[MCPClient] = Field(
        default_factory=list,
        exclude=True,
    )

    # ── runtime state (excluded from serialisation) ───────────────

    _mcps: list[MCPClient] = PrivateAttr(default_factory=list)
    _offload_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)
    _skill_lock: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)

    def model_post_init(self, __context: Any) -> None:
        """Normalize paths after pydantic construction."""
        # Ensure workdir is absolute
        self.workdir = os.path.abspath(self.workdir)
        # Deduplicate and normalize skill paths
        self.skill_paths = list(
            dict.fromkeys(os.path.abspath(p) for p in self.skill_paths),
        )

    # --- lifecycle ---

    async def initialize(self) -> None:
        """Initialise the workspace.

        MCP state is restored from ``.mcp`` if it exists; otherwise
        ``default_mcps`` are used.  ``skill_paths`` are seeded on first
        use.
        """
        if self._started:
            return
        self._started = True
        mcp_file = os.path.join(self.workdir, ".mcp")
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
            self._mcps = list(self.default_mcps)

        for mcp in self._mcps:
            if mcp.is_stateful and not mcp.is_connected:
                await mcp.connect()

        await self._seed_initial_skills()

    async def reset(self) -> None:
        """Clear session data and offloaded files."""
        sessions_dir = os.path.join(self.workdir, "sessions")
        if os.path.isdir(sessions_dir):
            await asyncio.to_thread(shutil.rmtree, sessions_dir)

        data_dir = os.path.join(self.workdir, "data")
        if os.path.isdir(data_dir):
            await asyncio.to_thread(shutil.rmtree, data_dir)

    async def is_alive(self) -> bool:
        """Always ``True`` — a local directory is always available."""
        return True

    async def close(self) -> None:
        """Close all stateful / stdio MCP client connections."""
        for mcp in self._mcps:
            if (
                mcp.is_stateful or mcp.mcp_config.type == "stdio_mcp"
            ) and mcp.is_connected:
                await mcp.close()
        self._started = False

    async def _exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
    ) -> ExecutionResult:
        """Run a shell command on the host as a subprocess.

        Args:
            command: Shell command string to execute.
            timeout: Maximum seconds to wait (covers both process
                creation and execution). ``None`` means no limit.
        """
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.workdir,
                ),
                timeout=timeout,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            if proc is not None:
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
            return ExecutionResult(
                exit_code=-1,
                stdout=b"",
                stderr=b"timed out",
            )
        return ExecutionResult(
            exit_code=proc.returncode or 0,
            stdout=stdout or b"",
            stderr=stderr or b"",
        )

    # --- instructions ---

    async def get_instructions(self) -> str:
        """Return the workspace-specific system prompt fragment."""
        return self.instructions.format(workdir=self.workdir)

    # --- tool discovery ---

    async def list_tools(self) -> list[ToolBase]:
        """Return built-in host-side tools (Bash, Edit, etc.)."""
        return [Bash(), Edit(), Glob(), Grep(), Read(), Write()]

    async def list_mcps(self) -> list[MCPClient]:
        """Return the current list of MCP clients."""
        return list(self._mcps)

    # --- MCP persistence ---

    async def _save_mcp_file(self) -> None:
        """Persist the current MCP client list to ``.mcp``."""
        mcp_file = os.path.join(self.workdir, ".mcp")
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

    async def add_mcp(self, mcp_client: MCPClient) -> None:
        """Add an MCP client, connect if stateful, and persist.

        Args:
            mcp_client: An :class:`MCPClient` instance to add.

        Raises:
            ValueError: If an MCP with the same name already exists.
        """
        for existing in self._mcps:
            if existing.name == mcp_client.name:
                raise ValueError(
                    f"MCP {mcp_client.name!r} already exists. "
                    "Remove it first or use a different name.",
                )

        if mcp_client.is_stateful and not mcp_client.is_connected:
            await mcp_client.connect()
        self._mcps.append(mcp_client)
        await self._save_mcp_file()
        logger.info("LocalWorkspace: added MCP %r", mcp_client.name)

    async def remove_mcp(self, name: str) -> None:
        """Remove an MCP client by name, disconnect, and persist.

        Args:
            name: Name of the MCP server to remove.

        Raises:
            KeyError: If no MCP with the given name exists.
        """
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
        avail = [m.name for m in self._mcps]
        raise KeyError(
            f"MCP {name!r} not found. Available: {avail}",
        )

    # --- skill discovery ---

    async def list_skills(self) -> list[Skill]:
        """List all valid skills by scanning the skills directory."""
        skills_dir = os.path.join(self.workdir, "skills")

        if not await aiofiles.ospath.isdir(skills_dir):
            return []

        def _list_dirs() -> list[str]:
            return [
                d
                for d in os.listdir(skills_dir)
                if os.path.isdir(os.path.join(skills_dir, d))
                and not d.startswith(".")
            ]

        dir_names = await asyncio.to_thread(_list_dirs)

        tasks = [
            self._load_single_skill(os.path.join(skills_dir, d))
            for d in dir_names
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        skills: list[Skill] = []
        for dir_name, result in zip(dir_names, results):
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
        """Offload conversation context to a JSONL file on disk."""
        path = os.path.join(
            self.workdir,
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
        """Persist a tool result to a text file on disk.

        Returns:
            The absolute file path of the written result.
        """
        path = os.path.join(
            self.workdir,
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
                "workspace_id": self.workspace_id,
                "workdir": self.workdir,
            },
        )

    # --- dynamic skill management ---

    async def add_skill(self, skill_path: str) -> None:
        """Add a skill by copying its directory into the workspace.

        This method is safe to call concurrently on the same workspace:
        skill validation, duplicate detection, target-name allocation,
        and copying are serialized by ``_skill_lock``.

        Args:
            skill_path: Absolute or relative path to the skill directory.

        Raises:
            ValueError: If the skill directory is invalid.
        """
        skill_path = os.path.abspath(skill_path)
        async with self._skill_lock:
            skills_dir = os.path.join(self.workdir, "skills")
            os.makedirs(skills_dir, exist_ok=True)

            result = await self._validate_and_hash_skill(skill_path)
            if result is None:
                raise ValueError(
                    f"Invalid skill at {skill_path!r}: missing or "
                    "malformed SKILL.md (requires 'name' and "
                    "'description' fields).",
                )

            _, raw_name, skill_hash = result

            existing_hashes = await self._collect_existing_hashes(skills_dir)
            if skill_hash in existing_hashes:
                logger.info(
                    "Skill '%s' (hash: %s...) already exists, skipping",
                    raw_name,
                    skill_hash[:8],
                )
                return

            existing_dir_names = await self._list_skill_dirs(skills_dir)

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
                    f"Skill path {skill_path!r} resolves outside skills_dir.",
                )

            await asyncio.to_thread(
                shutil.copytree,
                skill_path,
                dest_path,
                dirs_exist_ok=False,
            )

            logger.info(
                "Added skill '%s' from %s to %s",
                raw_name,
                skill_path,
                dest_path,
            )

    async def remove_skill(self, name: str) -> None:
        """Remove a skill by its name (from SKILL.md front matter).

        Args:
            name: The ``name`` field from the skill's ``SKILL.md``
                front matter.

        Raises:
            KeyError: If the skill is not found in the workspace.
        """
        async with self._skill_lock:
            skills_dir = os.path.join(self.workdir, "skills")

            if not await aiofiles.ospath.isdir(skills_dir):
                available: list[str] = []
                raise KeyError(
                    f"Skill {name!r} not found. Available: {available}",
                )

            skills = await self.list_skills()
            target_dir: str | None = None
            for skill in skills:
                if skill.name == name:
                    target_dir = skill.dir
                    break

            if target_dir is None:
                available = [s.name for s in skills]
                raise KeyError(
                    f"Skill {name!r} not found. Available: {available}",
                )

            if await aiofiles.ospath.isdir(target_dir):
                await asyncio.to_thread(shutil.rmtree, target_dir)
                logger.info("Removed skill '%s' from %s", name, target_dir)

    # --- internal: initial skill seeding ---

    async def _seed_initial_skills(self) -> None:
        """Seed skills from ``skill_paths`` on first use."""
        if not self.skill_paths:
            return

        async with self._skill_lock:
            skills_dir = os.path.join(self.workdir, "skills")
            os.makedirs(skills_dir, exist_ok=True)

            existing_hashes = await self._collect_existing_hashes(skills_dir)
            existing_dir_names = await self._list_skill_dirs(skills_dir)

            for skill_path in self.skill_paths:
                result = await self._validate_and_hash_skill(skill_path)
                if result is None:
                    continue

                _, raw_name, skill_hash = result

                if skill_hash in existing_hashes:
                    continue

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

                existing_hashes.add(skill_hash)
                existing_dir_names.add(dir_name)
                logger.info(
                    "Seeded skill '%s' from %s",
                    raw_name,
                    skill_path,
                )

    # --- internal: skill helpers ---

    async def _list_skill_dirs(self, skills_dir: str) -> set[str]:
        """Return the set of subdirectory names inside *skills_dir*.

        Args:
            skills_dir: Absolute path to the skills directory.
        """

        def _scan() -> set[str]:
            return {
                d
                for d in os.listdir(skills_dir)
                if os.path.isdir(os.path.join(skills_dir, d))
                and not d.startswith(".")
            }

        return await asyncio.to_thread(_scan)

    async def _collect_existing_hashes(self, skills_dir: str) -> set[str]:
        """Compute content hashes of all existing skills.

        Args:
            skills_dir: Absolute path to the skills directory.
        """
        dir_names = await self._list_skill_dirs(skills_dir)
        hashes: set[str] = set()
        for d in dir_names:
            skill_md = os.path.join(skills_dir, d, "SKILL.md")
            if await aiofiles.ospath.isfile(skill_md):
                async with aiofiles.open(
                    skill_md,
                    "r",
                    encoding="utf-8",
                ) as f:
                    content = await f.read()
                hashes.add(
                    hashlib.sha256(content.encode("utf-8")).hexdigest(),
                )
        return hashes

    async def _validate_and_hash_skill(
        self,
        skill_path: str,
    ) -> tuple[str, str, str] | None:
        """Validate a skill directory and compute its content hash.

        Args:
            skill_path: Absolute path to the skill directory.

        Returns:
            ``(skill_path, skill_name, skill_hash)`` on success,
            or ``None`` if validation fails.
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

            skill_hash = hashlib.sha256(
                content_str.encode("utf-8"),
            ).hexdigest()
            return skill_path, str(name), skill_hash

        except Exception as e:
            logger.warning(
                "Failed to validate skill at %s: %s",
                skill_path,
                e,
            )
            return None

    async def _load_single_skill(
        self,
        skill_dir: str,
    ) -> Skill | None:
        """Load a single Skill from its directory.

        Args:
            skill_dir: Absolute path to a skill subdirectory
                containing ``SKILL.md``.
        """
        skill_md_path = os.path.join(skill_dir, "SKILL.md")

        try:
            if not await aiofiles.ospath.isfile(skill_md_path):
                return None

            updated_at = await aiofiles.ospath.getmtime(skill_md_path)

            async with aiofiles.open(
                skill_md_path,
                "r",
                encoding="utf-8",
            ) as f:
                content = frontmatter.loads(await f.read())

            name = content.get("name")
            description = content.get("description")
            if not name or not description:
                return None

            return Skill(
                name=str(name),
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
        """Decode base64 data, save to ``data/``, return URL block.

        Args:
            data_block: A :class:`DataBlock` with a
                :class:`Base64Source`.
        """
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
        path = os.path.join(self.workdir, "data", f"{h}{ext}")
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
