# -*- coding: utf-8 -*-
"""WorkspaceBase — abstract interface and shared backend-driven impl.

A workspace provides:

- **Resources** — skills available to the agent.
- **Tools** — MCPs and built-in tools for operating on resources.
- **Offload** — persistence of compressed context and tool results
  for agentic retrieval.

Three concrete implementations:

- :class:`agentscope.workspace.LocalWorkspace` — local filesystem.
- :class:`agentscope.workspace.DockerWorkspace` — Docker container.
- :class:`agentscope.workspace.E2BWorkspace` — E2B cloud sandbox.
- :class:`agentscope.workspace.OpenSandboxWorkspace` — OpenSandbox
  remote sandbox.

Consumers:

- **Agent** — calls ``list_mcps``, ``list_skills``, ``list_tools``,
  ``offload_context``, ``offload_tool_result``.
- **User** — dynamically adds/removes MCPs and skills via
  ``add_mcp`` / ``remove_mcp`` / ``add_skill`` / ``remove_skill``.
- **Developer** — manages lifecycle via ``initialize`` / ``close``.
- **Backend consumers** access the active backend via :meth:`get_backend`.

Shared implementation
---------------------

The base class implements every operation that can be expressed against
the workspace's :class:`BackendBase` plus a fixed layout derived from
``workdir``:

.. code-block:: text

    {workdir}/
    ├── .mcp          # persisted MCP client configs (JSON array)
    ├── data/         # offloaded multimodal payloads
    ├── skills/       # skill subdirectories
    └── sessions/     # per-session context and tool-result files

Subclasses only set ``self.workdir`` (the agent-visible root); all
other directory paths are derived via :meth:`BackendBase.join_path`,
keeping path semantics consistent with whichever backend is bound.
"""

import asyncio
import base64
import hashlib
import io
import json
import mimetypes
import os
import tarfile
from abc import abstractmethod
from copy import deepcopy
from pathlib import Path
from typing import Self

from pydantic import AnyUrl

from .._logging import logger
from .._utils._common import _generate_id
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
from ..tool import BackendBase, ToolBase
from ._utils import (
    DEFAULT_DATA_DIR,
    DEFAULT_MCP_FILE,
    DEFAULT_SESSIONS_DIR,
    DEFAULT_SKILLS_DIR,
)

_EXTRACT_TAR_SHIM = (
    "import tarfile, sys, os\n"
    "src, dst = sys.argv[1], sys.argv[2]\n"
    "os.makedirs(dst, exist_ok=True)\n"
    "dst_real = os.path.realpath(dst)\n"
    "tf = tarfile.open(src)\n"
    "try:\n"
    "    members = tf.getmembers()\n"
    "    for m in members:\n"
    "        target = os.path.realpath(os.path.join(dst, m.name))\n"
    "        if not (target == dst_real"
    " or target.startswith(dst_real + os.sep)):\n"
    "            raise Exception('unsafe tar member: ' + m.name)\n"
    "    tf.extractall(dst, members=members)\n"
    "finally:\n"
    "    tf.close()\n"
    "os.unlink(src)\n"
)


class WorkspaceBase:
    """Abstract base class for all workspace implementations.

    Subclasses provide concrete behaviour for one execution backend
    (local filesystem, Docker container, E2B sandbox). The base class
    owns:

    - lifecycle scaffolding (``async with`` protocol, ``is_alive``);
    - the canonical workspace layout derived from ``workdir`` (data/,
      skills/, sessions/, .mcp);
    - shared backend-driven implementations of offload, MCP
      persistence and a basic skill manager that subclasses can
      override (LocalWorkspace does, with a hash-indexed variant).
    """

    workspace_id: str
    """Unique identifier for this workspace instance."""

    workdir: str
    """Agent-visible root directory for workspace file operations."""

    is_alive: bool
    """If the workspace is still operational."""

    _backend: BackendBase | None
    """Current execution backend, available through :meth:`get_backend`."""

    default_mcps: list[MCPClient]
    """MCP clients to seed on first :meth:`initialize` when the
    persisted ``.mcp`` file is absent."""

    skill_paths: list[str]
    """Local skill directories to seed on first :meth:`initialize`."""

    _mcps: list[MCPClient]
    """Currently registered MCP clients (in-memory authoritative copy).

    :class:`LocalWorkspace` stores the local live handles directly;
    :class:`SandboxedWorkspaceBase` stores gateway-side
    :class:`GatewayMCPClient` wrappers (also ``MCPClient`` instances)
    so ``list_mcps`` / persistence work uniformly across both.
    """

    _mcp_lock: asyncio.Lock
    """Guards mutation of :attr:`_mcps` and the ``.mcp`` file."""

    _skill_lock: asyncio.Lock
    """Guards mutation of the ``skills/`` directory."""

    @property
    def _glob_helper_path(self) -> str | None:
        """Optional path (backend-side) to the ``Glob`` helper script.

        ``None`` means the :class:`Glob` builtin tool falls back to its
        default behaviour (suitable for :class:`LocalBackend`). Remote
        backends override this with a sandbox-/container-side script path
        so :class:`Glob` can run efficiently inside the workspace.
        """
        return None

    def __init__(
        self,
        *,
        workspace_id: str | None = None,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
    ) -> None:
        """Initialise the shared workspace state.

        Subclasses must call ``super().__init__`` and then set
        :attr:`workdir` themselves before any base-class method is
        invoked. Backend binding (``self._backend``) is left to the
        subclass (Local sets it eagerly; Docker/E2B set it during
        :meth:`initialize`).

        Args:
            workspace_id (`str | None`, optional):
                Existing identifier to adopt; ``None`` mints a fresh
                UUID.
            default_mcps (`list[MCPClient] | None`, optional):
                MCP clients to register when the workspace boots
                without a persisted ``.mcp`` file.
            skill_paths (`list[str] | None`, optional):
                Local skill directories to copy into ``skills/`` on
                first start.
        """
        self.workspace_id = workspace_id or _generate_id()
        self.is_alive = False
        self._backend = None

        self.default_mcps = list(default_mcps or [])
        self.skill_paths = list(skill_paths or [])

        self._mcps = []
        self._mcp_lock = asyncio.Lock()
        self._skill_lock = asyncio.Lock()

    # ── derived paths ──────────────────────────────────────────────

    @property
    def _data_dir(self) -> str:
        """``${workdir}/data`` — offloaded multimodal payloads."""
        return self.get_backend().join_path(self.workdir, DEFAULT_DATA_DIR)

    @property
    def _skills_dir(self) -> str:
        """``${workdir}/skills`` — skill subdirectories."""
        return self.get_backend().join_path(
            self.workdir,
            DEFAULT_SKILLS_DIR,
        )

    @property
    def _sessions_dir(self) -> str:
        """``${workdir}/sessions`` — per-session offload files."""
        return self.get_backend().join_path(
            self.workdir,
            DEFAULT_SESSIONS_DIR,
        )

    @property
    def _mcp_file(self) -> str:
        """``${workdir}/.mcp`` — persisted MCP registrations."""
        return self.get_backend().join_path(self.workdir, DEFAULT_MCP_FILE)

    @property
    def is_persistent(self) -> bool:
        """Whether the workspace storage survives :meth:`close`.

        Defaults to ``True``. Subclasses with conditional persistence
        (e.g. :class:`DockerWorkspace` without a host bind-mount)
        override this to gate the cost of writing ``.mcp`` and other
        files that would not survive the next session.
        """
        return True

    @staticmethod
    def _path_to_file_uri(path: str) -> str:
        """Convert an absolute backend-side path to a ``file://`` URI.

        Absolute POSIX paths (every remote backend, plus
        :class:`LocalBackend` on Linux/macOS) start with ``/`` and use
        the plain ``file://{path}`` form. Windows absolute paths
        (e.g. ``C:\\Users\\...``) round-trip through
        :meth:`pathlib.Path.as_uri` to produce ``file:///C:/...`` form.
        """
        if path.startswith("/"):
            return f"file://{path}"
        return Path(path).as_uri()

    # ── lifecycle (developer) ──────────────────────────────────────

    @abstractmethod
    async def initialize(self) -> None:
        """Provision resources, connect MCP servers, copy skills."""

    @abstractmethod
    async def close(self) -> None:
        """Release all resources and connections."""

    async def reset(self) -> None:
        """Reset the workspace to a clean state.

        Closes and removes all registered MCPs, deletes all skills,
        and wipes per-session state (offloaded context / tool results
        and any data files). Constructor-time ``default_mcps`` and
        ``skill_paths`` are **not** re-seeded — reset returns the
        workspace to an empty state, not its initial state.

        The default implementation is a no-op. Subclasses with user
        state must override this.
        """

    def get_backend(self) -> BackendBase:
        """Return the workspace's active filesystem/execution backend.

        Docker and E2B workspaces may replace their backend when reconnecting,
        so callers should resolve it from the workspace when beginning an
        operation rather than retaining a stale private ``_backend`` value.

        Raises:
            RuntimeError:
                If the workspace has not been initialized or has no active
                backend.
        """
        if self._backend is None:
            raise RuntimeError(
                f"{type(self).__name__} has no active backend. "
                "Initialize the workspace before requesting its backend.",
            )
        return self._backend

    async def __aenter__(self) -> Self:
        """Context manager support for ``async with``. Calls ``initialize()``
        and returns the workspace instance.
        """
        await self.initialize()
        self.is_alive = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Context manager support for ``async with``. Calls ``close()``
        and returns the workspace instance.
        """
        await self.close()
        self.is_alive = False

    # ── instructions ───────────────────────────────────────────────

    @abstractmethod
    async def get_instructions(self) -> str:
        """Workspace-specific system prompt fragment."""

    # ── for Agent: tool & MCP discovery ────────────────────────────

    async def list_tools(self) -> list[ToolBase]:
        """Built-in tools scoped to this workspace.

        Returns the six builtin tools (:class:`Bash`, :class:`Edit`,
        :class:`Glob`, :class:`Grep`, :class:`Read`, :class:`Write`),
        each bound to the workspace's active backend so that all
        filesystem and process I/O happens inside the workspace's
        execution environment. :class:`Bash` is rooted at
        :attr:`workdir`; :class:`Glob` receives the optional
        :attr:`_glob_helper_path` when the backend ships one.

        Raises:
            RuntimeError:
                If the workspace has not been initialised yet.
        """
        from ..tool import Bash, Edit, Glob, Grep, Read, Write

        backend = self.get_backend()
        glob_kwargs: dict = {"backend": backend}
        if self._glob_helper_path is not None:
            glob_kwargs["glob_helper_path"] = self._glob_helper_path
        return [
            Bash(cwd=self.workdir, backend=backend),
            Edit(backend=backend),
            Glob(**glob_kwargs),
            Grep(backend=backend),
            Read(backend=backend),
            Write(backend=backend),
        ]

    async def list_mcps(self) -> list[MCPClient]:
        """Return the currently registered MCP clients."""
        return list(self._mcps)

    # ── for User: dynamic MCP management ───────────────────────────

    @abstractmethod
    async def add_mcp(self, mcp_client: MCPClient) -> None:
        """Register a new MCP server.

        Args:
            mcp_client (`MCPClient`):
                The MCP to register.

        Raises:
            `ValueError`:
                If an MCP with the same name already exists.
        """

    @abstractmethod
    async def remove_mcp(self, name: str) -> None:
        """Deregister an MCP server by name.

        Args:
            name (`str`):
                MCP name to remove. Unknown names log a warning and
                return silently.
        """

    # ── MCP persistence (shared) ───────────────────────────────────

    async def _save_mcp_file(self) -> None:
        """Persist ``self._mcps`` to ``${workdir}/.mcp`` via backend.

        No-op when :attr:`is_persistent` is ``False`` (e.g. ephemeral
        Docker container without a host bind-mount). Failures are
        logged but not raised — the in-memory MCP list remains the
        authoritative copy regardless of whether disk persistence
        succeeded.

        Callers are expected to hold :attr:`_mcp_lock` already.
        """
        if not self.is_persistent:
            return
        backend = self._backend
        if backend is None:
            return
        payload = json.dumps(
            [m.model_dump(mode="json") for m in self._mcps],
            indent=2,
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            await backend.write_file(self._mcp_file, payload)
        except Exception as e:
            logger.warning(
                "Failed to save MCP file at %s: %s",
                self._mcp_file,
                e,
            )

    # ── for Agent: offload (shared) ────────────────────────────────

    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
    ) -> str:
        """Persist compressed context for agentic retrieval.

        Appends every message in ``msgs`` to
        ``${workdir}/sessions/<session_id>/context.jsonl`` (one
        message per JSONL line). Inline base64
        :class:`DataBlock` payloads are extracted into ``data/`` and
        rewritten as ``file://`` URL blocks before serialisation so
        the JSONL line size stays bounded.

        Args:
            session_id (`str`):
                Session-scope key used to partition offloaded data
                (one subdirectory per session).
            msgs (`list[Msg]`):
                Conversation messages to offload. Not mutated — a
                deep copy is used internally.

        Returns:
            `str`:
                Backend-side path of the JSONL file that received
                the new lines.
        """
        backend = self.get_backend()
        base = backend.join_path(self._sessions_dir, session_id)
        path = backend.join_path(base, "context.jsonl")

        copied = deepcopy(msgs)
        lines: list[str] = []
        for msg in copied:
            if not isinstance(msg.content, str):
                content: list = []
                for block in msg.content:
                    if isinstance(block, DataBlock) and isinstance(
                        block.source,
                        Base64Source,
                    ):
                        block = await self._offload_data_block(block)
                    content.append(block)
                msg.content = content
            lines.append(msg.model_dump_json())

        payload = "\n".join(lines) + "\n"

        existing = b""
        try:
            existing = await backend.read_file(path)
        except (FileNotFoundError, OSError):
            pass
        await backend.write_file(path, existing + payload.encode("utf-8"))
        return path

    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
    ) -> str:
        """Persist a single tool result as a flat text file.

        Writes ``${workdir}/sessions/<session_id>/tool_result-<id>.txt``.
        Text blocks are concatenated verbatim; :class:`DataBlock` items
        emit ``<data url='…' name='…' media_type='…'/>`` placeholders,
        with inline base64 payloads first offloaded to ``data/``.

        On a filename clash (same tool-result ``id`` written twice in
        one session) the new file is suffixed with ``(1)``, ``(2)``,
        … to avoid clobbering the prior content.

        Args:
            session_id (`str`):
                Session-scope key used to partition offloaded data.
            tool_result (`ToolResultBlock`):
                The tool result block to persist.

        Returns:
            `str`:
                Backend-side path of the offloaded text file.
        """
        backend = self.get_backend()
        base = backend.join_path(self._sessions_dir, session_id)
        path = backend.join_path(base, f"tool_result-{tool_result.id}.txt")

        index = 1
        while await backend.file_exists(path):
            path = backend.join_path(
                base,
                f"tool_result-{tool_result.id}({index}).txt",
            )
            index += 1

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
                        url = str(d.source.url)
                    else:
                        url = str(block.source.url)
                    parts.append(
                        f"<data url='{url}' name='{block.name}' "
                        f"media_type='{block.source.media_type}'/>",
                    )

        await backend.write_file(path, "".join(parts).encode("utf-8"))
        return path

    async def _offload_data_block(self, block: DataBlock) -> DataBlock:
        """Persist a base64 :class:`DataBlock` under ``data/``.

        The decoded payload is stored at
        ``${workdir}/data/<sha256-of-base64>.<ext>``. Hashing the
        *base64* text rather than the decoded bytes lets a second
        offload of the same block short-circuit (same key → same
        file → no write).

        Args:
            block (`DataBlock`):
                A data block. Blocks already backed by a
                :class:`URLSource` are returned unchanged.

        Returns:
            `DataBlock`:
                A new :class:`DataBlock` whose source is a ``file://``
                URL pointing at the persisted file inside the
                workspace.
        """
        if not isinstance(block.source, Base64Source):
            return block

        backend = self.get_backend()
        hash_str = hashlib.sha256(block.source.data.encode()).hexdigest()
        ext = mimetypes.guess_extension(block.source.media_type) or ".bin"
        path = backend.join_path(self._data_dir, f"{hash_str}{ext}")

        if not await backend.file_exists(path):
            await backend.write_file(
                path,
                base64.b64decode(block.source.data),
            )

        return DataBlock(
            id=block.id,
            name=block.name,
            source=URLSource(
                url=AnyUrl(self._path_to_file_uri(path)),
                media_type=block.source.media_type,
            ),
        )

    # ── skill management (shared, simple) ──────────────────────────

    async def list_skills(self) -> list[Skill]:
        """Enumerate skills under ``${workdir}/skills``.

        Walks ``skills/`` recursively, parses every ``SKILL.md``'s
        YAML front matter, and yields one :class:`Skill` per file
        that has both ``name`` and ``description``.

        Subclasses with richer indexing (e.g.
        :class:`LocalWorkspace` with its ``.skills`` hash index)
        override this method.

        Returns:
            `list[Skill]`:
                Skills available to the agent. Empty when ``skills/``
                is missing or contains no parseable ``SKILL.md``.
        """
        import frontmatter as fm

        backend = self.get_backend()
        if not await backend.is_dir(self._skills_dir):
            return []

        entries = await backend.list_dir(self._skills_dir, recursive=True)

        skills: list[Skill] = []
        for md_path in entries:
            if backend.basename(md_path) != "SKILL.md":
                continue
            try:
                raw = await backend.read_file(md_path)
                doc = fm.loads(raw.decode("utf-8"))
                name = doc.get("name")
                desc = doc.get("description")
                if not name or not desc:
                    continue
                skills.append(
                    Skill(
                        name=str(name),
                        description=str(desc),
                        dir=backend.dirname(md_path),
                        markdown=doc.content or "",
                        updated_at=0.0,
                    ),
                )
            except Exception as e:
                logger.warning("Failed to load skill %s: %s", md_path, e)
        return skills

    async def add_skill(self, skill_path: str) -> None:
        """Copy a local skill directory into ``${workdir}/skills``.

        Tars the directory on the host, writes the archive to the
        backend's tmp area, and extracts it via ``python3 -c`` inside
        the sandbox — two round trips regardless of skill size, and
        portable across any backend whose image ships ``python3``
        (same contract as the gateway shim).

        Subclasses with richer dedup (e.g. :class:`LocalWorkspace`
        with hash-indexed conflict resolution) override this method.

        Args:
            skill_path (`str`):
                Path to a skill directory on the local filesystem.

        Raises:
            ValueError:
                If ``SKILL.md`` is missing or a directory with the
                same basename already exists in ``skills/``.
            RuntimeError:
                If extraction inside the sandbox fails.
        """
        skill_md = os.path.join(skill_path, "SKILL.md")
        if not os.path.isfile(skill_md):
            raise ValueError(
                f"Invalid skill at {skill_path!r}: SKILL.md not found",
            )

        backend = self.get_backend()

        async with self._skill_lock:
            dir_name = os.path.basename(os.path.abspath(skill_path))
            remote_dir = backend.join_path(self._skills_dir, dir_name)

            if await backend.file_exists(remote_dir):
                raise ValueError(
                    f"Skill directory {dir_name!r} already exists in "
                    f"{self._skills_dir}",
                )

            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tf:
                tf.add(skill_path, arcname=dir_name)
            tar_bytes = buf.getvalue()

            tmp_path = f"/tmp/skill-{_generate_id()}.tar"
            await backend.write_file(tmp_path, tar_bytes)

            await backend.exec_shell(
                ["mkdir", "-p", self._skills_dir],
            )
            result = await backend.exec_shell(
                [
                    "python3",
                    "-c",
                    _EXTRACT_TAR_SHIM,
                    tmp_path,
                    self._skills_dir,
                ],
            )
            if not result.ok():
                raise RuntimeError(
                    f"Failed to extract skill {dir_name!r}: "
                    f"{result.stderr.decode('utf-8', 'replace')}",
                )

            logger.info("Added skill %r at %s", dir_name, remote_dir)

    async def remove_skill(self, name: str) -> None:
        """Remove a skill by its agent-facing ``name`` (front matter).

        Looks up the skill via :meth:`list_skills` and ``rm -rf``-style
        deletes its directory through the backend.

        Args:
            name (`str`):
                The agent-facing name of the skill to remove.

        Raises:
            KeyError:
                If the skill is not found in the workspace.
        """
        backend = self.get_backend()
        skills = await self.list_skills()
        target_dir: str | None = None
        for s in skills:
            if s.name == name:
                target_dir = s.dir
                break
        if target_dir is None:
            available = [s.name for s in skills]
            raise KeyError(
                f"Skill {name!r} not found. Available: {available}",
            )
        await backend.delete_path(target_dir)
        logger.info("Removed skill %r at %s", name, target_dir)

    async def _setup_skills(self) -> None:
        """Copy :attr:`skill_paths` into ``${workdir}/skills`` once.

        Skips seeding when:

        - :attr:`skill_paths` is empty;
        - the backend is not bound; or
        - ``skills/`` already contains entries (assume the prior
          run, or the user, is the source of truth).

        Individual failures are logged and skipped — a single bad
        skill cannot block startup.
        """
        if not self.skill_paths:
            return
        backend = self._backend
        if backend is None:
            return
        entries = await backend.list_dir(self._skills_dir)
        if entries:
            return
        for path in self.skill_paths:
            try:
                await self.add_skill(path)
            except Exception as e:
                logger.warning(
                    "Skip skill %r: %s",
                    path,
                    e,
                )
