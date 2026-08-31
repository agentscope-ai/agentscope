# -*- coding: utf-8 -*-
# pylint: disable=too-many-lines
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
    ├── .mcp          # declared MCP configs, per agent and session
    ├── data/         # offloaded multimodal payloads
    ├── skills/       # .seed template, plus one partition per agent
    └── sessions/     # per-session context and tool-result files

MCPs are declared per agent and session, and instantiated lazily:
nothing connects until that session first calls ``list_mcps``, and
each session gets its own instances so stateful MCP state never leaks
across agents or sessions. ``.mcp`` holds only the declarations, and
only for sessions that diverged from ``default_mcps``.

Skills are partitioned per agent, one directory level under
``skills/``. Skills are editable in place — an agent may improve its
own — so the partition is what keeps one agent's edits off another's.
An agent's first skill call equips its partition from ``skills/.seed``,
the template holding ``skill_paths``; the partition existing afterwards
is what stops a seed the agent deleted from coming back.

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
import time
from abc import abstractmethod
from copy import deepcopy
from pathlib import Path
from typing import AsyncIterator, Literal, Self

from pydantic import AnyUrl

from .._logging import logger
from .._utils._common import _generate_id, _normalize_local_path
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

#: Current ``.mcp`` schema version. Version 1 was a bare JSON list of
#: specs with no ids; it is read back under the legacy empty ids.
MCP_FILE_VERSION = 2

#: Partition under ``skills/`` used when a caller names no agent — an
#: ordinary partition, read and written like any other, that the SDK
#: reaches when it is driven without agents at all.
DEFAULT_SKILL_PARTITION = "default"

#: Template under ``skills/`` copied into a partition the first time
#: its agent shows up. Not a partition, and never read directly.
SKILL_SEED_DIR = ".seed"

_DEFAULT_MAX_LIVE_STATEFUL_MCPS = 40

#: Moves a pre-partition ``skills/`` into the seed template, so the
#: content a shared workspace already had equips every agent that
#: shows up. Idempotent: a migrated ``skills/`` holds only
#: directories. Argv: skills dir, seed dir name.
_MIGRATE_SKILLS_SHIM = (
    "import os, shutil, sys\n"
    "skills, seed = sys.argv[1], sys.argv[2]\n"
    "if not os.path.isdir(skills):\n"
    "    sys.exit(0)\n"
    "stale = []\n"
    "for entry in os.listdir(skills):\n"
    "    if entry == seed:\n"
    "        continue\n"
    "    path = os.path.join(skills, entry)\n"
    "    if entry == '.skills' and os.path.isfile(path):\n"
    "        stale.append((entry, '.index'))\n"
    "    elif os.path.isfile(os.path.join(path, 'SKILL.md')):\n"
    "        stale.append((entry, entry))\n"
    "if not stale:\n"
    "    sys.exit(0)\n"
    "dst = os.path.join(skills, seed)\n"
    "os.makedirs(dst, exist_ok=True)\n"
    "for entry, name in stale:\n"
    "    shutil.move(os.path.join(skills, entry), os.path.join(dst, name))\n"
    "print(len(stale))\n"
)

#: Equips one partition from the seed template, once. The rename is
#: what makes it safe against a concurrent first touch: the loser
#: fails and drops its copy. Argv: seed dir, partition dir.
_EQUIP_PARTITION_SHIM = (
    "import os, shutil, sys\n"
    "seed, partition = sys.argv[1], sys.argv[2]\n"
    "if os.path.isdir(partition):\n"
    "    sys.exit(0)\n"
    "os.makedirs(os.path.dirname(partition), exist_ok=True)\n"
    "staging = partition + '.equipping-' + str(os.getpid())\n"
    "if os.path.isdir(seed):\n"
    "    shutil.copytree(seed, staging)\n"
    "else:\n"
    "    os.makedirs(staging)\n"
    "try:\n"
    "    os.rename(staging, partition)\n"
    "except OSError:\n"
    "    shutil.rmtree(staging, ignore_errors=True)\n"
    "print('equipped')\n"
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

#: Expands an archive inside the sandbox. Runs there rather than on the
#: server so a traversing or bomb-sized archive detonates in the
#: isolated environment. Argv: src, dst, format, max extracted bytes.
_EXTRACT_ARCHIVE_SHIM = (
    "import os, sys, tarfile, zipfile\n"
    "src, dst, fmt, limit = sys.argv[1:5]\n"
    "limit = int(limit)\n"
    "os.makedirs(dst, exist_ok=True)\n"
    "dst_real = os.path.realpath(dst)\n"
    "def check(name):\n"
    "    target = os.path.realpath(os.path.join(dst, name))\n"
    "    if not (target == dst_real"
    " or target.startswith(dst_real + os.sep)):\n"
    "        raise Exception('unsafe archive member: ' + name)\n"
    "if fmt == 'zip':\n"
    "    ar = zipfile.ZipFile(src)\n"
    "    members = ar.infolist()\n"
    "    total = sum(m.file_size for m in members)\n"
    "    names = [m.filename for m in members]\n"
    "else:\n"
    "    ar = tarfile.open(src)\n"
    "    members = ar.getmembers()\n"
    "    total = sum(m.size for m in members)\n"
    "    names = [m.name for m in members]\n"
    "    for m in members:\n"
    "        if m.issym() or m.islnk():\n"
    "            check(os.path.join(os.path.dirname(m.name), m.linkname))\n"
    "try:\n"
    "    if total > limit:\n"
    "        raise Exception('archive expands to %d bytes, limit is %d'\n"
    "                        % (total, limit))\n"
    "    for name in names:\n"
    "        check(name)\n"
    "    ar.extractall(dst, members)\n"
    "finally:\n"
    "    ar.close()\n"
    "os.unlink(src)\n"
)

#: Ceiling on what an archive may expand to inside the sandbox.
DEFAULT_MAX_EXTRACTED_BYTES = 500 * 1024 * 1024


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

    max_live_stateful_mcps: int
    """Cap on live stateful MCP instances retained for *other*
    agents and sessions. See :meth:`_enforce_mcp_capacity`."""

    _mcp_specs: dict[tuple[str, str], list[MCPClient]]
    """Declared MCP configs keyed by agent id and session id — the
    persisted layer. A missing session inherits :attr:`default_mcps`;
    an empty list means none."""

    _mcp_instances: dict[tuple[str, str], dict[str, MCPClient]]
    """Live handles keyed by agent id and session id, then by MCP
    name — built lazily, never persisted."""

    _mcp_last_used: dict[tuple[str, str], float]
    """Last :meth:`list_mcps` per agent/session, driving LRU eviction.
    Turn-level granularity — tool calls do not reach the workspace."""

    _mcp_lock: asyncio.Lock
    """Guards mutation of the MCP dicts and the ``.mcp`` file."""

    _equipped_partitions: set[str]
    """Partitions already equipped from the seed template, so the
    check costs nothing after an agent's first skill call."""

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
        max_live_stateful_mcps: int | None = None,
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
                MCP clients seeded into any agent/session that has
                never added or removed an MCP of its own.
            skill_paths (`list[str] | None`, optional):
                Local skill directories to copy into ``skills/`` on
                first start.
            max_live_stateful_mcps (`int | None`, optional):
                Cap on concurrently live *stateful* MCP instances.
                ``None`` derives ``max(40, 2 * <stateful defaults>)``
                so one session can always start its own seeded set.
        """
        self.workspace_id = workspace_id or _generate_id()
        self.is_alive = False
        self._backend = None

        self.default_mcps = list(default_mcps or [])
        self.skill_paths = [
            _normalize_local_path(path) for path in skill_paths or []
        ]
        self.max_live_stateful_mcps = max_live_stateful_mcps or max(
            _DEFAULT_MAX_LIVE_STATEFUL_MCPS,
            2 * len([m for m in self.default_mcps if m.is_stateful]),
        )

        self._mcp_specs = {}
        self._mcp_instances = {}
        self._mcp_last_used = {}
        self._equipped_partitions = set()
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
    def _python_command(self) -> str:
        """Interpreter that runs the shims below through the backend.

        Sandbox images all ship ``python3``. A backend executing on the
        host does not get that guarantee — Windows has ``python`` or a
        Store stub — so those workspaces override this.
        """
        return "python3"

    @property
    def _skill_seed_dir(self) -> str:
        """``skills/.seed`` — the template every partition starts from."""
        return self.get_backend().join_path(
            self._skills_dir,
            SKILL_SEED_DIR,
        )

    def _skill_partition(self, agent_id: str | None) -> str:
        """``skills/<agent_id>`` — where one agent's skills live.

        Skills are writable in place, so the partition is the boundary
        that keeps one agent's edits off another's. ``None`` maps to
        :data:`DEFAULT_SKILL_PARTITION`, an ordinary partition like any
        other — nobody else reads it.

        Args:
            agent_id (`str | None`):
                The owning agent, or ``None`` for the default one.

        Returns:
            `str`:
                Absolute path of the partition directory.

        Raises:
            ValueError:
                If ``agent_id`` cannot be used as a directory name.
        """
        # A leading dot would collide with ``.seed`` and with ``.``
        # and ``..``, which is the whole of the escaping problem.
        if agent_id and (
            agent_id.startswith(".") or "/" in agent_id or "\\" in agent_id
        ):
            raise ValueError(
                f"Agent id {agent_id!r} is not usable as a skill partition "
                f"name.",
            )
        return self.get_backend().join_path(
            self._skills_dir,
            agent_id or DEFAULT_SKILL_PARTITION,
        )

    async def _equip_partition(self, agent_id: str | None) -> str:
        """Return the agent's partition, creating it on first sight.

        A partition that does not exist yet is copied from
        :attr:`_skill_seed_dir`, which is how ``skill_paths`` reach a
        new agent. Its existence afterwards is the "already equipped"
        marker, so a seed the agent later deletes stays deleted — the
        same rule that keeps ``default_mcps`` from coming back.

        Args:
            agent_id (`str | None`):
                The agent to equip, or ``None`` for the default one.

        Returns:
            `str`:
                Absolute path of the partition directory.
        """
        partition = self._skill_partition(agent_id)
        if partition in self._equipped_partitions:
            return partition
        backend = self.get_backend()
        result = await backend.exec_shell(
            [
                self._python_command,
                "-c",
                _EQUIP_PARTITION_SHIM,
                self._skill_seed_dir,
                partition,
            ],
        )
        if not result.ok():
            raise RuntimeError(
                f"Failed to equip skill partition {partition!r}: "
                f"{result.stderr.decode('utf-8', 'replace')}",
            )
        self._equipped_partitions.add(partition)
        return partition

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

    def _declared_specs(
        self,
        agent_id: str,
        session_id: str,
    ) -> list[MCPClient]:
        """Configs declared for one agent/session, seeded from defaults.

        A session missing from :attr:`_mcp_specs` has never been
        modified and inherits fresh copies of :attr:`default_mcps`; an
        empty list means it explicitly has none.
        """
        declared = self._mcp_specs.get((agent_id, session_id))
        if declared is not None:
            return declared
        return [
            MCPClient.model_validate(m.model_dump(mode="json"))
            for m in self.default_mcps
        ]

    async def list_mcps(
        self,
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> list[MCPClient]:
        """Return the MCP clients for one agent/session.

        Instances are built on first use and are private to that
        agent/session, so stateful sessions (browser cookies, login
        state) never leak across agents or sessions. Instances dropped
        by :meth:`_enforce_mcp_capacity` are rebuilt here.

        Subclasses that route through a gateway
        (:class:`SandboxedWorkspaceBase`) override this to hand back
        gateway-wired proxies instead of local clients.

        Args:
            agent_id (`str | None`, optional):
                The owning agent. ``None`` means the legacy ``""``.
                Keyword-only: both ids are plain strings, so passing
                them positionally could silently hit another session.
            session_id (`str | None`, optional):
                The owning session. ``None`` means the legacy ``""``.
        """
        agent_id, session_id = agent_id or "", session_id or ""
        async with self._mcp_lock:
            self._mcp_last_used[(agent_id, session_id)] = time.monotonic()
            live = self._mcp_instances.setdefault((agent_id, session_id), {})
            specs = self._declared_specs(agent_id, session_id)
            for spec in specs:
                if spec.name in live:
                    continue
                await self._enforce_mcp_capacity(agent_id, session_id, spec)
                try:
                    # Copy rather than share, so two sessions running
                    # the same MCP hold independent state.
                    client = MCPClient.model_validate(
                        spec.model_dump(mode="json"),
                    )
                    if client.is_stateful:
                        await client.connect()
                    live[spec.name] = client
                except Exception as e:
                    logger.warning(
                        "Failed to start MCP %r for agent=%r session=%r: "
                        "%s, skipping.",
                        spec.name,
                        agent_id,
                        session_id,
                        e,
                    )
            # Declaration order: an MCP rebuilt after eviction must
            # not jump to the end.
            return [live[s.name] for s in specs if s.name in live]

    # ── for User: dynamic MCP management ───────────────────────────

    @abstractmethod
    async def add_mcp(
        self,
        mcp_client: MCPClient,
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Register a new MCP server for one agent/session and persist.

        Implementations record the config in :attr:`_mcp_specs`, start
        one live instance under :meth:`_enforce_mcp_capacity`, and call
        :meth:`_save_mcp_file`.

        Args:
            mcp_client (`MCPClient`):
                The MCP to register.
            agent_id (`str | None`, optional):
                The owning agent. ``None`` means the legacy ``""``.
                Keyword-only: both ids are plain strings, so passing
                them positionally could silently hit another session.
            session_id (`str | None`, optional):
                The owning session. ``None`` means the legacy ``""``.

        Raises:
            `ValueError`:
                If the name already exists for this agent/session.
        """

    @abstractmethod
    async def remove_mcp(
        self,
        name: str,
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Deregister an MCP by name within one agent/session.

        Other agents and sessions keep their own declarations and
        instances, so there is nothing to reconcile across them.

        Args:
            name (`str`):
                MCP name to remove. Unknown names log a warning and
                return silently.
            agent_id (`str | None`, optional):
                The owning agent. ``None`` means the legacy ``""``.
                Keyword-only: both ids are plain strings, so passing
                them positionally could silently hit another session.
            session_id (`str | None`, optional):
                The owning session. ``None`` means the legacy ``""``.
        """

    # ── session teardown ───────────────────────────────────────────

    async def purge_session(
        self,
        *,
        agent_id: str,
        session_id: str,
    ) -> None:
        """Drop everything this workspace holds for one session.

        Closes the session's MCP instances, forgets its ``.mcp``
        declaration and deletes its offload directory. Removing every
        MCP one by one is not equivalent: an emptied session persists
        as ``[]``, which is a meaningful state, and would keep dead
        sessions in ``.mcp`` forever.

        Best-effort — failures are logged, never raised, so a purge
        cannot block session deletion.

        Args:
            agent_id (`str`):
                The agent that owned the session.
            session_id (`str`):
                The session being deleted.
        """
        async with self._mcp_lock:
            dropped = self._mcp_instances.pop((agent_id, session_id), {})
            for instance in list(dropped.values()):
                await self._close_mcp_instance(instance)
            self._mcp_last_used.pop((agent_id, session_id), None)
            forgotten = self._mcp_specs.pop((agent_id, session_id), None)
            if forgotten is not None:
                await self._save_mcp_file()

        if self._backend is None or not session_id:
            return
        try:
            await self._backend.delete_path(
                self._backend.join_path(self._sessions_dir, session_id),
            )
        except Exception as e:
            logger.warning(
                "Failed to delete offload directory for session %r: %s",
                session_id,
                e,
            )

    async def purge_agent(self, *, agent_id: str) -> None:
        """Drop everything this workspace holds for one agent.

        Deletes the agent's skill partition. MCP declarations are
        keyed by session and cleared by :meth:`purge_session` as those
        sessions are deleted.

        Best-effort — failures are logged, never raised, so a purge
        cannot block agent deletion.

        Args:
            agent_id (`str`):
                The agent being deleted.
        """
        if self._backend is None or not agent_id:
            return
        try:
            partition = self._skill_partition(agent_id)
            self._equipped_partitions.discard(partition)
            await self._backend.delete_path(partition)
        except Exception as e:
            logger.warning(
                "Failed to delete the skill partition of agent %r: %s",
                agent_id,
                e,
            )

    # ── instance lifecycle ─────────────────────────────────────────

    @staticmethod
    async def _close_mcp_instance(instance: MCPClient) -> None:
        """Close one live handle, downgrading failures to warnings.

        Stateless clients hold no connection, so they are skipped.
        """
        if not (instance.is_stateful and instance.is_connected):
            return
        try:
            await instance.close()
        except Exception as e:
            logger.warning("MCP %r close failed: %s", instance.name, e)

    async def _enforce_mcp_capacity(
        self,
        agent_id: str,
        session_id: str,
        incoming: MCPClient,
    ) -> None:
        """Make room for ``incoming`` under
        :attr:`max_live_stateful_mcps`.

        Only stateful instances count — a stateless client holds no
        connection or subprocess, so capping them reclaims nothing.
        Eviction never targets the requesting agent/session, which
        therefore always gets its full set no matter how the cap is
        configured; when every live instance belongs to it the cap is
        exceeded instead.

        Callers must hold :attr:`_mcp_lock`.
        """
        if not incoming.is_stateful:
            return
        while True:
            live_count = sum(
                1
                for by_name in self._mcp_instances.values()
                for c in by_name.values()
                if c.is_stateful
            )
            if live_count < self.max_live_stateful_mcps:
                return

            victim_agent, victim_session, oldest = None, None, 0.0
            for (
                other_agent,
                other_session,
            ), by_name in self._mcp_instances.items():
                if other_agent == agent_id and other_session == session_id:
                    continue
                if not any(c.is_stateful for c in by_name.values()):
                    continue
                used = self._mcp_last_used.get(
                    (other_agent, other_session),
                    0.0,
                )
                if victim_agent is None or used < oldest:
                    victim_agent = other_agent
                    victim_session = other_session
                    oldest = used

            if victim_agent is None or victim_session is None:
                logger.warning(
                    "All %d live stateful MCPs belong to agent=%r "
                    "session=%r; exceeding max_live_stateful_mcps to "
                    "start %r.",
                    live_count,
                    agent_id,
                    session_id,
                    incoming.name,
                )
                return

            by_name = self._mcp_instances[(victim_agent, victim_session)]
            name = next(n for n, c in by_name.items() if c.is_stateful)
            logger.info(
                "Evicting idle MCP %r (agent=%r session=%r) to stay "
                "under max_live_stateful_mcps=%d.",
                name,
                victim_agent,
                victim_session,
                self.max_live_stateful_mcps,
            )
            await self._close_mcp_instance(by_name.pop(name))

    async def _close_all_mcp_instances(self) -> None:
        """Close every live handle and drop the instance layer.

        Declarations survive — a later :meth:`list_mcps` rebuilds.
        """
        for by_name in self._mcp_instances.values():
            for instance in list(by_name.values()):
                await self._close_mcp_instance(instance)
        self._mcp_instances.clear()
        self._mcp_last_used.clear()

    # ── MCP persistence (shared) ───────────────────────────────────

    async def _save_mcp_file(self) -> None:
        """Persist :attr:`_mcp_specs` to ``${workdir}/.mcp``.

        Only declarations are written, and only for sessions that
        diverged from :attr:`default_mcps`, so the file does not grow
        with session count on its own. No-op when
        :attr:`is_persistent` is ``False``; failures are logged, not
        raised — the in-memory copy stays authoritative.

        Callers are expected to hold :attr:`_mcp_lock` already.
        """
        if not self.is_persistent:
            return
        backend = self._backend
        if backend is None:
            return
        # Nested {agent_id: {session_id: [...]}} on disk rather than a
        # joined key, so no separator can collide with an id.
        mcps: dict[str, dict[str, list[dict]]] = {}
        for (agent_id, session_id), specs in self._mcp_specs.items():
            mcps.setdefault(agent_id, {})[session_id] = [
                m.model_dump(mode="json") for m in specs
            ]
        payload = json.dumps(
            {"version": MCP_FILE_VERSION, "mcps": mcps},
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

    async def _restore_mcp_specs(
        self,
    ) -> dict[tuple[str, str], list[MCPClient]]:
        """Read the declarations persisted in ``${workdir}/.mcp``.

        Returns an empty mapping when the file is absent or unusable —
        every agent/session then falls back to :attr:`default_mcps`, so
        a corrupt file cannot block startup. Version 1 files (a bare
        list) are read under the legacy empty ids; the next write emits
        v2.
        """
        if not self.is_persistent:
            return {}
        backend = self._backend
        if backend is None:
            return {}
        try:
            if not await backend.file_exists(self._mcp_file):
                return {}
            raw = await backend.read_file(self._mcp_file)
            data = json.loads(raw.decode("utf-8"))
        except Exception as e:
            logger.warning(
                "Failed to read MCP file at %s, falling back to "
                "default_mcps: %s",
                self._mcp_file,
                e,
            )
            return {}

        def _parse(
            cfgs: list,
            agent_id: str,
            session_id: str,
        ) -> list[MCPClient]:
            """Parse serialised configs, skipping invalid entries."""
            parsed: list[MCPClient] = []
            for m in cfgs:
                try:
                    parsed.append(MCPClient.model_validate(m))
                except Exception as e:
                    name = m.get("name", "?") if isinstance(m, dict) else "?"
                    logger.warning(
                        "Skipping invalid MCP entry %r for agent=%r "
                        "session=%r: %s",
                        name,
                        agent_id,
                        session_id,
                        e,
                    )
            return parsed

        if isinstance(data, list):
            logger.info("Migrating .mcp v1 (flat list) to the legacy ids.")
            return {("", ""): _parse(data, "", "")}

        if not isinstance(data, dict):
            logger.warning(
                "%s is neither a list nor an object; ignoring it.",
                self._mcp_file,
            )
            return {}

        specs: dict[tuple[str, str], list[MCPClient]] = {}
        for agent_id, by_session in (data.get("mcps") or {}).items():
            if not isinstance(by_session, dict):
                continue
            for session_id, cfgs in by_session.items():
                if isinstance(cfgs, list):
                    specs[(agent_id, session_id)] = _parse(
                        cfgs,
                        agent_id,
                        session_id,
                    )
        return specs

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
                        block = await self.offload_data_block(block)
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
                        d = await self.offload_data_block(block)
                        url = str(d.source.url)
                    else:
                        url = str(block.source.url)
                    parts.append(
                        f"<data url='{url}' name='{block.name}' "
                        f"media_type='{block.source.media_type}'/>",
                    )

        await backend.write_file(path, "".join(parts).encode("utf-8"))
        return path

    async def offload_data_block(self, block: DataBlock) -> DataBlock:
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
                A new :class:`DataBlock` whose source is a portable
                ``workspace://`` URL pointing at the persisted file
                inside the workspace.
        """
        if not isinstance(block.source, Base64Source):
            return block

        backend = self.get_backend()
        hash_str = hashlib.sha256(block.source.data.encode()).hexdigest()
        ext = mimetypes.guess_extension(block.source.media_type) or ".bin"
        rel = f"{DEFAULT_DATA_DIR}/{hash_str}{ext}"
        path = backend.join_path(self._data_dir, f"{hash_str}{ext}")

        if not await backend.file_exists(path):
            await backend.write_file(
                path,
                base64.b64decode(block.source.data),
            )

        # A ``workspace://`` reference (workspace-relative path). Unlike a
        # node-local ``file://`` path it is portable: a serving endpoint
        # resolves the actual workspace from the request's session
        # context and reads the relative path. See docs TODO (frontend
        # ``workspace://`` rendering).
        return DataBlock(
            id=block.id,
            name=block.name,
            source=URLSource(
                url=AnyUrl(f"workspace:///{rel}"),
                media_type=block.source.media_type,
            ),
        )

    # ── skill management (shared, simple) ──────────────────────────

    async def list_skills(
        self,
        *,
        agent_id: str | None = None,
    ) -> list[Skill]:
        """Enumerate the skills one agent can use.

        Reads the agent's own partition — equipping it from the seed
        template if this is the agent's first appearance — parses
        every ``skills/<agent_id>/<dir>/SKILL.md``'s YAML front
        matter, and yields one :class:`Skill` per file that has both
        ``name`` and ``description``.

        Subclasses with richer indexing (e.g.
        :class:`LocalWorkspace` with its ``.index`` hash index)
        override this method.

        Args:
            agent_id (`str | None`, optional):
                The agent asking. ``None`` reads the default
                partition, which is where an SDK caller driving the
                workspace without agents puts everything.

        Returns:
            `list[Skill]`:
                Skills available to the agent. Empty when the
                partition holds no parseable ``SKILL.md``.
        """
        import frontmatter as fm

        backend = self.get_backend()
        partition = await self._equip_partition(agent_id)

        skills: list[Skill] = []
        entries = await backend.list_dir(partition, recursive=True)
        for md_path in entries:
            skill_dir = backend.dirname(md_path)
            # Exactly ``<partition>/<dir>/SKILL.md`` — a SKILL.md a
            # skill ships in a subfolder is not a second skill.
            if (
                backend.basename(md_path) != "SKILL.md"
                or backend.dirname(skill_dir) != partition
            ):
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
                        dir=skill_dir,
                        markdown=doc.content or "",
                        updated_at=0.0,
                    ),
                )
            except Exception as e:
                logger.warning("Failed to load skill %s: %s", md_path, e)
        return skills

    async def add_skill(
        self,
        skill_path: str,
        *,
        agent_id: str | None = None,
    ) -> None:
        """Copy a local skill directory into an agent's partition.

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
            agent_id (`str | None`, optional):
                The agent taking ownership. ``None`` installs into the
                default partition.

        Raises:
            ValueError:
                If ``SKILL.md`` is missing or a directory with the
                same basename already exists in the partition.
            RuntimeError:
                If extraction inside the sandbox fails.
        """
        skill_md = os.path.join(skill_path, "SKILL.md")
        if not os.path.isfile(skill_md):
            raise ValueError(
                f"Invalid skill at {skill_path!r}: SKILL.md not found",
            )

        backend = self.get_backend()
        partition = await self._equip_partition(agent_id)

        async with self._skill_lock:
            dir_name = os.path.basename(os.path.abspath(skill_path))
            remote_dir = backend.join_path(partition, dir_name)

            if await backend.file_exists(remote_dir):
                raise ValueError(
                    f"Skill directory {dir_name!r} already exists in "
                    f"{partition}",
                )

            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tf:
                tf.add(skill_path, arcname=dir_name)
            tar_bytes = buf.getvalue()

            tmp_path = f"/tmp/skill-{_generate_id()}.tar"
            await backend.write_file(tmp_path, tar_bytes)

            await backend.exec_shell(
                ["mkdir", "-p", partition],
            )
            result = await backend.exec_shell(
                [
                    self._python_command,
                    "-c",
                    _EXTRACT_TAR_SHIM,
                    tmp_path,
                    partition,
                ],
            )
            if not result.ok():
                raise RuntimeError(
                    f"Failed to extract skill {dir_name!r}: "
                    f"{result.stderr.decode('utf-8', 'replace')}",
                )

            logger.info("Added skill %r at %s", dir_name, remote_dir)

    async def add_skill_archive(
        self,
        stream: AsyncIterator[bytes],
        fmt: Literal["zip", "tar", "tar.gz"],
        dir_name: str,
        max_extracted_bytes: int = DEFAULT_MAX_EXTRACTED_BYTES,
        *,
        agent_id: str | None = None,
    ) -> None:
        """Install a skill from an archive stream into a partition.

        The archive is piped straight into the sandbox and expanded
        there, so the server never holds it whole. Expansion lands in a
        staging directory that is renamed into place only once it is
        known to hold a ``SKILL.md`` — a failed install leaves nothing
        behind.

        Args:
            stream (`AsyncIterator[bytes]`):
                The archive bytes, in order.
            fmt (`Literal["zip", "tar", "tar.gz"]`):
                The archive format.
            dir_name (`str`):
                Directory name to install as. A numeric suffix is
                appended when it is taken.
            max_extracted_bytes (`int`):
                Ceiling on the archive's expanded size.
            agent_id (`str | None`, optional):
                The agent taking ownership. ``None`` installs into the
                default partition.

        Raises:
            ValueError:
                If ``dir_name`` is not a plain name, or the archive
                holds no ``SKILL.md``.
            RuntimeError:
                If expanding the archive inside the sandbox fails.
        """
        if dir_name in (".", "..") or os.path.basename(dir_name) != dir_name:
            raise ValueError(
                f"Skill directory name {dir_name!r} must be a plain name "
                f"without path separators.",
            )

        backend = self.get_backend()
        partition = await self._equip_partition(agent_id)
        suffix = "tar.gz" if fmt == "tar.gz" else fmt
        archive_path = f"/tmp/skill-{_generate_id()}.{suffix}"

        async with self._skill_lock:
            await backend.exec_shell(["mkdir", "-p", partition])
            # Staged beside ``skills/`` rather than inside it: the same
            # filesystem keeps the final rename atomic, while
            # ``list_skills`` never sees a half-expanded archive.
            staging = backend.join_path(
                self.workdir,
                f".skill-staging-{_generate_id()}",
            )
            await backend.write_stream(archive_path, stream)
            try:
                result = await backend.exec_shell(
                    [
                        self._python_command,
                        "-c",
                        _EXTRACT_ARCHIVE_SHIM,
                        archive_path,
                        staging,
                        fmt,
                        str(max_extracted_bytes),
                    ],
                )
                if not result.ok():
                    raise RuntimeError(
                        f"Failed to expand skill archive: "
                        f"{result.stderr.decode('utf-8', 'replace')}",
                    )

                root = await self._find_skill_root(staging)
                installed = await self._free_skill_dir(dir_name, partition)
                move = await backend.exec_shell(
                    [
                        "mv",
                        root,
                        backend.join_path(partition, installed),
                    ],
                )
                if not move.ok():
                    raise RuntimeError(
                        f"Failed to install skill {installed!r}: "
                        f"{move.stderr.decode('utf-8', 'replace')}",
                    )
            finally:
                await backend.delete_path(staging)
                await backend.delete_path(archive_path)

        logger.info("Added skill %r to %s", installed, partition)

    async def _find_skill_root(self, staging: str) -> str:
        """Return the directory under ``staging`` holding ``SKILL.md``.

        Archives arrive flat or wrapped in a single top-level folder;
        both are accepted.

        Args:
            staging (`str`):
                The directory the archive was expanded into.

        Returns:
            `str`:
                The path of the directory containing ``SKILL.md``.

        Raises:
            ValueError:
                If no ``SKILL.md`` is found.
        """
        backend = self.get_backend()
        if await backend.file_exists(backend.join_path(staging, "SKILL.md")):
            return staging

        for entry in await backend.list_dir(staging):
            candidate = backend.join_path(staging, entry)
            if await backend.file_exists(
                backend.join_path(candidate, "SKILL.md"),
            ):
                return candidate

        raise ValueError("The skill archive contains no SKILL.md")

    async def _free_skill_dir(self, dir_name: str, partition: str) -> str:
        """Return ``dir_name``, suffixed until it is unused.

        Args:
            dir_name (`str`):
                The preferred directory name.
            partition (`str`):
                The partition directory the name must be free in.

        Returns:
            `str`:
                A directory name not present in ``partition``.
        """
        backend = self.get_backend()
        candidate, counter = dir_name, 1
        while await backend.file_exists(
            backend.join_path(partition, candidate),
        ):
            candidate = f"{dir_name}-{counter}"
            counter += 1
        return candidate

    async def remove_skill(
        self,
        name: str,
        *,
        agent_id: str | None = None,
    ) -> None:
        """Remove a skill by its agent-facing ``name`` (front matter).

        Looks up the skill via :meth:`list_skills` and ``rm -rf``-style
        deletes its directory through the backend. An agent can remove
        a shared skill: the partitions bound who *owns* a skill, not
        who may delete one.

        Args:
            name (`str`):
                The agent-facing name of the skill to remove.
            agent_id (`str | None`, optional):
                The agent asking. ``None`` searches every partition.

        Raises:
            KeyError:
                If the skill is not found in the workspace.
        """
        backend = self.get_backend()
        skills = await self.list_skills(agent_id=agent_id)
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

    async def _migrate_skill_layout(self) -> None:
        """Move a pre-partition ``skills/`` into the seed template.

        Runs once per :meth:`initialize`; idempotent, since a migrated
        ``skills/`` holds nothing but partition directories. Failures
        are logged, not raised.
        """
        backend = self._backend
        if backend is None:
            return
        try:
            result = await backend.exec_shell(
                [
                    self._python_command,
                    "-c",
                    _MIGRATE_SKILLS_SHIM,
                    self._skills_dir,
                    SKILL_SEED_DIR,
                ],
            )
            moved = result.stdout.decode("utf-8", "replace").strip()
            if result.ok() and moved:
                logger.info(
                    "Moved %s pre-partition skill entries into %s/.",
                    moved,
                    SKILL_SEED_DIR,
                )
            elif not result.ok():
                logger.warning(
                    "Failed to partition %s: %s",
                    self._skills_dir,
                    result.stderr.decode("utf-8", "replace"),
                )
        except Exception as e:
            logger.warning("Failed to partition %s: %s", self._skills_dir, e)

    async def _setup_skills(self) -> None:
        """Copy :attr:`skill_paths` into the seed template once.

        Seeds carry no owning agent, so they are not installed
        anywhere: they become the template each agent's partition is
        equipped from on first sight. One archive covers all of them,
        so a sandbox takes the upload once rather than once per agent.
        Skips seeding when:

        - :attr:`skill_paths` is empty;
        - the backend is not bound; or
        - the template already contains entries (assume the prior run,
          or the user, is the source of truth).

        A skill without ``SKILL.md`` is logged and skipped — one bad
        path cannot block startup.
        """
        if not self.skill_paths:
            return
        backend = self._backend
        if backend is None:
            return
        seed = self._skill_seed_dir
        if await backend.is_dir(seed) and await backend.list_dir(seed):
            return

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for path in self.skill_paths:
                if not os.path.isfile(os.path.join(path, "SKILL.md")):
                    logger.warning("Skip skill %r: SKILL.md not found", path)
                    continue
                tf.add(path, arcname=os.path.basename(os.path.abspath(path)))
        tar_bytes = buf.getvalue()

        tmp_path = f"/tmp/skill-seed-{_generate_id()}.tar"
        await backend.write_file(tmp_path, tar_bytes)
        result = await backend.exec_shell(
            [self._python_command, "-c", _EXTRACT_TAR_SHIM, tmp_path, seed],
        )
        if not result.ok():
            logger.warning(
                "Failed to seed %s: %s",
                seed,
                result.stderr.decode("utf-8", "replace"),
            )
