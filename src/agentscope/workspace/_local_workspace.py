# -*- coding: utf-8 -*-
"""The local workspace class."""

import asyncio
import hashlib
import json
import os
import re
import shutil
from typing import TypedDict

import frontmatter

from ._utils import DEFAULT_WORKSPACE_INSTRUCTIONS
from .._logging import logger
from ..mcp import MCPClient
from ..skill import Skill
from ..tool._builtin._backend import LocalBackend
from ._base import WorkspaceBase


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


class LocalWorkspace(WorkspaceBase):
    """Local-directory workspace.

    Layout::

        {workdir}/
        ├── .mcp          # persisted MCP client configs (JSON array)
        ├── data/         # offloaded multimodal files
        ├── skills/       # skill subdirectories
        └── sessions/     # per-session context and tool-result files
    """

    def __init__(
        self,
        *,
        workdir: str,
        workspace_id: str | None = None,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        instructions: str = DEFAULT_WORKSPACE_INSTRUCTIONS,
    ) -> None:
        """Construct a :class:`LocalWorkspace`.

        Args:
            workdir (`str`):
                Filesystem path to the workspace root. Created on
                demand. Always resolved to an absolute path.
            workspace_id (`str | None`, optional):
                Existing workspace identifier to adopt. ``None``
                generates a fresh UUID.
            default_mcps (`list[MCPClient] | None`, optional):
                MCP clients seeded into a brand-new workspace.
                Ignored on subsequent restarts that already have a
                persisted ``<workdir>/.mcp`` file.
            skill_paths (`list[str] | None`, optional):
                Local skill directories seeded into
                ``<workdir>/skills`` on first :meth:`initialize`.
            instructions (`str`, defaults to \
            `_DEFAULT_WORKSPACE_INSTRUCTIONS`):
                System-prompt fragment template returned by
                :meth:`get_instructions`. Supports the ``{workdir}``
                placeholder.
        """
        super().__init__(workspace_id=workspace_id)

        # ── serializable config ─────────────────────────────────
        self.workdir = os.path.abspath(workdir)
        self.instructions = instructions.format(
            backend="local",
            workdir=self.workdir,
        )

        # ── seed-only ───────────────────────────────────────────
        self.default_mcps: list[MCPClient] = list(default_mcps or [])
        self.skill_paths: list[str] = list(skill_paths or [])

        # ── runtime state ───────────────────────────────────────
        self._backend = LocalBackend()
        self._mcps: list[MCPClient] = []

        self._skill_lock = asyncio.Lock()
        self._mcp_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialise the workspace.

        MCP state is restored from ``.mcp`` if it exists; otherwise
        ``default_mcps`` are used and persisted so the next start picks
        them up from disk. ``skill_paths`` are seeded on first use.

        Idempotent: a no-op when the workspace is already alive.
        """
        if self.is_alive:
            return

        os.makedirs(self.workdir, exist_ok=True)

        # Restore or seed MCPs
        mcp_file = os.path.join(self.workdir, ".mcp")
        if await self._backend.file_exists(mcp_file):
            raw = await self._backend.read_file(mcp_file)
            raw_list = json.loads(raw.decode("utf-8"))
            for m in raw_list:
                try:
                    self._mcps.append(MCPClient.model_validate(m))
                except Exception as e:
                    logger.warning(
                        "Skipping invalid MCP entry '%s': %s",
                        m.get("name", "?"),
                        e,
                    )
        else:
            self._mcps = list(self.default_mcps)
            await self._save_mcp_file()

        failed: list[MCPClient] = []
        for mcp in self._mcps:
            if mcp.is_stateful and not mcp.is_connected:
                try:
                    await mcp.connect()
                except Exception as e:
                    logger.warning(
                        "Failed to connect stateful MCP '%s': %s, removing.",
                        mcp.name,
                        e,
                    )
                    failed.append(mcp)
        for mcp in failed:
            self._mcps.remove(mcp)

        # Seed skills
        skills_dir = os.path.join(self.workdir, "skills")
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
        for skill_path in self.skill_paths:
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
            mtime = await self._backend.stat_mtime(skills_dir)
            skills_file["skills_dir_mtime"] = (
                mtime if mtime is not None else 0.0
            )
            await self._save_skills_file(skills_dir, skills_file)

        self.is_alive = True

    async def get_instructions(self) -> str:
        """Get the workspace instructions."""
        return self.instructions

    async def _load_skills_file(self, skills_dir: str) -> _SkillsFile:
        """Load the .skills index file, returning an empty structure if absent.

        Args:
            skills_dir (`str`): The skills directory path.

        Returns:
            `_SkillsFile`: The parsed index, or a fresh empty structure.
        """
        path = os.path.join(skills_dir, ".skills")
        if not await self._backend.file_exists(path):
            return {"skills_dir_mtime": 0.0, "skills": {}}

        try:
            raw = await self._backend.read_file(path)
            data = json.loads(raw.decode("utf-8"))
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
            await self._backend.write_file(
                path,
                json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"),
            )
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
            if not await self._backend.file_exists(skill_md_path):
                logger.warning(
                    "Invalid skill at %s: SKILL.md not found",
                    skill_path,
                )
                return None

            # Read and parse SKILL.md
            raw = await self._backend.read_file(skill_md_path)
            content_str = raw.decode("utf-8")

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

    async def close(self) -> None:
        """Close every stateful MCP attached to this workspace.

        ``LocalWorkspace`` itself owns no resources (the workdir is
        the persistence layer and is left untouched), but stdio /
        stateful HTTP MCPs hold long-lived sessions that have to be
        closed explicitly. Stateless HTTP MCPs are skipped — they
        spin up an ad-hoc session per call and have nothing to close.
        """
        async with self._mcp_lock:
            for mcp in self._mcps:
                if mcp.is_stateful and mcp.is_connected:
                    try:
                        await mcp.close()
                    except Exception as e:
                        logger.warning(
                            (
                                "Failed to close MCP %r "
                                "when closing local workspace: %s"
                            ),
                            mcp.name,
                            e,
                        )
        self.is_alive = False

    async def reset(self) -> None:
        """Return the workspace to an empty state.

        Closes and drops all MCPs (including the persisted ``.mcp``)
        and deletes ``skills/``, ``sessions/``, and ``data/``.
        ``default_mcps`` and ``skill_paths`` are not re-seeded.
        """
        async with self._mcp_lock:
            for mcp in self._mcps:
                if mcp.is_stateful and mcp.is_connected:
                    try:
                        await mcp.close()
                    except Exception as e:
                        logger.warning(
                            "MCP %r close failed during reset: %s",
                            mcp.name,
                            e,
                        )
            self._mcps = []

            mcp_file = os.path.join(self.workdir, ".mcp")
            await self._backend.delete_path(mcp_file)

        async with self._skill_lock:
            skills_path = os.path.join(self.workdir, "skills")
            await self._backend.delete_path(skills_path)

        for sub in ("sessions", "data"):
            path = os.path.join(self.workdir, sub)
            await self._backend.delete_path(path)

    async def list_skills(self) -> list[Skill]:
        """List all skills available in the workspace.

        The method uses the .skills index for agent-facing names, compares the
        skills directory mtime to detect manual additions/removals since the
        last write, and reconciles the index when a change is found.

        Returns:
            `list[Skill]`:
                A list of Skill objects found in the workspace.
        """
        skills_dir = os.path.join(self.workdir, "skills")
        async with self._skill_lock:
            if not await self._backend.is_dir(skills_dir):
                return []

            skills_file = await self._load_skills_file(skills_dir)
            current_mtime = await self._backend.stat_mtime(skills_dir)
            if current_mtime is None:
                current_mtime = 0.0

            # Detect if the skills directory has changed since last indexing
            if current_mtime != skills_file["skills_dir_mtime"]:
                skills_file = await self._reconcile_skills_dir(
                    skills_dir,
                    skills_file,
                    current_mtime,
                )

            # Load skills from disk using the index for the agent-facing name
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
        original_mtime = skills_file["skills_dir_mtime"]

        # Collect actual subdirectories on disk
        entries = await self._backend.list_dir(skills_dir)
        actual_dirs: set[str] = set()
        for d in entries:
            dir_path = os.path.join(skills_dir, d)
            if await self._backend.is_dir(dir_path):
                actual_dirs.add(d)

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

        # Save if index changed OR if mtime needs updating
        # (mtime change without index change means non-skill files were
        # added/removed, we still need to record the new mtime to avoid
        # re-reconciling on every list_skills call)
        if updated or current_mtime != original_mtime:
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
            if not await self._backend.file_exists(skill_md_path):
                return None

            updated_at = await self._backend.stat_mtime(skill_md_path)
            if updated_at is None:
                updated_at = 0.0

            raw = await self._backend.read_file(skill_md_path)
            content_str = raw.decode("utf-8")
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

    async def add_mcp(self, mcp_client: MCPClient) -> None:
        """Add an MCP client, connect it if stateful, and persist.

        Args:
            mcp_client (`MCPClient`):
                The MCP client to add.
        """
        async with self._mcp_lock:
            if mcp_client.is_stateful and not mcp_client.is_connected:
                await mcp_client.connect()
            self._mcps.append(mcp_client)
            await self._save_mcp_file()

    async def remove_mcp(self, name: str) -> None:
        """Remove an MCP client by name, disconnecting it if stateful.

        Args:
            name (`str`):
                The ``name`` field of the client to remove.
        """
        async with self._mcp_lock:
            for i, mcp in enumerate(self._mcps):
                if mcp.name == name:
                    if mcp.is_stateful and mcp.is_connected:
                        await mcp.close()
                    self._mcps.pop(i)
                    await self._save_mcp_file()
                    return
        logger.warning("MCP client %r not found in workspace", name)

    async def add_skill(self, skill_path: str) -> None:
        """Add a skill to the workspace by copying from the given path.

        The skill directory must contain a valid ``SKILL.md`` file with
        ``name`` and ``description`` frontmatter fields.  Duplicate skills
        (identified by the SHA-256 hash of ``SKILL.md``) are silently skipped.
        Name and directory conflicts are resolved by appending a numeric
        suffix.

        Args:
            skill_path (`str`):
                Absolute or relative path to the skill directory to copy.

        Raises:
            ValueError: If the skill at ``skill_path`` is invalid (missing or
                malformed ``SKILL.md``).
        """
        skills_dir = os.path.join(self.workdir, "skills")
        async with self._skill_lock:
            os.makedirs(skills_dir, exist_ok=True)

            result = await self._validate_and_hash_skill(skill_path)
            if result is None:
                raise ValueError(
                    f"Invalid skill at {skill_path!r}: missing or malformed "
                    "SKILL.md (requires 'name' and 'description' fields).",
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
                "Copied skill '%s' (agent name: '%s') from %s to %s",
                raw_name,
                agent_name,
                skill_path,
                dest_path,
            )

            existing[dir_name] = {"hash": skill_hash, "skill_name": agent_name}
            skills_file["skills"] = existing
            mtime = await self._backend.stat_mtime(skills_dir)
            skills_file["skills_dir_mtime"] = (
                mtime if mtime is not None else 0.0
            )
            await self._save_skills_file(skills_dir, skills_file)

    async def remove_skill(self, name: str) -> None:
        """Remove a skill from the workspace by its agent-facing name.

        The skill directory is deleted from disk and the ``.skills`` index is
        updated.  If no skill with the given name is found, a warning is
        logged and the method returns without error.

        Args:
            name (`str`):
                The agent-facing name of the skill to remove (as stored in the
                ``.skills`` index, i.e. the ``name`` field from ``SKILL.md``
                possibly with a numeric suffix for de-duplication).
        """
        skills_dir = os.path.join(self.workdir, "skills")
        async with self._skill_lock:
            if not await self._backend.is_dir(skills_dir):
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
                logger.warning("Skill %r not found in workspace", name)
                return

            skill_dir_path = os.path.join(skills_dir, target_dir)
            if await self._backend.is_dir(skill_dir_path):
                await self._backend.delete_path(skill_dir_path)
                logger.info(
                    "Removed skill '%s' from %s",
                    name,
                    skill_dir_path,
                )
            else:
                logger.warning(
                    (
                        "Skill directory %r not found on disk; "
                        "removing index entry"
                    ),
                    skill_dir_path,
                )

            del existing[target_dir]
            skills_file["skills"] = existing
            mtime = await self._backend.stat_mtime(skills_dir)
            skills_file["skills_dir_mtime"] = (
                mtime if mtime is not None else 0.0
            )
            await self._save_skills_file(skills_dir, skills_file)
