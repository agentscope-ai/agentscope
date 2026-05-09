# -*- coding: utf-8 -*-
"""The local workspace class."""
import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

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

        This method uses a two-phase approach to avoid race conditions:
        1. Phase 1: Validate and compute hashes for all skills concurrently
        2. Phase 2: Deduplicate by hash, then copy unique skills concurrently
        """
        skills_dir = os.path.join(self._workdir, "skills")

        # Ensure skills directory exists
        os.makedirs(skills_dir, exist_ok=True)

        # Load existing skill hashes
        existing_hashes = await self._load_skill_hashes(skills_dir)

        # Phase 1: Validate all skills and compute hashes concurrently
        validation_tasks = [
            self._validate_and_hash_skill(skill_path)
            for skill_path in self._skill_paths
        ]
        validation_results: list = await asyncio.gather(
            *validation_tasks,
            return_exceptions=True,
        )

        # Collect valid skills and deduplicate by hash
        # Keep only the first occurrence of each hash
        skill_by_hash: dict[str, tuple[str, str]] = {}  # hash -> (path, name)
        for i, result in enumerate(validation_results):
            if isinstance(result, Exception):
                logger.warning(
                    "Failed to validate skill at %s: %s",
                    self._skill_paths[i],
                    str(result),
                )
                continue

            if result is None:
                # Invalid skill, already logged in _validate_and_hash_skill
                continue

            skill_path, skill_name, skill_hash = result

            # Skip if hash already exists in workspace
            if skill_hash in existing_hashes:
                logger.info(
                    "Skill '%s' (hash: %s...) already exists, skipping",
                    skill_name,
                    skill_hash[:8],
                )
                continue

            # Deduplicate: keep only the first occurrence of each hash
            if skill_hash not in skill_by_hash:
                skill_by_hash[skill_hash] = (skill_path, skill_name)
            else:
                logger.info(
                    "Skipping duplicate skill at %s (same hash as %s)",
                    skill_path,
                    skill_by_hash[skill_hash][0],
                )

        # Phase 2: Copy unique skills concurrently
        copy_tasks = [
            self._copy_skill(skill_path, skill_name, skills_dir)
            for skill_hash, (skill_path, skill_name) in skill_by_hash.items()
        ]
        copy_results = await asyncio.gather(
            *copy_tasks,
            return_exceptions=True,
        )

        # Update hash mappings with successfully copied skills
        updated = False
        for i, result in enumerate(copy_results):
            skill_hash = list(skill_by_hash.keys())[i]
            skill_path, skill_name = skill_by_hash[skill_hash]

            if isinstance(result, Exception):
                logger.warning(
                    "Failed to copy skill at %s: %s",
                    skill_path,
                    str(result),
                )
                continue

            if result:  # Successfully copied
                existing_hashes[skill_hash] = skill_name
                updated = True

        # Save updated hash mappings if any new skills were added
        if updated:
            await self._save_skill_hashes(skills_dir, existing_hashes)

    async def get_instructions(self) -> str:
        """Get the workspace instructions."""
        return self._instructions.format(workdir=self._workdir)

    async def _load_skill_hashes(self, skills_dir: str) -> dict[str, str]:
        """Load existing skill hash mappings from .skills file.

        Args:
            skills_dir (`str`):
                The skills directory path.

        Returns:
            `dict[str, str]`:
                A dictionary mapping hash to skill name. Returns empty dict
                if file doesn't exist or cannot be parsed.
        """
        hash_file = os.path.join(skills_dir, ".skills")
        if not await aiofiles.ospath.exists(hash_file):
            return {}

        try:
            async with aiofiles.open(hash_file, "r", encoding="utf-8") as f:
                content = await f.read()
                return json.loads(content)
        except Exception as e:
            logger.warning(
                "Failed to load skill hashes from %s: %s",
                hash_file,
                str(e),
            )
            return {}

    async def _save_skill_hashes(
        self,
        skills_dir: str,
        hashes: dict[str, str],
    ) -> None:
        """Save skill hash mappings to .skills file.

        Args:
            skills_dir (`str`):
                The skills directory path.
            hashes (`dict[str, str]`):
                A dictionary mapping hash to skill name.
        """
        hash_file = os.path.join(skills_dir, ".skills")
        try:
            async with aiofiles.open(hash_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps(hashes, indent=2))
        except Exception as e:
            logger.warning(
                "Failed to save skill hashes to %s: %s",
                hash_file,
                str(e),
            )

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

    async def _copy_skill(
        self,
        skill_path: str,
        skill_name: str,
        skills_dir: str,
    ) -> bool:
        """Copy a skill directory to the target skills directory.

        Args:
            skill_path (`str`):
                The source skill directory path.
            skill_name (`str`):
                The skill name.
            skills_dir (`str`):
                The target skills directory.

        Returns:
            `bool`:
                True if successfully copied, False otherwise.
        """
        try:
            dest_path = os.path.join(skills_dir, skill_name)

            # Check if destination already exists
            if await aiofiles.ospath.exists(dest_path):
                logger.warning(
                    "Destination path %s already exists, skipping skill '%s'",
                    dest_path,
                    skill_name,
                )
                return False

            def _sync_copy() -> None:
                """Synchronous copy operation to be run in thread."""
                shutil.copytree(skill_path, dest_path, dirs_exist_ok=False)

            await asyncio.to_thread(_sync_copy)

            logger.info(
                "Copied skill '%s' from %s to %s",
                skill_name,
                skill_path,
                dest_path,
            )

            return True

        except Exception as e:
            logger.warning(
                "Failed to copy skill at %s: %s",
                skill_path,
                str(e),
            )
            return False

    async def _process_single_skill(
        self,
        skill_path: str,
        skills_dir: str,
        existing_hashes: dict[str, str],
    ) -> tuple[bool, str | None, str | None]:
        """Process a single skill: validate, check hash, and copy if needed.

        Args:
            skill_path (`str`):
                The source skill directory path.
            skills_dir (`str`):
                The target skills directory.
            existing_hashes (`dict[str, str]`):
                Existing hash to skill name mappings.

        Returns:
            `tuple[bool, str | None, str | None]`:
                A tuple of (success, skill_hash, skill_name).
                - success: True if processed successfully (copied or skipped)
                - skill_hash: The computed hash, or None if validation failed
                - skill_name: The skill name, or None if validation failed
        """
        try:
            # Step 1: Validate the skill
            validation_result = await self._validate_skill(skill_path)
            if validation_result is None:
                return False, None, None

            name, _, skill_md_content = validation_result

            # Step 2: Compute hash
            skill_hash = hashlib.sha256(
                skill_md_content.encode("utf-8"),
            ).hexdigest()

            # Step 3: Check if skill already exists
            if skill_hash in existing_hashes:
                logger.info(
                    "Skill '%s' (hash: %s...) already exists, skipping",
                    name,
                    skill_hash[:8],
                )
                return True, skill_hash, name

            # Step 4: Copy skill to target directory
            dest_path = os.path.join(skills_dir, name)

            # Check if destination already exists (shouldn't happen)
            if await aiofiles.ospath.exists(dest_path):
                logger.warning(
                    "Destination path %s already exists, skipping skill '%s'",
                    dest_path,
                    name,
                )
                return False, skill_hash, name

            def _sync_copy() -> None:
                """Synchronous copy operation to be run in thread."""
                shutil.copytree(skill_path, dest_path, dirs_exist_ok=False)

            await asyncio.to_thread(_sync_copy)

            logger.info(
                "Copied skill '%s' from %s to %s",
                name,
                skill_path,
                dest_path,
            )

            return True, skill_hash, name

        except Exception as e:
            logger.warning(
                "Failed to process skill at %s: %s",
                skill_path,
                str(e),
            )
            return False, None, None

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
        async with aiofiles.open(path, mode="w") as file:
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

        This method scans the skills directory and loads all valid skills.
        Each skill must have a SKILL.md file with valid frontmatter containing
        name and description fields.

        Returns:
            `list[Skill]`:
                A list of Skill objects found in the workspace.
        """
        skills_dir = os.path.join(self._workdir, "skills")

        # Check if skills directory exists
        if not await aiofiles.ospath.isdir(skills_dir):
            logger.info(
                "Skills directory %s does not exist, returning empty list",
                skills_dir,
            )
            return []

        # Find all subdirectories in skills directory
        def _find_skill_dirs() -> list[str]:
            """Find all subdirectories that might contain skills."""
            if not os.path.isdir(skills_dir):
                return []
            return [
                os.path.join(skills_dir, d)
                for d in os.listdir(skills_dir)
                if os.path.isdir(os.path.join(skills_dir, d))
            ]

        skill_dirs = await asyncio.to_thread(_find_skill_dirs)

        if not skill_dirs:
            logger.info(
                "No skill directories found in %s",
                skills_dir,
            )
            return []

        # Load all skills concurrently
        tasks = [
            self._load_single_skill(skill_dir) for skill_dir in skill_dirs
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out None results and exceptions
        skills: list[Skill] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(
                    "Failed to load skill from %s: %s",
                    skill_dirs[i],
                    str(result),
                )
            elif isinstance(result, Skill):
                skills.append(result)

        return skills

    async def _load_single_skill(self, skill_dir: str) -> Skill | None:
        """Load a single skill from a skill directory.

        Args:
            skill_dir (`str`):
                The skill directory path containing SKILL.md.

        Returns:
            `Skill | None`:
                A Skill object or None if loading failed.
        """
        skill_md_path = os.path.join(skill_dir, "SKILL.md")

        try:
            # Check if SKILL.md exists
            if not await aiofiles.ospath.isfile(skill_md_path):
                return None

            # Get file modification time
            updated_at = await aiofiles.ospath.getmtime(skill_md_path)

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
                    skill_dir,
                )
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
                str(e),
            )
            return None

    async def list_mcps(self) -> list[MCPClient]:
        """The workspace doesn't need to handle the MCP servers. Just leave
        it empty."""
        return []
