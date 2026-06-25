# -*- coding: utf-8 -*-
"""DaytonaWorkspace — sandboxed workspace backed by Daytona.

Architecture
------------

Mirrors :class:`agentscope.workspace.E2BWorkspace` and
:class:`agentscope.workspace.DockerWorkspace` at the AgentScope boundary,
but swaps the provider runtime for the Daytona SDK:

* **Lifecycle.** ``initialize()`` looks up a sandbox by the
  ``agentscope.workspace.id`` label, starts / recovers an existing
  candidate when possible, or creates a new sandbox and runs the
  bootstrap shell sequence. ``close()`` calls Daytona ``stop`` with
  ``force=False`` so provider-level graceful stop semantics are used.
* **Persistence.** Sandbox filesystem state is the persistence layer.
  ``.mcp``, ``skills/``, ``sessions/`` and ``data/`` live in the
  SDK-reported workdir and are restored after reattachment.
* **Bootstrap.** First-time provisioning installs ripgrep, uv, the
  gateway virtualenv, AgentScope itself, the gateway script and the
  glob helper. Bootstrap is detected by probing the gateway script path
  inside the sandbox and is safe to rerun when a previous attempt was
  interrupted.
* **MCP gateway.** Same AgentScope gateway model as Docker/E2B: a
  FastAPI process runs inside the sandbox while the host talks to it
  through Daytona's preview URL.
* **Preview authentication.** AgentScope gateway bearer auth remains
  separate from Daytona preview proxy auth. The gateway gets a fresh
  bearer token on every ``initialize()``; Daytona's preview token is
  forwarded as ``x-daytona-preview-token`` when the SDK returns one.
* **SDK paths.** The real workdir and user home are read from Daytona
  before deriving any AgentScope paths. The implementation does not
  assume a fixed OS user, root home, or ``/home/daytona`` layout.

Configuration is per-workspace. The manager handles cache and TTL
eviction; each workspace owns its own ``AsyncDaytona`` client and
runtime sandbox handle.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import posixpath
import shlex
import uuid
from copy import deepcopy
from typing import Any, AsyncIterator

from pydantic import AnyUrl

from ..._logging import logger
from ...mcp import MCPClient
from ...message import (
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    ToolResultBlock,
    URLSource,
)
from ...skill import Skill
from ...tool import ToolBase
from .._base import WorkspaceBase
from .._gateway_client import GatewayClient, GatewayMCPClient
from .._utils import (
    _agentscope_version,
    _is_released_install,
    _read_gateway_script_bytes,
    _read_glob_helper_bytes,
)
from ._bootstrap import (
    DATA_DIR_NAME,
    DAYTONA_PREVIEW_TOKEN_HEADER,
    DEFAULT_GATEWAY_PORT,
    DEFAULT_TIMEOUT,
    DEV_SRC_DIR_NAME,
    DEV_SRC_TAR_NAME,
    GATEWAY_CONFIG_NAME,
    GATEWAY_HOME_NAME,
    GATEWAY_LOG_NAME,
    GATEWAY_SCRIPT_NAME,
    GATEWAY_VENV_NAME,
    GLOB_HELPER_NAME,
    MCP_FILE_NAME,
    METADATA_WORKSPACE_ID_KEY,
    SESSIONS_DIR_NAME,
    SKILLS_DIR_NAME,
    bootstrap_commands,
    build_source_tarball,
    log_bootstrap_attempt,
    render_install_agentscope_cmd_dev,
    render_install_agentscope_cmd_released,
)
from ._daytona_backend import DaytonaBackend

_DEFAULT_INSTRUCTIONS = """<workspace>
You have a Daytona-based sandbox workspace. All tool calls execute
inside the sandbox at ``{workdir}``.

Layout:

```
{workdir}
├── data/        # offloaded multimodal files
├── skills/      # reusable skills
└── sessions/    # session context and tool results
```

Use the MCP-provided tools to interact with the sandbox filesystem
and processes.
</workspace>"""


# ── the workspace ──────────────────────────────────────────────────


class DaytonaWorkspace(WorkspaceBase):
    """Workspace backed by a Daytona sandbox.

    ``default_mcps`` and ``skill_paths`` are seed-time inputs. They are
    used only when a brand-new workspace has no persisted ``.mcp`` file
    or empty ``skills/`` directory; reattached workspaces prefer the
    sandbox filesystem state.
    """

    def __init__(
        self,
        *,
        workspace_id: str | None = None,
        api_key: str = "",
        api_url: str = "",
        target: str = "",
        timeout_seconds: int = DEFAULT_TIMEOUT,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        env: dict[str, str] | None = None,
        sandbox_metadata: dict[str, str] | None = None,
        extra_pip: list[str] | None = None,
        instructions: str = _DEFAULT_INSTRUCTIONS,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        os_user: str | None = None,
    ) -> None:
        """Construct a :class:`DaytonaWorkspace`.

        The sandbox is *not* started here; call :meth:`initialize`
        (or use the workspace as an ``async`` context manager).

        Args:
            workspace_id (`str | None`, optional):
                Stable identifier; doubles as the
                ``agentscope.workspace.id`` label used for later
                reattachment. ``None`` generates a fresh UUID.
            api_key (`str`, defaults to `""`):
                Daytona API key. ``""`` lets the Daytona SDK read
                credentials from its environment.
            api_url (`str`, defaults to `""`):
                Optional Daytona API URL for self-hosted or non-default
                deployments. Empty string defers to SDK defaults.
            target (`str`, defaults to `""`):
                Optional Daytona target / region. Empty string defers
                to SDK defaults.
            timeout_seconds (`int`, defaults to `DEFAULT_TIMEOUT`):
                Timeout forwarded to Daytona create/start/recover/stop
                calls.
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the in-sandbox gateway listens on.
            env (`dict[str, str] | None`, optional):
                Environment variables passed to Daytona create params.
            sandbox_metadata (`dict[str, str] | None`, optional):
                Extra labels merged with
                ``{METADATA_WORKSPACE_ID_KEY: workspace_id}``. Useful
                for dashboard filtering by user / agent.
            extra_pip (`list[str] | None`, optional):
                Extra Python packages installed into the gateway venv
                during bootstrap.
            instructions (`str`, defaults to `_DEFAULT_INSTRUCTIONS`):
                System-prompt fragment template returned by
                :meth:`get_instructions`.
            default_mcps (`list[MCPClient] | None`, optional):
                MCPs seeded on first initialization when no persisted
                ``.mcp`` file exists.
            skill_paths (`list[str] | None`, optional):
                Local skill directories copied into ``skills/`` when a
                new sandbox has no skills yet.
            os_user (`str | None`, optional):
                Optional Daytona OS user. ``None`` means AgentScope
                does not choose a user and lets Daytona / snapshot
                defaults decide.
        """
        super().__init__(workspace_id=workspace_id)

        # ── SDK-derived paths ───────────────────────────────────
        self.workdir = ""
        self._user_home = ""

        # ── serializable config ─────────────────────────────────
        self.api_key = api_key
        self.api_url = api_url
        self.target = target
        self.timeout_seconds = timeout_seconds
        self.gateway_port = gateway_port
        self.env: dict[str, str] = dict(env or {})
        self.sandbox_metadata: dict[str, str] = dict(sandbox_metadata or {})
        self.extra_pip: list[str] = list(extra_pip or [])
        self.instructions = instructions
        self.os_user = os_user

        # ── seed-only ───────────────────────────────────────────
        self.default_mcps: list[MCPClient] = list(default_mcps or [])
        self.skill_paths: list[str] = list(skill_paths or [])

        # ── runtime state ───────────────────────────────────────
        self._daytona: Any = None
        self._sandbox: Any = None
        self._backend: DaytonaBackend | None = None
        self._gateway: GatewayClient | None = None
        self._gateway_token: str = ""
        self._mcps: list[MCPClient] = []
        self._gateway_clients: dict[str, GatewayMCPClient] = {}
        self._mcp_lock = asyncio.Lock()
        self._skill_lock = asyncio.Lock()

    # ── lifecycle ───────────────────────────────────────────────

    @property
    def sandbox_id(self) -> str | None:
        """Daytona sandbox id, or ``None`` if not started."""
        return _sandbox_id(self._sandbox) if self._sandbox is not None else None

    async def initialize(self) -> None:
        """Reattach or create the sandbox, then start the gateway.

        Steps:

        1. Find an existing sandbox with the workspace label. Reuse it
           when it is started, stopped, paused, starting, stopping,
           resuming, or recoverable-error.
        2. Create a new sandbox with minimal Daytona params when no
           usable candidate exists.
        3. Derive every AgentScope path from Daytona SDK path APIs.
        4. Run bootstrap only when the gateway script is missing.
        5. Restore MCPs from ``$workdir/.mcp`` if present, otherwise
           seed from ``default_mcps``.
        6. Mint a fresh gateway bearer token, stop any stale gateway
           process, write gateway config, launch the gateway and wait
           for health.
        7. Build host-side :class:`GatewayMCPClient` handles and seed
           skills when configured.

        Idempotent — a no-op when already alive.
        """
        if self.is_alive:
            return

        await self._attach_or_create_sandbox()
        await self._derive_sdk_paths()
        self._backend = DaytonaBackend(self._sandbox, workdir=self.workdir)

        # Missing gateway script means a fresh sandbox or an
        # interrupted previous bootstrap. Commands are idempotent
        # enough to retry.
        if not await self._backend.file_exists(self._gateway_script):
            await self._backend.exec_shell(["mkdir", "-p", self.workdir])
            await self._run_bootstrap()

        self._mcps = await self._restore_or_seed_mcps()
        self._gateway_token = uuid.uuid4().hex

        # Stop any stale gateway from a previous start / reattach
        # cycle. Each init mints a fresh bearer token, so an old
        # process on the port would reject new-token requests.
        await self._backend.exec_shell(
            ["sh", "-c", "pkill -f _mcp_gateway_app.py || true"],
        )
        await self._write_gateway_config()
        await self._start_gateway_process()

        preview = await self._sandbox.get_preview_link(self.gateway_port)
        self._gateway = GatewayClient(
            base_url=_preview_url(preview),
            token=self._gateway_token,
            timeout=30.0,
            extra_headers=self._sandbox_proxy_headers(preview),
        )
        await self._wait_for_gateway()

        self._gateway_clients = {
            c.name: c for c in await self._gateway.list_mcps()
        }

        # Persist the MCP set unconditionally so freshly seeded
        # ``default_mcps`` become the canonical ``.mcp`` for the next
        # reattach. ``_seed_skills`` is also idempotent and short
        # circuits when sandbox-side ``skills/`` already has entries.
        await self._save_mcp_file()
        await self._seed_skills()

        self.is_alive = True

    async def reset(self) -> None:
        """Return the workspace to an empty persistent state.

        Mirrors :meth:`DockerWorkspace.reset`: deregisters every MCP
        from the gateway, clears the local handles, and wipes
        ``.mcp``, ``skills/``, ``sessions/`` and ``data/`` inside the
        sandbox. The gateway process keeps running with no upstream
        MCPs. ``default_mcps`` / ``skill_paths`` are not re-seeded.
        """
        if self._backend is None:
            raise RuntimeError(
                "DaytonaWorkspace is not initialized: its sandbox backend "
                "is unavailable. Use 'async with workspace:' or call "
                "'await workspace.initialize()' before 'reset()'.",
            )

        async with self._mcp_lock, self._skill_lock:
            for gw_client in list(self._gateway_clients.values()):
                try:
                    await gw_client.close()
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "MCP %r close failed during reset: %s",
                        gw_client.name,
                        e,
                    )
            self._gateway_clients.clear()
            self._mcps = []

            for path in (
                self._sessions_dir,
                self._data_dir,
                self._skills_dir,
            ):
                await self._backend.delete_path(path)
            await self._save_mcp_file()

    async def close(self) -> None:
        """Gracefully stop the sandbox and release host-side resources.

        ``force=False`` preserves Daytona's non-force stop semantics.
        Filesystem persistence is left to the provider so the next
        :meth:`initialize` can reattach by label. The host-side gateway
        client is closed first so its connection pool is released
        cleanly.

        Errors during teardown are swallowed so ``close`` is always
        safe to call (for example from ``__aexit__``).
        """
        if self._gateway is not None:
            try:
                await self._gateway.aclose()
            except Exception:
                pass
            self._gateway = None
        self._gateway_clients.clear()

        if self._sandbox is not None:
            try:
                await self._sandbox.stop(timeout=self.timeout_seconds, force=False)
            except Exception as e:  # noqa: BLE001
                logger.warning("DaytonaWorkspace: stop failed: %s", e)
            self._sandbox = None
            self._backend = None

        if self._daytona is not None:
            try:
                await self._daytona.close()
            except Exception:
                pass
            self._daytona = None

        self.is_alive = False

    # ── instructions ────────────────────────────────────────────

    async def get_instructions(self) -> str:
        """Return the system-prompt fragment for this workspace.

        Substitutes ``{workdir}`` in the configured template with the
        sandbox-side workdir discovered from the Daytona SDK. The agent
        always sees sandbox-internal paths.
        """
        workdir = self.workdir or "<sandbox workdir>"
        return self.instructions.format(workdir=workdir)

    # ── tool / MCP / skill discovery ────────────────────────────

    async def list_tools(self) -> list[ToolBase]:
        """Built-in tools backed by the Daytona sandbox.

        Returns the six builtin tools (Bash, Read, Write, Edit, Grep,
        Glob), each backed by the workspace's :class:`DaytonaBackend`
        that executes inside the sandbox.

        Raises:
            `RuntimeError`:
                If the workspace has not been initialized yet (the
                sandbox-backed backend is unavailable). Without this
                guard the builtin tools would silently fall back to a
                :class:`LocalBackend` and run on the host instead of
                inside the sandbox.
        """
        if self._backend is None:
            raise RuntimeError(
                "DaytonaWorkspace is not initialized: its sandbox backend "
                "is unavailable. Use 'async with workspace:' or call "
                "'await workspace.initialize()' before 'list_tools()'.",
            )

        from ...tool._builtin import Bash, Edit, Glob, Grep, Read, Write

        return [
            Bash(cwd=self.workdir, backend=self._backend),
            Edit(backend=self._backend),
            Glob(backend=self._backend, glob_helper_path=self._glob_helper_script),
            Grep(backend=self._backend),
            Read(backend=self._backend),
            Write(backend=self._backend),
        ]

    async def list_mcps(self) -> list[MCPClient]:
        """Return one :class:`GatewayMCPClient` per registered MCP.

        Each entry's ``name`` matches the upstream MCP server name and
        all of its protocol calls are routed over the Daytona preview
        URL to the in-sandbox gateway.
        """
        return list(self._gateway_clients.values())

    async def list_skills(self) -> list[Skill]:
        """Enumerate skills by scanning ``skills/`` inside the sandbox.

        Reads each ``SKILL.md`` via :class:`DaytonaBackend` and parses
        the YAML front-matter. Files missing ``name`` or
        ``description`` are skipped.
        """
        import frontmatter as fm

        result = await self._backend.exec_shell(
            [
                "sh",
                "-c",
                f"find {shlex.quote(self._skills_dir)} -name SKILL.md "
                "2>/dev/null || true",
            ],
        )
        if not result.ok():
            return []
        listing = result.stdout.decode(errors="replace").strip()
        if not listing:
            return []

        skills: list[Skill] = []
        for md_path in (line.strip() for line in listing.split("\n")):
            if not md_path:
                continue
            try:
                raw = await self._backend.read_file(md_path)
                doc = fm.loads(raw.decode("utf-8"))
                name = doc.get("name")
                desc = doc.get("description")
                if not name or not desc:
                    continue
                skills.append(
                    Skill(
                        name=str(name),
                        description=str(desc),
                        dir=posixpath.dirname(md_path),
                        markdown=doc.content or "",
                        updated_at=0.0,
                    ),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to load skill %s: %s", md_path, e)
        return skills

    # ── dynamic MCP management ──────────────────────────────────

    async def add_mcp(self, mcp_client: MCPClient) -> None:
        """Register a new MCP server on the in-sandbox gateway.

        Mirrors :meth:`DockerWorkspace.add_mcp` but persists ``.mcp``
        unconditionally — the sandbox filesystem is always persistent
        for Daytona.
        """
        async with self._mcp_lock:
            if mcp_client.name in self._gateway_clients:
                raise ValueError(
                    f"MCP {mcp_client.name!r} already exists in workspace.",
                )
            spec = mcp_client.model_dump(mode="json")
            assert self._gateway is not None
            gw_client = self._gateway.make_client(spec)
            await gw_client.connect()
            self._mcps.append(mcp_client)
            self._gateway_clients[gw_client.name] = gw_client
            await self._save_mcp_file()

    async def remove_mcp(self, name: str) -> None:
        """Unregister an MCP server by name.

        Mirrors :meth:`DockerWorkspace.remove_mcp`.
        """
        async with self._mcp_lock:
            gw_client = self._gateway_clients.pop(name, None)
            if gw_client is None:
                logger.warning("MCP %r not found in workspace", name)
                return
            try:
                await gw_client.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("MCP %r close failed: %s", name, e)
            self._mcps = [m for m in self._mcps if m.name != name]
            await self._save_mcp_file()

    # ── dynamic skill management ────────────────────────────────

    async def add_skill(self, skill_path: str) -> None:
        """Upload a local skill directory into sandbox ``skills/``.

        The directory must contain a ``SKILL.md`` with ``name`` and
        ``description`` in its YAML front matter. A directory of the
        same basename already in the sandbox is rejected rather than
        overwritten.
        """
        skill_md = os.path.join(skill_path, "SKILL.md")
        if not os.path.isfile(skill_md):
            raise ValueError(
                f"Invalid skill at {skill_path!r}: SKILL.md not found",
            )

        async with self._skill_lock:
            await self._backend.exec_shell(["mkdir", "-p", self._skills_dir])
            dir_name = os.path.basename(os.path.abspath(skill_path))

            check = await self._backend.exec_shell(
                ["test", "-e", f"{self._skills_dir}/{dir_name}"],
            )
            if check.ok():
                raise ValueError(
                    f"Skill directory {dir_name!r} already exists in "
                    f"{self._skills_dir}",
                )

            for root, _dirs, files in os.walk(skill_path):
                for fname in files:
                    local = os.path.join(root, fname)
                    rel = os.path.relpath(local, skill_path)
                    remote = f"{self._skills_dir}/{dir_name}/{rel}"
                    with open(local, "rb") as f:
                        data = f.read()
                    await self._backend.write_file(remote, data)

    async def remove_skill(self, name: str) -> None:
        """Delete a skill directory by its agent-facing name."""
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
        await self._backend.delete_path(target_dir)

    # ── offload ─────────────────────────────────────────────────

    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
    ) -> str:
        """Persist a batch of messages as JSONL inside the sandbox.

        Same shape as :meth:`DockerWorkspace.offload_context`: each
        :class:`Msg` becomes a line; inline base64 :class:`DataBlock`
        payloads are extracted into ``data/`` and replaced with
        ``file://`` URL blocks.
        """
        base = f"{self._sessions_dir}/{session_id}"
        path = f"{base}/context.jsonl"

        copied = deepcopy(msgs)
        lines: list[str] = []
        for msg in copied:
            if not isinstance(msg.content, str):
                content = []
                for block in msg.content:
                    if isinstance(block, DataBlock) and isinstance(
                        block.source,
                        Base64Source,
                    ):
                        block = await self._offload_data_block(block)
                    content.append(block)
                msg.content = content
            lines.append(msg.model_dump_json())

        await self._backend.exec_shell(["mkdir", "-p", base])
        existing = b""
        try:
            existing = await self._backend.read_file(path)
        except FileNotFoundError:
            pass
        await self._backend.write_file(
            path,
            existing + ("\n".join(lines) + "\n").encode("utf-8"),
        )
        return path

    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
    ) -> str:
        """Persist a single tool result as a flat text file."""
        base = f"{self._sessions_dir}/{session_id}"
        path = f"{base}/tool_result-{tool_result.id}.txt"

        parts: list[str] = []
        if isinstance(tool_result.output, str):
            parts.append(tool_result.output)
        else:
            for block in tool_result.output:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
                elif isinstance(block, DataBlock):
                    if isinstance(block.source, Base64Source):
                        data_block = await self._offload_data_block(block)
                        url = str(data_block.source.url)
                    else:
                        url = str(block.source.url)
                    parts.append(
                        f"<data url='{url}' name='{block.name}' "
                        f"media_type='{block.source.media_type}'/>",
                    )

        await self._backend.exec_shell(["mkdir", "-p", base])
        await self._backend.write_file(path, "".join(parts).encode("utf-8"))
        return path

    # ── internals: Daytona client / sandbox attach / create ─────

    async def _get_daytona_client(self) -> Any:
        """Return a per-workspace ``AsyncDaytona`` client.

        Daytona SDK clients own network resources, so the workspace
        does not share a global client with the manager or with other
        workspaces. Empty config fields are omitted to let the SDK read
        its normal environment defaults.
        """
        if self._daytona is not None:
            return self._daytona

        from daytona import AsyncDaytona, DaytonaConfig

        config_kwargs: dict[str, str] = {}
        if self.api_key:
            config_kwargs["api_key"] = self.api_key
        if self.api_url:
            config_kwargs["api_url"] = self.api_url
        if self.target:
            config_kwargs["target"] = self.target

        if config_kwargs:
            self._daytona = AsyncDaytona(DaytonaConfig(**config_kwargs))
        else:
            self._daytona = AsyncDaytona()
        return self._daytona

    async def _attach_or_create_sandbox(self) -> None:
        """Reattach to an existing sandbox by label, or create one.

        Resolution rule: a single sandbox is expected per
        ``workspace_id``. If multiple usable candidates are returned
        (for example after an unclean shutdown), attach to the newest
        one by provider timestamps and log a warning — manual cleanup
        is left to the operator.

        Existing candidates are normalized to a ready state before the
        caller derives paths or starts the gateway. New sandboxes are
        created from the constructor config captured during
        ``__init__``.
        """
        existing = await self._find_existing_sandbox()
        client = await self._get_daytona_client()

        if existing is not None:
            self._sandbox = existing
            await self._ensure_existing_sandbox_ready()
            return

        params = self._create_params()
        self._sandbox = await client.create(params, timeout=self.timeout_seconds)

    async def _find_existing_sandbox(self) -> Any:
        """Find the most recent usable Daytona sandbox for this workspace.

        Daytona may return started, stopped, paused, transitional, or
        error-state sandboxes. Candidate filtering is intentionally
        conservative: unrecoverable errors are ignored, while stopped
        and paused sandboxes can be started again.

        Returns:
            The most recent usable Daytona sandbox object, or ``None``
            if no matching sandbox exists.
        """
        from daytona import ListSandboxesQuery, SandboxState

        client = await self._get_daytona_client()
        query = ListSandboxesQuery(
            labels={METADATA_WORKSPACE_ID_KEY: self.workspace_id},
            states=[
                SandboxState.STARTED,
                SandboxState.STOPPED,
                SandboxState.STARTING,
                SandboxState.STOPPING,
                SandboxState.ERROR,
                SandboxState.PAUSED,
                SandboxState.RESUMING,
            ],
        )

        candidates: list[Any] = []
        try:
            candidates = await _collect_sandboxes(client.list(query))
        except Exception as e:  # noqa: BLE001
            logger.warning("DaytonaWorkspace: list sandboxes failed: %s", e)
            return None

        usable = [
            sandbox
            for sandbox in candidates
            if self._is_candidate_usable(sandbox)
        ]
        if not usable:
            return None
        if len(usable) > 1:
            logger.warning(
                "DaytonaWorkspace: %d sandboxes match workspace_id=%r; "
                "attaching to most recent",
                len(usable),
                self.workspace_id,
            )
        usable.sort(key=_candidate_sort_key, reverse=True)
        return usable[0]

    def _is_candidate_usable(self, sandbox: Any) -> bool:
        """Return whether a listed sandbox can be attached.

        Recoverable error sandboxes can be recovered; unrecoverable
        error sandboxes are ignored so a fresh sandbox can be created.
        """
        state = _state_value(getattr(sandbox, "state", None))
        if state == "error":
            return bool(getattr(sandbox, "recoverable", False))
        return state in {
            "started",
            "stopped",
            "starting",
            "stopping",
            "paused",
            "resuming",
        }

    async def _ensure_existing_sandbox_ready(self) -> None:
        """Start or recover an existing sandbox when needed.

        Daytona exposes different state transitions for stopped,
        paused, stopping, starting, resuming and recoverable error
        sandboxes. This method normalizes those states into a running
        sandbox before bootstrap / gateway setup continues.
        """
        state = _state_value(getattr(self._sandbox, "state", None))
        if state == "error":
            await self._sandbox.recover(timeout=self.timeout_seconds)
        elif state in {"stopped", "paused"}:
            await self._sandbox.start(timeout=self.timeout_seconds)
        elif state == "stopping":
            wait = getattr(self._sandbox, "wait_for_sandbox_stop", None)
            if wait is not None:
                await wait(timeout=self.timeout_seconds)
            await self._sandbox.start(timeout=self.timeout_seconds)
        elif state in {"starting", "resuming"}:
            wait = getattr(self._sandbox, "wait_for_sandbox_start", None)
            if wait is not None:
                await wait(timeout=self.timeout_seconds)
            else:
                await self._sandbox.start(timeout=self.timeout_seconds)

        refresh = getattr(self._sandbox, "refresh_data", None)
        if refresh is not None:
            await refresh()

    def _create_params(self) -> Any:
        """Build Daytona create params from initialized workspace config.

        The first release keeps the config surface intentionally
        minimal and aligned with E2B. Snapshot/image/language/TTL are
        not exposed here; Daytona defaults apply. AgentScope sets
        ``public=False`` explicitly and uses its own manager TTL.
        """
        from daytona import CreateSandboxFromSnapshotParams

        labels = {
            METADATA_WORKSPACE_ID_KEY: self.workspace_id,
            **self.sandbox_metadata,
        }
        kwargs: dict[str, Any] = {
            "labels": labels,
            "public": False,
        }
        if self.env:
            kwargs["env_vars"] = self.env
        if self.os_user is not None:
            kwargs["os_user"] = self.os_user
        return CreateSandboxFromSnapshotParams(**kwargs)

    # ── internals: SDK paths / bootstrap ────────────────────────

    async def _derive_sdk_paths(self) -> None:
        """Derive all sandbox paths from Daytona SDK path APIs.

        Daytona docs show common ``/home/daytona`` examples, but the
        SDK is the source of truth. Every AgentScope path is derived
        from ``get_work_dir()`` or ``get_user_home_dir()`` so custom
        snapshots or OS users keep working.
        """
        self.workdir = await self._sandbox.get_work_dir()
        self._user_home = await self._sandbox.get_user_home_dir()

        self._data_dir = posixpath.join(self.workdir, DATA_DIR_NAME)
        self._skills_dir = posixpath.join(self.workdir, SKILLS_DIR_NAME)
        self._sessions_dir = posixpath.join(self.workdir, SESSIONS_DIR_NAME)
        self._mcp_file = posixpath.join(self.workdir, MCP_FILE_NAME)

        self._gateway_home = posixpath.join(
            self._user_home,
            GATEWAY_HOME_NAME,
        )
        self._gateway_venv = posixpath.join(
            self._gateway_home,
            GATEWAY_VENV_NAME,
        )
        self._gateway_venv_py = posixpath.join(
            self._gateway_venv,
            "bin",
            "python",
        )
        self._gateway_script = posixpath.join(
            self._gateway_home,
            GATEWAY_SCRIPT_NAME,
        )
        self._glob_helper_script = posixpath.join(
            self._gateway_home,
            GLOB_HELPER_NAME,
        )
        self._gateway_config = posixpath.join(
            self._gateway_home,
            GATEWAY_CONFIG_NAME,
        )
        self._gateway_log = posixpath.join(
            self._gateway_home,
            GATEWAY_LOG_NAME,
        )
        self._uv_bin = posixpath.join(self._user_home, ".local", "bin", "uv")
        self._dev_src_tar = posixpath.join(
            self._gateway_home,
            DEV_SRC_TAR_NAME,
        )
        self._dev_src_dir = posixpath.join(
            self._gateway_home,
            DEV_SRC_DIR_NAME,
        )

    async def _run_bootstrap(self) -> None:
        """Provision a fresh sandbox: tools → uv → venv → scripts.

        Released installs pin the host AgentScope version. Dev installs
        upload a tarball of the current source tree and install it into
        the gateway venv with ``--no-deps``.

        Each command runs through :class:`DaytonaBackend`; a non-zero
        exit raises :class:`RuntimeError` with the command, exit code,
        stderr and stdout so provider-side startup failures are visible
        in host logs.
        """
        if _is_released_install():
            log_bootstrap_attempt(self.workspace_id, "released")
            install_cmd = render_install_agentscope_cmd_released(
                uv_bin=self._uv_bin,
                gateway_venv_py=self._gateway_venv_py,
                version=_agentscope_version(),
            )
        else:
            log_bootstrap_attempt(self.workspace_id, "dev")
            tar_bytes = build_source_tarball()
            await self._backend.write_file(self._dev_src_tar, tar_bytes)
            install_cmd = render_install_agentscope_cmd_dev(
                uv_bin=self._uv_bin,
                gateway_venv_py=self._gateway_venv_py,
                dev_src_tar=self._dev_src_tar,
                dev_src_dir=self._dev_src_dir,
            )

        for cmd in bootstrap_commands(
            workdir=self.workdir,
            data_dir=self._data_dir,
            skills_dir=self._skills_dir,
            sessions_dir=self._sessions_dir,
            user_home=self._user_home,
            gateway_home=self._gateway_home,
            gateway_venv=self._gateway_venv,
            gateway_venv_py=self._gateway_venv_py,
            uv_bin=self._uv_bin,
            extra_pip=self.extra_pip,
            install_agentscope_cmd=install_cmd,
        ):
            result = await self._backend.exec_shell(
                ["sh", "-c", cmd],
                timeout=600,
            )
            if not result.ok():
                raise RuntimeError(
                    f"DaytonaWorkspace bootstrap failed "
                    f"(exit {result.exit_code}) for: {cmd!r}\n"
                    f"stderr: {result.stderr.decode(errors='replace')}\n"
                    f"stdout: {result.stdout.decode(errors='replace')}",
                )

        # Upload helper scripts used by builtin tools.
        await self._backend.write_file(
            self._glob_helper_script,
            _read_glob_helper_bytes(),
        )

        # Upload the gateway script last so its presence is the
        # idempotency marker we probe in :meth:`initialize`.
        await self._backend.write_file(
            self._gateway_script,
            _read_gateway_script_bytes(),
        )

    # ── internals: gateway lifecycle ────────────────────────────

    async def _restore_or_seed_mcps(self) -> list[MCPClient]:
        """Decide the MCP set to ship to the gateway on startup.

        * ``$workdir/.mcp`` missing → return ``default_mcps``.
        * ``.mcp`` present → :meth:`MCPClient.model_validate` each
          entry. Read / parse error → log and fall back to
          ``default_mcps``.
        """
        try:
            raw = await self._backend.read_file(self._mcp_file)
        except FileNotFoundError:
            return list(self.default_mcps)
        try:
            data = json.loads(raw.decode("utf-8"))
            return [MCPClient.model_validate(m) for m in data]
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "DaytonaWorkspace: failed to parse %s, falling back to "
                "default_mcps: %s",
                self._mcp_file,
                e,
            )
            return list(self.default_mcps)

    async def _save_mcp_file(self) -> None:
        """Persist ``self._mcps`` to ``$workdir/.mcp`` inside sandbox.

        Failures are logged but not raised.
        """
        payload = json.dumps(
            [m.model_dump(mode="json") for m in self._mcps],
            indent=2,
            ensure_ascii=False,
        )
        try:
            await self._backend.exec_shell(["mkdir", "-p", self.workdir])
            await self._backend.write_file(
                self._mcp_file,
                payload.encode("utf-8"),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "DaytonaWorkspace: failed to save %s: %s",
                self._mcp_file,
                e,
            )

    async def _write_gateway_config(self) -> None:
        """Drop the gateway's ``--config`` JSON into the sandbox."""
        cfg = {
            "token": self._gateway_token,
            "servers": [m.model_dump(mode="json") for m in self._mcps],
        }
        await self._backend.exec_shell(["mkdir", "-p", self._gateway_home])
        await self._backend.write_file(
            self._gateway_config,
            json.dumps(cfg, indent=2, ensure_ascii=False).encode("utf-8"),
        )

    async def _start_gateway_process(self) -> None:
        """Launch the gateway inside the sandbox as a detached process."""
        cmd = (
            f"nohup {shlex.quote(self._gateway_venv_py)} -u "
            f"{shlex.quote(self._gateway_script)} "
            f"--config {shlex.quote(self._gateway_config)} "
            f"--port {self.gateway_port} "
            f"> {shlex.quote(self._gateway_log)} 2>&1 &"
        )
        await self._backend.exec_shell(["sh", "-c", cmd])

    async def _wait_for_gateway(self, timeout: float = 30.0) -> None:
        """Block until the gateway answers ``/health``.

        If startup fails, include the tail of the gateway log in the
        exception so provider-side bootstrap issues are debuggable from
        host logs.
        """
        assert self._gateway is not None
        deadline = asyncio.get_event_loop().time() + timeout
        delay = 0.1
        while asyncio.get_event_loop().time() < deadline:
            if await self._gateway.health():
                return
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 1.0)
        try:
            log = await self._backend.read_file(self._gateway_log)
            tail = log[-2000:].decode(errors="replace")
        except Exception:
            tail = "<no gateway log available>"
        raise RuntimeError(
            f"gateway did not become healthy within {timeout}s. "
            f"Tail of {self._gateway_log}:\n{tail}",
        )

    async def _seed_skills(self) -> None:
        """Copy ``self.skill_paths`` into ``skills/`` once, on first init.

        Skips seeding when ``skill_paths`` is empty or the sandbox-side
        ``skills/`` already contains entries — meaning the user (or a
        prior init) is the source of truth.
        """
        if not self.skill_paths:
            return
        listing = await self._backend.exec_shell(
            [
                "sh",
                "-c",
                f"ls -A {shlex.quote(self._skills_dir)} "
                "2>/dev/null || true",
            ],
        )
        if listing.ok() and listing.stdout.strip():
            return
        for path in self.skill_paths:
            try:
                await self.add_skill(path)
            except Exception as e:  # noqa: BLE001
                logger.warning("DaytonaWorkspace: skip skill %r: %s", path, e)

    # ── internals: data offload ─────────────────────────────────

    async def _offload_data_block(self, block: DataBlock) -> DataBlock:
        """Persist a base64 :class:`DataBlock` under ``data/``.

        Mirrors :meth:`DockerWorkspace._offload_data_block` exactly,
        only the I/O primitive differs. Hashing the *base64* text
        rather than decoded bytes keeps the key short-circuit: a
        repeat offload of the same block writes the same file.
        """
        if not isinstance(block.source, Base64Source):
            return block
        digest = hashlib.sha256(block.source.data.encode()).hexdigest()
        ext = mimetypes.guess_extension(block.source.media_type) or ".bin"
        path = f"{self._data_dir}/{digest}{ext}"
        await self._backend.exec_shell(["mkdir", "-p", self._data_dir])
        await self._backend.write_file(path, base64.b64decode(block.source.data))
        return DataBlock(
            id=block.id,
            name=block.name,
            source=URLSource(
                url=AnyUrl(f"file://{path}"),
                media_type=block.source.media_type,
            ),
        )

    def _sandbox_proxy_headers(self, preview: Any) -> dict[str, str]:
        """Headers required by Daytona's preview proxy.

        This is provider authentication only. It is deliberately kept
        separate from the AgentScope gateway bearer token.
        """
        token = getattr(preview, "token", None)
        if not token:
            return {}
        return {DAYTONA_PREVIEW_TOKEN_HEADER: str(token)}


# ── SDK response helpers ──────────────────────────────────────────


async def _collect_sandboxes(result: Any) -> list[Any]:
    """Collect Daytona list results from async iterators or test fakes."""
    if hasattr(result, "__aiter__"):
        return [item async for item in result]
    if hasattr(result, "__await__"):
        result = await result
    if hasattr(result, "__aiter__"):
        return [item async for item in result]
    return list(result or [])


def _preview_url(preview: Any) -> str:
    """Extract preview URL from SDK response variants."""
    for attr in ("url", "preview_url", "public_url"):
        value = getattr(preview, attr, None)
        if value:
            return str(value).rstrip("/")
    if isinstance(preview, str):
        return preview.rstrip("/")
    raise RuntimeError("Daytona preview response did not contain a URL")


def _sandbox_id(sandbox: Any) -> str | None:
    """Return sandbox id from SDK object variants."""
    value = getattr(sandbox, "id", None) or getattr(sandbox, "sandbox_id", None)
    return None if value is None else str(value)


def _state_value(state: Any) -> str:
    """Normalize SDK enum/string sandbox states."""
    value = getattr(state, "value", state)
    return "" if value is None else str(value).lower()


def _candidate_sort_key(sandbox: Any) -> str:
    """Best-effort newest-first sort key for duplicate candidates."""
    for attr in ("last_activity_at", "updated_at", "created_at"):
        value = getattr(sandbox, attr, None)
        if value is not None:
            return str(value)
    return _sandbox_id(sandbox) or ""
