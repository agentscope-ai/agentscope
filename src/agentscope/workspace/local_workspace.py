# -*- coding: utf-8 -*-
"""LocalWorkspace — local-filesystem workspace (no container).

The agent operates directly on a host directory. MCP clients run
on the host as well. Skills are plain subdirectories.

Architecture (from diagram 3):

- ``self._mcps`` holds MCP clients directly.
- ``add_mcp`` / ``remove_mcp`` manage the list at runtime.
- ``add_skill`` / ``remove_skill`` copy/delete directories.
- No container, no gateway.
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

from .workspace_base import WorkspaceBase
from .config import MCPServerConfig
from ..mcp import HttpMCPConfig, MCPClient, StdioMCPConfig
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
from .._logging import logger


# ── skill index types ─────────────────────────────────────────────


class _SkillEntry(TypedDict):
    hash: str
    skill_name: str


class _SkillsFile(TypedDict):
    skills_dir_mtime: float
    skills: dict[str, _SkillEntry]


def _sanitize_dir_name(name: str) -> str:
    return re.sub(r"[^\w一-鿿-]", "_", name)


# ── instructions ──────────────────────────────────────────────────


_DEFAULT_INSTRUCTIONS = """<workspace>
You have access to a local workspace at {workdir} with the following structure:

```
{workdir}
├── data/        # offloaded multimodal files (images, etc.)
├── skills/      # reusable skills, each in its own subdirectory
└── sessions/    # session context and tool results
```

This workspace is your personal working environment for completing various tasks.

### Project Directory
- Create a dedicated subdirectory for each task or project under the workspace root.
- Always create a `README.md` at the project root documenting what the project is about.

### Python Environment
- If a project requires Python, use `uv` to create an isolated virtual environment:
  ```shell
  uv venv && uv pip install ...
  ```
</workspace>"""


# ── LocalWorkspace ────────────────────────────────────────────────


class LocalWorkspace(WorkspaceBase):
    """Workspace backed by a local directory on the host filesystem.

    Layout::

        {workdir}/
        ├── data/                       # offloaded binary data
        ├── skills/                     # skill directories
        │   ├── {skill_name}/
        │   │   └── SKILL.md
        └── sessions/
            └── {session_id}/
                ├── context.jsonl
                └── tool_result-{id}.txt
    """

    def __init__(
        self,
        workdir: str,
        skill_paths: list[str] | None = None,
        mcps: list[MCPClient] | None = None,
        instructions: str = _DEFAULT_INSTRUCTIONS,
    ) -> None:
        self._id = uuid.uuid4().hex[:12]
        self._workdir = os.path.abspath(workdir)
        self._skill_paths = list(
            dict.fromkeys(os.path.abspath(p) for p in (skill_paths or [])),
        )
        self._instructions = instructions
        self._mcps: list[MCPClient] = list(mcps or [])

    @property
    def workspace_id(self) -> str:
        return self._id

    # ── lifecycle ──────────────────────────────────────────────────

    async def initialize(self) -> None:
        for mcp in self._mcps:
            if (
                mcp.is_stateful or mcp.mcp_config.type == "stdio_mcp"
            ) and not mcp.is_connected:
                await mcp.connect()

        skills_dir = os.path.join(self._workdir, "skills")
        os.makedirs(skills_dir, exist_ok=True)
        await self._install_initial_skills(skills_dir)

    async def close(self) -> None:
        for mcp in self._mcps:
            if (
                mcp.is_stateful or mcp.mcp_config.type == "stdio_mcp"
            ) and mcp.is_connected:
                await mcp.close()

    # ── instructions ───────────────────────────────────────────────

    async def get_instructions(self) -> str:
        return self._instructions.format(workdir=self._workdir)

    # ── tool discovery ─────────────────────────────────────────────

    async def list_tools(self) -> list[ToolBase]:
        return [Bash(), Edit(), Glob(), Grep(), Read(), Write()]

    async def list_mcps(self) -> list[MCPClient]:
        return list(self._mcps)

    # ── skill discovery ────────────────────────────────────────────

    async def list_skills(self) -> list[Skill]:
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
        results = await asyncio.gather(*tasks, return_exceptions=True)

        skills: list[Skill] = []
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

    # ── offload ────────────────────────────────────────────────────

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

        os.makedirs(os.path.dirname(path), exist_ok=True)
        async with aiofiles.open(path, mode="a", encoding="utf-8") as f:
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

        os.makedirs(os.path.dirname(path), exist_ok=True)
        async with aiofiles.open(path, mode="w", encoding="utf-8") as f:
            await f.write("".join(parts))
        return path

    # ── dynamic MCP management ─────────────────────────────────────

    async def add_mcp(self, config: MCPServerConfig) -> None:
        for existing in self._mcps:
            if existing.name == config.name:
                raise ValueError(
                    f"MCP {config.name!r} already exists. "
                    "Remove it first or use a different name.",
                )

        if config.transport == "http":
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
        logger.info("LocalWorkspace: added MCP %r", config.name)

    async def remove_mcp(self, name: str) -> None:
        for i, mcp in enumerate(self._mcps):
            if mcp.name == name:
                if mcp.is_connected:
                    await mcp.close()
                self._mcps.pop(i)
                logger.info("LocalWorkspace: removed MCP %r", name)
                return
        raise KeyError(
            f"MCP {name!r} not found. "
            f"Available: {[m.name for m in self._mcps]}",
        )

    # ── dynamic skill management ───────────────────────────────────

    async def add_skill(self, skill_path: str) -> None:
        skill_path = os.path.abspath(skill_path)
        result = await self._validate_and_hash_skill(skill_path)
        if result is None:
            raise ValueError(
                f"Invalid skill at {skill_path}: "
                "SKILL.md missing or incomplete",
            )

        _, raw_name, skill_hash = result
        skills_dir = os.path.join(self._workdir, "skills")
        os.makedirs(skills_dir, exist_ok=True)
        skills_file = await self._load_skills_file(skills_dir)
        existing = skills_file["skills"]
        existing_hashes = {e["hash"] for e in existing.values()}

        if skill_hash in existing_hashes:
            logger.info("Skill %r already exists (by hash), skipping", raw_name)
            return

        existing_agent_names = {e["skill_name"] for e in existing.values()}
        agent_name = raw_name
        counter = 1
        while agent_name in existing_agent_names:
            agent_name = f"{raw_name} ({counter})"
            counter += 1

        base_dir = _sanitize_dir_name(raw_name)
        dir_name = base_dir
        counter = 1
        while dir_name in existing:
            dir_name = f"{base_dir}_{counter}"
            counter += 1

        dest = os.path.join(skills_dir, dir_name)
        await asyncio.to_thread(shutil.copytree, skill_path, dest)

        existing[dir_name] = _SkillEntry(
            hash=skill_hash,
            skill_name=agent_name,
        )
        skills_file["skills_dir_mtime"] = await aiofiles.ospath.getmtime(
            skills_dir,
        )
        await self._save_skills_file(skills_dir, skills_file)
        logger.info("LocalWorkspace: added skill %r as %r", raw_name, agent_name)

    async def remove_skill(self, name: str) -> None:
        skills_dir = os.path.join(self._workdir, "skills")
        skills_file = await self._load_skills_file(skills_dir)
        existing = skills_file["skills"]

        target_dir: str | None = None
        for dir_name, entry in existing.items():
            if entry["skill_name"] == name:
                target_dir = dir_name
                break

        if target_dir is None:
            available = [e["skill_name"] for e in existing.values()]
            raise KeyError(
                f"Skill {name!r} not found. Available: {available}",
            )

        dest = os.path.join(skills_dir, target_dir)
        if await aiofiles.ospath.isdir(dest):
            await asyncio.to_thread(shutil.rmtree, dest)

        del existing[target_dir]
        skills_file["skills_dir_mtime"] = await aiofiles.ospath.getmtime(
            skills_dir,
        )
        await self._save_skills_file(skills_dir, skills_file)
        logger.info("LocalWorkspace: removed skill %r", name)

    # ── internal: initial skill install ────────────────────────────

    async def _install_initial_skills(self, skills_dir: str) -> None:
        skills_file = await self._load_skills_file(skills_dir)
        existing = skills_file["skills"]
        existing_hashes = {e["hash"] for e in existing.values()}
        existing_agent_names = {e["skill_name"] for e in existing.values()}
        existing_dir_names = set(existing.keys())

        updated = False
        for skill_path in self._skill_paths:
            result = await self._validate_and_hash_skill(skill_path)
            if result is None:
                continue

            _, raw_name, skill_hash = result
            if skill_hash in existing_hashes:
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

            dest = os.path.join(skills_dir, dir_name)
            if not os.path.realpath(dest).startswith(
                os.path.realpath(skills_dir) + os.sep,
            ):
                logger.warning(
                    "Skill %r resolves outside skills_dir, skipping",
                    raw_name,
                )
                continue

            try:
                await asyncio.to_thread(
                    shutil.copytree,
                    skill_path,
                    dest,
                    dirs_exist_ok=False,
                )
            except Exception as e:
                logger.warning("Failed to copy skill %r: %s", raw_name, e)
                continue

            existing[dir_name] = _SkillEntry(
                hash=skill_hash,
                skill_name=agent_name,
            )
            existing_hashes.add(skill_hash)
            existing_agent_names.add(agent_name)
            existing_dir_names.add(dir_name)
            updated = True

        if updated:
            skills_file["skills"] = existing
            skills_file["skills_dir_mtime"] = (
                await aiofiles.ospath.getmtime(skills_dir)
            )
            await self._save_skills_file(skills_dir, skills_file)

    # ── internal: skill file I/O ───────────────────────────────────

    async def _load_skills_file(self, skills_dir: str) -> _SkillsFile:
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
            logger.warning("Failed to load .skills: %s", e)
            return {"skills_dir_mtime": 0.0, "skills": {}}

    async def _save_skills_file(
        self,
        skills_dir: str,
        data: _SkillsFile,
    ) -> None:
        path = os.path.join(skills_dir, ".skills")
        try:
            async with aiofiles.open(path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.warning("Failed to save .skills: %s", e)

    async def _validate_and_hash_skill(
        self,
        skill_path: str,
    ) -> tuple[str, str, str] | None:
        skill_md = os.path.join(skill_path, "SKILL.md")
        try:
            if not await aiofiles.ospath.isfile(skill_md):
                return None
            async with aiofiles.open(skill_md, "r", encoding="utf-8") as f:
                content_str = await f.read()
            content = frontmatter.loads(content_str)
            name = content.get("name")
            desc = content.get("description")
            if not name or not desc:
                return None
            h = hashlib.sha256(content_str.encode("utf-8")).hexdigest()
            return skill_path, str(name), h
        except Exception as e:
            logger.warning("Skill validation failed for %s: %s", skill_path, e)
            return None

    async def _reconcile_skills_dir(
        self,
        skills_dir: str,
        skills_file: _SkillsFile,
        current_mtime: float,
    ) -> _SkillsFile:
        existing = skills_file["skills"]
        original_mtime = skills_file["skills_dir_mtime"]

        def _list_dirs() -> set[str]:
            return {
                d
                for d in os.listdir(skills_dir)
                if os.path.isdir(os.path.join(skills_dir, d))
            }

        actual_dirs = await asyncio.to_thread(_list_dirs)
        indexed_dirs = set(existing.keys())

        updated = False
        for removed in indexed_dirs - actual_dirs:
            del existing[removed]
            updated = True

        existing_agent_names = {e["skill_name"] for e in existing.values()}
        existing_hashes = {e["hash"] for e in existing.values()}

        for new_dir in actual_dirs - indexed_dirs:
            sp = os.path.join(skills_dir, new_dir)
            result = await self._validate_and_hash_skill(sp)
            if result is None:
                continue
            _, raw_name, skill_hash = result
            if skill_hash in existing_hashes:
                continue

            agent_name = raw_name
            counter = 1
            while agent_name in existing_agent_names:
                agent_name = f"{raw_name} ({counter})"
                counter += 1

            existing[new_dir] = _SkillEntry(
                hash=skill_hash,
                skill_name=agent_name,
            )
            existing_agent_names.add(agent_name)
            existing_hashes.add(skill_hash)
            updated = True

        skills_file["skills"] = existing
        skills_file["skills_dir_mtime"] = current_mtime
        if updated or current_mtime != original_mtime:
            await self._save_skills_file(skills_dir, skills_file)
        return skills_file

    async def _load_single_skill(
        self,
        skill_dir: str,
        skill_name: str,
    ) -> Skill | None:
        skill_md = os.path.join(skill_dir, "SKILL.md")
        try:
            if not await aiofiles.ospath.isfile(skill_md):
                return None
            updated_at = await aiofiles.ospath.getmtime(skill_md)
            async with aiofiles.open(skill_md, "r", encoding="utf-8") as f:
                content = frontmatter.loads(await f.read())
            desc = content.get("description")
            if not desc:
                return None
            return Skill(
                name=skill_name,
                description=str(desc),
                dir=skill_dir,
                markdown=content.content,
                updated_at=updated_at,
            )
        except Exception as e:
            logger.warning("Failed to load skill from %s: %s", skill_dir, e)
            return None

    # ── internal: data offload ─────────────────────────────────────

    async def _offload_data_block(self, data_block: DataBlock) -> DataBlock:
        if isinstance(data_block.source, URLSource):
            return data_block
        h = hashlib.sha256(data_block.source.data.encode()).hexdigest()
        ext = mimetypes.guess_extension(data_block.source.media_type) or ".bin"
        path = os.path.join(self._workdir, "data", f"{h}{ext}")
        if not await aiofiles.ospath.exists(path):
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
