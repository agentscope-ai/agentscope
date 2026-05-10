# -*- coding: utf-8 -*-
"""The local workspace class."""
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
from typing import Any, TypedDict

import aiofiles
import aiofiles.ospath
import frontmatter
from pydantic import AnyUrl

from ._base import WorkspaceBase
from ..mcp import MCPClient
from ..message import (
    TextBlock,
    DataBlock,
    ToolResultBlock,
    Msg,
    URLSource,
    Base64Source,
)
from ..skill import Skill
from ..tool import (
    ToolBase,
    Bash,
    Edit,
    Glob,
    Grep,
    Read,
    Write,
)
from .._logging import logger


class _SkillEntry(TypedDict):
    """A single entry in the .skills index file."""

    hash: str
    """SHA-256 hash of the skill's SKILL.md content."""
    skill_name: str
    """The name exposed to the agent (may differ from the directory name)."""


class _SkillsFile(TypedDict):
    """Schema of the .skills index file stored inside skills_dir."""

    skills_dir_mtime: float
    """mtime of skills_dir at the time the index was last written."""
    skills: dict[str, _SkillEntry]
    """Mapping from directory name (relative to skills_dir) to skill entry."""


def _sanitize_dir_name(name: str) -> str:
    """Sanitize a skill name into a safe directory name.

    Allowed characters: ASCII letters, digits, CJK unified ideographs,
    hyphens, and underscores. Everything else is replaced with ``_``.

    Args:
        name (`str`):
            The raw skill name from SKILL.md frontmatter.

    Returns:
        `str`:
            A sanitized string safe to use as a directory name on Windows,
            macOS, and Linux.
    """
    return re.sub(r"[^\w一-鿿-]", "_", name)


_DEFAULT_WORKSPACE_INSTRUCTIONS = """<workspace>
You have access to a local workspace at {workdir} with the following structure:

```
{workdir}
├── data/        # offloaded multimodal files (images, etc.)
├── skills/      # reusable skills, each in its own subdirectory
└── sessions/    # session context and tool results
```

This workspace is your personal working environment for completing various tasks.
You are responsible for keeping it clean, structured, and easy to navigate over time.

### Project Directory
- Create a dedicated subdirectory for each task or project under the workspace root.
- Name the directory concisely and descriptively, e.g. `20240315_web-scraper`, so it remains identifiable long after creation.
- Always create a `README.md` at the project root documenting:
  - What the project is about
  - When it was created
  - Key decisions or context that would help you resume work later
  - The changes you have made (and when)

### Version Control
- It is recommended to initialize a `git` repository in each project directory
  to track changes and allow rollbacks.
- Always create a `.gitignore` before the first commit to exclude unwanted files
  (e.g. virtual environments, cache, secrets).

### Python Environment
- If a project requires Python, use `uv` to create an isolated virtual environment
  inside the project directory:
  ```shell
  uv venv && uv pip install ...
  ```
- Never install packages into a shared or global environment — each project must
  manage its own dependencies to avoid conflicts.
</workspace>"""  # noqa: E501


class LocalWorkspace(WorkspaceBase):
    # pylint: disable=line-too-long
    """The local workspace class, which chooses a local directory as the
    agent workspace. It's organized as

    ```
    {workdir}
    ├── data/                         # the offloaded multimodal files, e.g. images
    ├── skills/                       # the skill directories, each skill has a subdirectory under skills/
    │   ├── {skill_name}/             # the skill directory
    │   │   ├── {skill_files}         # the files for the skill
    └── sessions/                     # the session directories, each session has a subdirectory under sessions/
        └── {session_id}/             # the session directory
            ├── context.jsonl         # the offloaded context for the session, in jsonl format
            └── tool_result-{id}.txt  # the offloaded tool results for the session, in txt format
    ```
    """  # noqa: E501

    def __init__(
        self,
        workdir: str,
        skill_paths: list[str] | None = None,
        instructions: str = _DEFAULT_WORKSPACE_INSTRUCTIONS,
    ) -> None:
        """Initialize the local workspace.

        Args:
            workdir (`str`):
                The agent workdir, used to offload the compressed context for
                agentic retrieval.
            skill_paths (`list[str] | None`, optional):
                A list of skill paths that will be copied into the target
                skill directory when the workspace is first initialized.
                Defaults to `None`.
            instructions (`str`, optional):
                The workspace instructions to guide the agent how to use it.
        """
        self._workdir = os.path.abspath(workdir)
        # Deduplicate skill paths by absolute path
        self._skill_paths = list(
            dict.fromkeys(os.path.abspath(p) for p in (skill_paths or [])),
        )
        self._instructions = instructions

    async def initialize(self) -> None:
        """Initialize the local workspace by copying skills to the target
        directory.

        Skills are processed sequentially to guarantee correct deduplication:
        1. Validate each skill and compute its SKILL.md hash
        2. Skip if the hash already exists in the workspace
        3. Resolve directory-name and agent-name conflicts with numeric
        suffixes
        4. Copy the skill, then update the .skills index
        """
        skills_dir = os.path.join(self._workdir, "skills")
        os.makedirs(skills_dir, exist_ok=True)

        skills_file = await self._load_skills_file(skills_dir)
        existing: dict[str, _SkillEntry] = skills_file["skills"]

        # Build fast-lookup sets from the current index
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

            # Skip if already present (by content hash)
            if skill_hash in existing_hashes:
                logger.info(
                    "Skill '%s' (hash: %s...) already exists, skipping",
                    raw_name,
                    skill_hash[:8],
                )
                continue

            # Resolve agent-facing name conflict
            agent_name = raw_name
            counter = 1
            while agent_name in existing_agent_names:
                agent_name = f"{raw_name} ({counter})"
                counter += 1

            # Resolve directory name conflict
            base_dir = _sanitize_dir_name(raw_name)
            dir_name = base_dir
            counter = 1
            while dir_name in existing_dir_names:
                dir_name = f"{base_dir}_{counter}"
                counter += 1

            dest_path = os.path.join(skills_dir, dir_name)

            # Defensive path-traversal check
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
                    str(e),
                )
                continue

            logger.info(
                "Copied skill '%s' (agent name: '%s') from %s to %s",
                raw_name,
                agent_name,
                skill_path,
                dest_path,
            )

            entry: _SkillEntry = {"hash": skill_hash, "skill_name": agent_name}
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

    async def get_instructions(self) -> str:
        """Get the workspace instructions."""
        return self._instructions.format(workdir=self._workdir)

    async def _load_skills_file(self, skills_dir: str) -> _SkillsFile:
        """Load the .skills index file, returning an empty structure if absent.

        Args:
            skills_dir (`str`): The skills directory path.

        Returns:
            `_SkillsFile`: The parsed index, or a fresh empty structure.
        """
        path = os.path.join(skills_dir, ".skills")
        if not await aiofiles.ospath.exists(path):
            return {"skills_dir_mtime": 0.0, "skills": {}}

        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                data = json.loads(await f.read())
            return _SkillsFile(
                skills_dir_mtime=float(data.get("skills_dir_mtime", 0.0)),
                skills=data.get("skills", {}),
            )
        except Exception as e:
            logger.warning("Failed to load .skills from %s: %s", path, str(e))
            return {"skills_dir_mtime": 0.0, "skills": {}}

    async def _save_skills_file(
        self,
        skills_dir: str,
        data: _SkillsFile,
    ) -> None:
        """Persist the .skills index file.

        Args:
            skills_dir (`str`): The skills directory path.
            data (`_SkillsFile`): The index to write.
        """
        path = os.path.join(skills_dir, ".skills")
        try:
            async with aiofiles.open(path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.warning("Failed to save .skills to %s: %s", path, str(e))

    async def _validate_skill(
        self,
        skill_path: str,
    ) -> tuple[str, str, str] | None:
        """Validate if a skill path contains a valid SKILL.md file.

        Args:
            skill_path (`str`):
                The path to the skill directory.

        Returns:
            `tuple[str, str, str] | None`:
                A tuple of (name, description, skill_md_content) if valid,
                None otherwise.
        """
        skill_md_path = os.path.join(skill_path, "SKILL.md")

        try:
            # Check if SKILL.md exists
            if not await aiofiles.ospath.isfile(skill_md_path):
                logger.warning(
                    "Invalid skill at %s: SKILL.md not found",
                    skill_path,
                )
                return None

            # Read and parse SKILL.md
            async with aiofiles.open(
                skill_md_path,
                "r",
                encoding="utf-8",
            ) as f:
                content_str = await f.read()

            # Parse frontmatter
            content = frontmatter.loads(content_str)
            name = content.get("name")
            description = content.get("description")

            if not name or not description:
                logger.warning(
                    "Invalid skill at %s: SKILL.md missing required "
                    "fields (name or description)",
                    skill_path,
                )
                return None

            return str(name), str(description), content_str

        except Exception as e:
            logger.warning(
                "Failed to validate skill at %s: %s",
                skill_path,
                str(e),
            )
            return None

    async def _validate_and_hash_skill(
        self,
        skill_path: str,
    ) -> tuple[str, str, str] | None:
        """Validate a skill and compute its hash.

        Args:
            skill_path (`str`):
                The path to the skill directory.

        Returns:
            `tuple[str, str, str] | None`:
                A tuple of (skill_path, skill_name, skill_hash) if valid,
                None otherwise.
        """
        validation_result = await self._validate_skill(skill_path)
        if validation_result is None:
            return None

        skill_name, _, skill_md_content = validation_result

        # Compute hash
        skill_hash = hashlib.sha256(
            skill_md_content.encode("utf-8"),
        ).hexdigest()

        return skill_path, skill_name, skill_hash

    async def _offload_data_block(self, data_block: DataBlock) -> DataBlock:
        """Offload the data block by persisting it as local files. This avoids
        embedding large base64-encoded data directly in the offload files,
        keeping them lightweight and readable.

        Args:
            data_block (`DataBlock`):
                The data block with base64 source.

        Returns:
            `DataBlock`:
                A new data block with the same metadata but with the source
                replaced by the local file path where the data is stored.
        """
        if isinstance(data_block.source, URLSource):
            return data_block

        # Use the full SHA-256 hex digest (256-bit) as the filename stem.
        # A full hash collision is computationally infeasible, so an existing
        # file with the same name is guaranteed to have identical content —
        # no need to read and compare bytes.
        hash_str = hashlib.sha256(data_block.source.data.encode()).hexdigest()
        ext = mimetypes.guess_extension(data_block.source.media_type) or ".bin"
        path = os.path.join(self._workdir, "data", f"{hash_str}{ext}")

        # Reuse the existing file directly — same hash ⟹ same content.
        if not await aiofiles.ospath.exists(path):
            # Write decoded bytes to disk and return a URL-source DataBlock.
            os.makedirs(os.path.dirname(path), exist_ok=True)
            async with aiofiles.open(path, "wb") as f:
                await f.write(base64.b64decode(data_block.source.data))

        return DataBlock(
            id=data_block.id,
            name=data_block.name,
            source=URLSource(
                url=AnyUrl(Path(path).as_uri()),
                media_type=data_block.source.media_type,
            ),
        )

    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
        **kwargs: Any,
    ) -> str:
        """Offload the compressed messages into the local directory for
        further processing.

        Args:
            session_id (`str`):
                The session id.
            msgs (`list[Msg]`):
                The messages to offload.

        Returns:
            `str`:
                The file path to the offloaded message.
        """
        path = os.path.join(
            self._workdir,
            "sessions",
            session_id,
            "context.jsonl",
        )

        copied_msgs = deepcopy(msgs)
        msgs_strs = []
        for msg in copied_msgs:
            if not isinstance(msg.content, str):
                content = []
                for block in msg.content:
                    if isinstance(block, DataBlock) and isinstance(
                        block.source,
                        Base64Source,
                    ):
                        content.append(await self._offload_data_block(block))
                    else:
                        content.append(block)
                msg.content = content
            msgs_strs.append(msg.model_dump_json())

        msgs_str = "\n".join(msgs_strs)
        # Create parent directory if it doesn't exist
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Offload the context into the local file
        # Always end with a newline to ensure proper JSONL format when
        # appending
        async with aiofiles.open(
            path,
            mode="a",
            encoding="utf-8",
        ) as file:
            await file.write(msgs_str + "\n")
        return path

    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
        **kwargs: Any,
    ) -> str:
        """Offload the tool results into the local directory for agentic
        retrieval.

        Args:
            session_id (`str`):
                The session id.
            tool_result (`ToolResultBlock`):
                The tool result.

        Returns:
            `str`:
                The file path to the offloaded tool results.
        """
        path = os.path.join(
            self._workdir,
            "sessions",
            session_id,
            f"tool_result-{tool_result.id}.txt",
        )

        res_strs = []
        if isinstance(tool_result.output, str):
            res_strs.append(tool_result.output)
        else:
            for block in tool_result.output:
                if isinstance(block, TextBlock):
                    res_strs.append(block.text)
                elif isinstance(block, DataBlock):
                    if isinstance(block.source, Base64Source):
                        data_block = await self._offload_data_block(block)
                        url = data_block.source.url
                    else:
                        url = block.source.url
                    res_strs.append(
                        f"<data url='{url}' name='{block.name}' "
                        f"media_type='{block.source.media_type}'/>",
                    )

        # Create parent directory if it doesn't exist
        os.makedirs(os.path.dirname(path), exist_ok=True)
        async with aiofiles.open(path, mode="w", encoding="utf-8") as file:
            await file.write("".join(res_strs))

        return path

    async def close(self) -> None:
        """Close the workspace and clean up resources.

        For LocalWorkspace, this is a no-op as there are no persistent
        connections or resources to clean up.
        """

    async def list_tools(self) -> list[ToolBase]:
        """List all tools available in the workspace."""
        return [
            Bash(),
            Edit(),
            Glob(),
            Grep(),
            Read(),
            Write(),
        ]

    async def list_skills(self) -> list[Skill]:
        """List all skills available in the workspace.

        The method uses the .skills index for agent-facing names, compares the
        skills directory mtime to detect manual additions/removals since the
        last write, and reconciles the index when a change is found.

        Returns:
            `list[Skill]`:
                A list of Skill objects found in the workspace.
        """
        skills_dir = os.path.join(self._workdir, "skills")

        if not await aiofiles.ospath.isdir(skills_dir):
            return []

        skills_file = await self._load_skills_file(skills_dir)
        current_mtime = await aiofiles.ospath.getmtime(skills_dir)

        # Detect whether the skills directory has changed since last indexing
        if current_mtime != skills_file["skills_dir_mtime"]:
            skills_file = await self._reconcile_skills_dir(
                skills_dir,
                skills_file,
                current_mtime,
            )

        # Load each skill from disk using the index for the agent-facing name
        tasks = [
            self._load_single_skill(
                os.path.join(skills_dir, dir_name),
                entry["skill_name"],
            )
            for dir_name, entry in skills_file["skills"].items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        skills: list = []
        for dir_name, result in zip(skills_file["skills"], results):
            if isinstance(result, Exception):
                logger.warning(
                    "Failed to load skill from %s: %s",
                    dir_name,
                    str(result),
                )
            elif result is not None:
                skills.append(result)

        return skills

    async def _reconcile_skills_dir(
        self,
        skills_dir: str,
        skills_file: _SkillsFile,
        current_mtime: float,
    ) -> _SkillsFile:
        """Reconcile the .skills index after the skills directory has changed.

        Handles:
        - Manually deleted subdirectories: removed from the index.
        - Manually added subdirectories: validated and added with conflict
          resolution for both directory name and agent-facing skill name.

        Args:
            skills_dir (`str`): Path to the skills directory.
            skills_file (`_SkillsFile`): The current (stale) index.
            current_mtime (`float`): The freshly-read mtime of skills_dir.

        Returns:
            `_SkillsFile`: The updated index (also persisted to disk).
        """
        existing: dict[str, _SkillEntry] = skills_file["skills"]

        # Collect actual subdirectories on disk
        def _list_dirs() -> set[str]:
            return {
                d
                for d in os.listdir(skills_dir)
                if os.path.isdir(os.path.join(skills_dir, d))
            }

        actual_dirs = await asyncio.to_thread(_list_dirs)
        indexed_dirs = set(existing.keys())

        updated = False

        # Remove entries for directories that no longer exist
        for removed in indexed_dirs - actual_dirs:
            logger.info(
                "Skill directory '%s' removed, updating index",
                removed,
            )
            del existing[removed]
            updated = True

        # Add entries for directories not yet in the index
        existing_agent_names: set[str] = {
            e["skill_name"] for e in existing.values()
        }
        existing_hashes: set[str] = {e["hash"] for e in existing.values()}

        for new_dir in actual_dirs - indexed_dirs:
            skill_path = os.path.join(skills_dir, new_dir)
            result = await self._validate_and_hash_skill(skill_path)
            if result is None:
                continue

            _, raw_name, skill_hash = result

            if skill_hash in existing_hashes:
                logger.info(
                    "Manually added skill '%s' already tracked by hash, "
                    "skipping",
                    new_dir,
                )
                continue

            agent_name = raw_name
            counter = 1
            while agent_name in existing_agent_names:
                agent_name = f"{raw_name} ({counter})"
                counter += 1

            entry: _SkillEntry = {"hash": skill_hash, "skill_name": agent_name}
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
        if updated:
            await self._save_skills_file(skills_dir, skills_file)

        return skills_file

    async def _load_single_skill(
        self,
        skill_dir: str,
        skill_name: str,
    ) -> Skill | None:
        """Load a single skill from disk using the agent-facing name from
        the index.

        Args:
            skill_dir (`str`):
                The skill directory path containing SKILL.md.
            skill_name (`str`):
                The agent-facing name stored in the .skills index.

        Returns:
            `Skill | None`:
                A Skill object or None if the SKILL.md is missing/invalid.
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
                str(e),
            )
            return None

    async def list_mcps(self) -> list[MCPClient]:
        """The workspace doesn't need to handle the MCP servers. Just leave
        it empty."""
        return []
