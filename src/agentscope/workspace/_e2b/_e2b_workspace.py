# -*- coding: utf-8 -*-
"""E2BWorkspace — sandboxed workspace backed by an E2B cloud sandbox.

Architecture
------------

Mirrors :class:`agentscope.workspace.DockerWorkspace` but swaps the
Docker engine for the E2B SDK (``e2b.AsyncSandbox``):

* **Lifecycle.** ``initialize()`` looks up an existing sandbox by
  metadata and either resumes it (``connect(sandbox_id=...)``
  auto-resumes paused sandboxes) or creates a fresh one and runs the
  bootstrap shell sequence. ``close()`` calls ``sandbox.pause()`` so
  the sandbox filesystem (skills, ``.mcp``, sessions, data) survives
  for the next ``initialize()``. There is no ``kill()`` path in this
  iteration.
* **Persistence.** Sandbox filesystem state is the persistence layer —
  there is no host-side ``workdir`` parameter. Pausing keeps the disk;
  resuming brings it back wholesale.
* **Bootstrap.** First-time provisioning installs uv + a gateway venv
  + agentscope (``--no-deps``) and uploads the gateway script. We
  detect whether bootstrap has already happened via a single
  ``files.exists(GATEWAY_SCRIPT)`` probe so the cost is paid exactly
  once per sandbox lifetime.
* **MCP gateway.** Identical to Docker: a FastAPI process inside the
  sandbox, host-side talks to it over HTTPS via E2B's proxy
  (``sandbox.get_host(port)`` + ``X-Access-Token`` header).
* **Service-layer index.** The host stores only ``workspace_id``;
  the sandbox carries ``METADATA_WORKSPACE_ID_KEY = workspace_id`` in
  its E2B metadata. Manager code calls ``AsyncSandbox.list(query=...)``
  with that filter to find the sandbox on cache miss.

Configuration is per-instance: every workspace owns one sandbox. The
manager handles cache, TTL eviction and metadata-based reattachment.
"""

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
from typing import Any

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
from .._gateway_client import (
    GatewayClient,
    GatewayMCPClient,
)
from .._utils import (
    _agentscope_version,
    _is_released_install,
    _read_gateway_script_bytes,
    _read_glob_helper_bytes,
)
from ._bootstrap import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_TEMPLATE,
    DEFAULT_TIMEOUT,
    DEV_SRC_TAR,
    GATEWAY_CONFIG,
    GATEWAY_HOME,
    GATEWAY_LOG,
    GATEWAY_SCRIPT,
    GATEWAY_VENV_PY,
    GLOB_HELPER_SCRIPT,
    METADATA_WORKSPACE_ID_KEY,
    SANDBOX_DATA_DIR,
    SANDBOX_MCP_FILE,
    SANDBOX_SESSIONS_DIR,
    SANDBOX_SKILLS_DIR,
    SANDBOX_WORKDIR,
    bootstrap_commands,
    build_source_tarball,
    log_bootstrap_attempt,
    render_install_agentscope_cmd_dev,
    render_install_agentscope_cmd_released,
)
from ._e2b_backend import E2BBackend

_DEFAULT_INSTRUCTIONS = """<workspace>
You have an E2B-based cloud workspace. All tool calls execute **inside
the sandbox** at ``{workdir}``.

Layout:

```
{workdir}
├── data/        # offloaded multimodal files
├── skills/      # reusable skills
└── sessions/    # session context and tool results
```

Use the MCP-provided tools to interact with the sandbox's filesystem
and processes.
</workspace>"""


# ── the workspace ──────────────────────────────────────────────────


class E2BWorkspace(WorkspaceBase):
    """Workspace backed by an E2B cloud sandbox.

    ``default_mcps`` and ``skill_paths`` are seed-time inputs and are
    not retained as instance state past :meth:`initialize`.
    """

    def __init__(
        self,
        *,
        workspace_id: str | None = None,
        template: str = DEFAULT_TEMPLATE,
        api_key: str = "",
        domain: str = "",
        timeout_seconds: int = DEFAULT_TIMEOUT,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        env: dict[str, str] | None = None,
        sandbox_metadata: dict[str, str] | None = None,
        extra_pip: list[str] | None = None,
        instructions: str = _DEFAULT_INSTRUCTIONS,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
    ) -> None:
        """Construct an :class:`E2BWorkspace`.

        The sandbox is *not* started here; call :meth:`initialize`
        (or use the workspace as an ``async`` context manager).

        Args:
            workspace_id (`str | None`, optional):
                Stable identifier; doubles as the value stored in the
                sandbox's ``agentscope.workspace.id`` metadata for
                later reattachment. ``None`` generates a fresh UUID.
            template (`str`, defaults to `DEFAULT_TEMPLATE`):
                E2B template id. Defaults to ``"base"`` — the stock
                Ubuntu image with python3 + curl, which is enough for
                the bootstrap to install uv on top.
            api_key (`str`, defaults to `""`):
                E2B API key. ``""`` falls back to the ``E2B_API_KEY``
                env var.
            domain (`str`, defaults to `""`):
                Optional custom E2B domain (self-hosted etc.).
            timeout_seconds (`int`, defaults to `DEFAULT_TIMEOUT`):
                Sandbox keep-alive timeout passed to ``create`` /
                ``connect``.
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the in-sandbox gateway listens on.
            env (`dict[str, str] | None`, optional):
                Environment variables baked into the sandbox at
                create time (``envs`` parameter on the SDK side).
            sandbox_metadata (`dict[str, str] | None`, optional):
                Extra metadata merged with
                ``{METADATA_WORKSPACE_ID_KEY: workspace_id}``. Useful
                for attaching ``user_id`` / ``agent_id`` for E2B
                dashboard filtering.
            extra_pip (`list[str] | None`, optional):
                Extra Python packages to install into the gateway
                venv during bootstrap.
            instructions (`str`, defaults to `_DEFAULT_INSTRUCTIONS`):
                System-prompt fragment template returned by
                :meth:`get_instructions`.
            default_mcps (`list[MCPClient] | None`, optional):
                Initial MCPs registered on first :meth:`initialize`.
                Subsequent restarts read ``$workdir/.mcp`` instead.
            skill_paths (`list[str] | None`, optional):
                Local skill directories seeded into
                ``$workdir/skills`` on first :meth:`initialize`.
        """
        super().__init__(workspace_id=workspace_id)

        # ── serializable config ─────────────────────────────────
        self.workdir = SANDBOX_WORKDIR
        self.template = template
        self.api_key = api_key
        self.domain = domain
        self.timeout_seconds = timeout_seconds
        self.gateway_port = gateway_port
        self.env: dict[str, str] = dict(env or {})
        self.sandbox_metadata: dict[str, str] = dict(sandbox_metadata or {})
        self.extra_pip: list[str] = list(extra_pip or [])
        self.instructions = instructions

        # ── seed-only ───────────────────────────────────────────
        self.default_mcps: list[MCPClient] = list(default_mcps or [])
        self.skill_paths: list[str] = list(skill_paths or [])

        # ── runtime state ───────────────────────────────────────
        self._sandbox: Any = None  # e2b.AsyncSandbox
        self._backend: E2BBackend | None = None
        self._gateway: GatewayClient | None = None
        self._gateway_token: str = ""
        self._mcps: list[MCPClient] = []
        self._gateway_clients: dict[str, GatewayMCPClient] = {}
        self._mcp_lock = asyncio.Lock()
        self._skill_lock = asyncio.Lock()

    # ── lifecycle ───────────────────────────────────────────────

    @property
    def sandbox_id(self) -> str | None:
        """E2B sandbox id, or ``None`` if not started."""
        return self._sandbox.sandbox_id if self._sandbox else None

    async def initialize(self) -> None:
        """Reattach or create the sandbox, then start the gateway.

        Steps:

        1. Look up an existing sandbox via
           ``AsyncSandbox.list(query=SandboxQuery(metadata=...))``.
           If found, ``AsyncSandbox.connect(sandbox_id=...)`` reattaches
           — it auto-resumes paused sandboxes.
        2. If not found, ``AsyncSandbox.create(...)`` provisions a
           fresh sandbox tagged with our metadata and runs bootstrap
           (uv → gateway venv → agentscope ``--no-deps`` → upload
           gateway script).
        3. If bootstrap output is missing on a reattached sandbox
           (e.g. a previous bootstrap was interrupted), run bootstrap
           again — detected by ``files.exists(GATEWAY_SCRIPT)``.
        4. Restore MCPs from ``$workdir/.mcp`` if present, else seed
           from ``default_mcps``.
        5. Mint a fresh gateway bearer token (not persisted).
        6. Kill any leftover gateway process, drop a fresh
           ``gateway.config.json`` into the sandbox, launch the
           gateway, wait for ``/health``.
        7. Pull the gateway-side MCP view back as
           :class:`GatewayMCPClient` instances.

        Idempotent — a no-op when already alive.
        """
        if self.is_alive:
            return

        await self._attach_or_create_sandbox()
        self._backend = E2BBackend(self._sandbox, workdir=SANDBOX_WORKDIR)

        # If the gateway script is missing, the sandbox is fresh (or
        # a prior bootstrap was interrupted). Re-running bootstrap is
        # safe because every step is idempotent (mkdir -p, uv venv,
        # uv pip install).
        if not await self._sandbox.files.exists(GATEWAY_SCRIPT):
            # The backend pins ``cwd=SANDBOX_WORKDIR`` so the very
            # first bootstrap command (which itself is ``mkdir -p``)
            # would fail before it ran when the dir does not yet
            # exist. Use ``cwd="/"`` to break the chicken-and-egg —
            # ``mkdir -p`` itself never fails on an already-existing
            # directory.
            await self._backend.exec_shell(
                ["mkdir", "-p", SANDBOX_WORKDIR],
                cwd="/",
            )
            await self._run_bootstrap()

        self._mcps = await self._restore_or_seed_mcps()

        self._gateway_token = uuid.uuid4().hex

        # Stop any stale gateway from a previous resume cycle. Each
        # init mints a new bearer token, so an old gateway listening
        # on the port would happily accept old-token requests but
        # reject new ones — kill it before starting the new one.
        await self._backend.exec_shell(
            ["sh", "-c", "pkill -f _mcp_gateway_app.py || true"],
        )

        await self._write_gateway_config()
        await self._start_gateway_process()

        host = self._sandbox.get_host(self.gateway_port)
        self._gateway = GatewayClient(
            base_url=f"https://{host}",
            token=self._gateway_token,
            timeout=30.0,
            extra_headers=self._sandbox_proxy_headers(),
        )
        await self._wait_for_gateway()

        self._gateway_clients = {
            c.name: c for c in await self._gateway.list_mcps()
        }

        # Persist the MCP set unconditionally so a freshly seeded
        # ``self._mcps`` (default_mcps path) is rewritten as the
        # canonical ``.mcp`` for the next restart, and a restored set
        # is round-tripped harmlessly. ``_seed_skills`` itself is
        # idempotent — it short-circuits when the sandbox-side
        # ``skills/`` already has entries.
        await self._save_mcp_file()
        await self._seed_skills()

        self.is_alive = True

    async def reset(self) -> None:
        """Return the workspace to an empty state.

        Mirrors :meth:`DockerWorkspace.reset`: deregisters every MCP
        from the gateway, clears the local handles, and wipes
        ``.mcp``, ``skills/``, ``sessions/``, and ``data/`` inside the
        sandbox. The gateway process keeps running with no upstream
        MCPs. ``default_mcps`` / ``skill_paths`` are not re-seeded.
        """
        if self._backend is None:
            raise RuntimeError(
                "E2BWorkspace is not initialized: its sandbox backend "
                "is unavailable. Use 'async with workspace:' or call "
                "'await workspace.initialize()' before 'reset()'.",
            )

        async with self._mcp_lock, self._skill_lock:
            for gw_client in list(self._gateway_clients.values()):
                try:
                    await gw_client.close()
                except Exception as e:
                    logger.warning(
                        "MCP %r close failed during reset: %s",
                        gw_client.name,
                        e,
                    )
            self._gateway_clients.clear()
            self._mcps = []

            for path in (
                SANDBOX_SESSIONS_DIR,
                SANDBOX_DATA_DIR,
                SANDBOX_SKILLS_DIR,
            ):
                await self._backend.delete_path(path)

            # Rewrite ``.mcp`` to an empty list so a future restart does
            # not fall back to ``default_mcps``.
            await self._save_mcp_file()

    async def close(self) -> None:
        """Pause the sandbox and release host-side resources.

        ``sandbox.pause()`` (not ``kill()``) keeps the sandbox's
        filesystem so the next :meth:`initialize` can reattach to it
        via metadata lookup. The host-side gateway client is closed
        first so its connection pool is released cleanly.

        Errors during teardown are swallowed so ``close`` is always
        safe to call (e.g. from ``__aexit__``).
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
                await self._sandbox.pause()
            except Exception as e:
                logger.warning("E2BWorkspace: pause failed: %s", e)
            self._sandbox = None
            self._backend = None

        self.is_alive = False

    # ── instructions ────────────────────────────────────────────

    async def get_instructions(self) -> str:
        """Return the system-prompt fragment for this workspace.

        Substitutes ``{workdir}`` in the configured template with
        the sandbox-side path (``/home/user/workspace``). The agent
        always sees sandbox-internal paths.
        """
        return self.instructions.format(workdir=SANDBOX_WORKDIR)

    # ── tool / MCP / skill discovery ────────────────────────────

    async def list_tools(self) -> list[ToolBase]:
        """Built-in tools backed by the E2B sandbox.

        Returns the six builtin tools (Bash, Read, Write, Edit, Grep,
        Glob), each backed by the workspace's :class:`E2BBackend`
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
                "E2BWorkspace is not initialized: its sandbox backend "
                "is unavailable. Use 'async with workspace:' or call "
                "'await workspace.initialize()' before 'list_tools()'.",
            )

        from ...tool._builtin import Bash, Edit, Glob, Grep, Read, Write

        return [
            Bash(cwd=SANDBOX_WORKDIR, backend=self._backend),
            Edit(backend=self._backend),
            Glob(backend=self._backend, glob_helper_path=GLOB_HELPER_SCRIPT),
            Grep(backend=self._backend),
            Read(backend=self._backend),
            Write(backend=self._backend),
        ]

    async def list_mcps(self) -> list[MCPClient]:
        """Return one :class:`GatewayMCPClient` per registered MCP.

        Each entry's ``name`` matches the upstream MCP server name and
        all of its protocol calls are routed over HTTPS to the
        in-sandbox gateway.
        """
        return list(self._gateway_clients.values())

    async def list_skills(self) -> list[Skill]:
        """Enumerate skills by scanning ``skills/`` inside the sandbox.

        Reads each ``SKILL.md`` via the SDK's ``files.read`` and parses
        the YAML front-matter. Files missing ``name`` or ``description``
        are skipped.
        """
        import frontmatter as fm

        result = await self._backend.exec_shell(
            [
                "sh",
                "-c",
                f"find {SANDBOX_SKILLS_DIR} -name SKILL.md "
                f"2>/dev/null || true",
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
            except Exception as e:
                logger.warning("Failed to load skill %s: %s", md_path, e)
        return skills

    # ── dynamic MCP management ──────────────────────────────────

    async def add_mcp(self, mcp_client: MCPClient) -> None:
        """Register a new MCP server on the in-sandbox gateway.

        Mirrors :meth:`DockerWorkspace.add_mcp` but persists ``.mcp``
        unconditionally — the sandbox filesystem is always
        persistent for E2B.
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
            except Exception as e:
                logger.warning("MCP %r close failed: %s", name, e)
            self._mcps = [m for m in self._mcps if m.name != name]
            await self._save_mcp_file()

    # ── dynamic skill management ────────────────────────────────

    async def add_skill(self, skill_path: str) -> None:
        """Upload a local skill directory into ``skills/`` inside the sandbox.

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
            await self._backend.exec_shell(
                ["mkdir", "-p", SANDBOX_SKILLS_DIR],
            )
            dir_name = os.path.basename(os.path.abspath(skill_path))

            check = await self._backend.exec_shell(
                ["test", "-e", SANDBOX_SKILLS_DIR + "/" + dir_name],
            )
            if check.ok():
                raise ValueError(
                    f"Skill directory {dir_name!r} already exists in "
                    f"{SANDBOX_SKILLS_DIR}",
                )

            for root, _dirs, files in os.walk(skill_path):
                for fname in files:
                    local = os.path.join(root, fname)
                    rel = os.path.relpath(local, skill_path)
                    remote = f"{SANDBOX_SKILLS_DIR}/{dir_name}/{rel}"
                    with open(local, "rb") as f:
                        data = f.read()
                    await self._backend.write_file(remote, data)

            logger.info(
                "E2BWorkspace: added skill %r at %s/%s",
                dir_name,
                SANDBOX_SKILLS_DIR,
                dir_name,
            )

    async def remove_skill(self, name: str) -> None:
        """Delete a skill directory by its agent-facing name."""
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
        base = f"{SANDBOX_SESSIONS_DIR}/{session_id}"
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
        base = f"{SANDBOX_SESSIONS_DIR}/{session_id}"
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
                        d = await self._offload_data_block(block)
                        url = str(d.source.url)
                    else:
                        url = str(block.source.url)
                    parts.append(
                        f"<data url='{url}' name='{block.name}' "
                        f"media_type='{block.source.media_type}'/>",
                    )

        await self._backend.exec_shell(["mkdir", "-p", base])
        await self._backend.write_file(
            path,
            "".join(parts).encode("utf-8"),
        )
        return path

    # ── internals: sandbox attach / create ─────────────────────

    async def _attach_or_create_sandbox(self) -> None:
        """Reattach to an existing sandbox by metadata, or create one.

        Resolution rule: a single sandbox is expected per
        ``workspace_id``. If multiple are returned (e.g. a leaked
        running + paused pair after an unclean shutdown) we attach to
        the newest by ``started_at`` and log a warning — manual
        cleanup is left to the operator.

        Always blocks until the sandbox's envd answers
        :meth:`AsyncSandbox.is_running` so the caller can issue
        ``commands`` / ``files`` calls without hitting transient
        "not yet routable" errors — typical on a paused sandbox that
        has just been auto-resumed via ``connect``.
        """
        from e2b import AsyncSandbox

        existing = await self._find_existing_sandbox()

        api_opts = self._api_opts()
        if existing is not None:
            self._sandbox = await AsyncSandbox.connect(
                sandbox_id=existing.sandbox_id,
                timeout=self.timeout_seconds,
                **api_opts,
            )
        else:
            merged_metadata = {
                METADATA_WORKSPACE_ID_KEY: self.workspace_id,
                **self.sandbox_metadata,
            }
            create_kwargs: dict[str, Any] = {
                "template": self.template,
                "timeout": self.timeout_seconds,
                "metadata": merged_metadata,
                **api_opts,
            }
            if self.env:
                create_kwargs["envs"] = self.env

            self._sandbox = await AsyncSandbox.create(**create_kwargs)

        await self._wait_until_running()

    async def _wait_until_running(self, timeout: float = 30.0) -> None:
        """Poll ``self._sandbox.is_running()`` until it answers ``True``.

        ``AsyncSandbox.create`` / ``AsyncSandbox.connect`` can return
        before the in-sandbox envd is routable. Subsequent
        ``commands.run`` / ``files.exists`` calls against an
        unrouted envd surface as transient SDK errors. We poll envd's
        own ``/health`` (which is what :meth:`AsyncSandbox.is_running`
        wraps — 502 → ``False``, 200 → ``True``) until it goes green.

        Args:
            timeout (`float`, defaults to `30.0`):
                Hard ceiling in seconds. Raises :class:`RuntimeError`
                if envd is still not routable after this long.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        delay = 0.1
        while asyncio.get_event_loop().time() < deadline:
            try:
                if await self._sandbox.is_running():
                    return
            except Exception as e:  # noqa: BLE001
                # SDK can raise on transient network / proxy errors
                # while the sandbox is still provisioning. Treat as
                # "not yet" and keep polling.
                logger.debug(
                    "E2BWorkspace: is_running probe error (will retry): %s",
                    e,
                )
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 1.0)
        raise RuntimeError(
            f"E2B sandbox did not become ready within {timeout}s "
            f"(workspace_id={self.workspace_id!r})",
        )

    async def _find_existing_sandbox(self) -> Any:
        """List sandboxes filtered by ``workspace_id`` metadata.

        Returns the most recent :class:`SandboxInfo` (paused or
        running) or ``None`` if no match exists.
        """
        from e2b import AsyncSandbox
        from e2b.api.client.models.sandbox_state import SandboxState
        from e2b.sandbox.sandbox_api import SandboxQuery

        query = SandboxQuery(
            metadata={METADATA_WORKSPACE_ID_KEY: self.workspace_id},
            state=[SandboxState.PAUSED, SandboxState.RUNNING],
        )

        candidates: list[Any] = []
        paginator = AsyncSandbox.list(query=query, **self._api_opts())
        while paginator.has_next:
            try:
                page = await paginator.next_items()
            except Exception as e:
                logger.warning(
                    "E2BWorkspace: list sandboxes failed: %s",
                    e,
                )
                break
            candidates.extend(page)

        if not candidates:
            return None
        if len(candidates) > 1:
            logger.warning(
                "E2BWorkspace: %d sandboxes match workspace_id=%r; "
                "attaching to most recent",
                len(candidates),
                self.workspace_id,
            )
        candidates.sort(key=lambda s: s.started_at, reverse=True)
        return candidates[0]

    def _api_opts(self) -> dict[str, Any]:
        """Common ``api_key`` / ``domain`` opts forwarded to E2B SDK calls."""
        opts: dict[str, Any] = {}
        if self.api_key:
            opts["api_key"] = self.api_key
        if self.domain:
            opts["domain"] = self.domain
        return opts

    def _sandbox_proxy_headers(self) -> dict[str, str]:
        """Headers required by the E2B proxy to reach the gateway port.

        E2B's edge proxy gates non-default ports behind the
        ``X-Access-Token`` header tied to the sandbox. The token is
        exposed on the sandbox object as ``traffic_access_token``.
        """
        if self._sandbox is None:
            return {}
        token = getattr(self._sandbox, "traffic_access_token", None)
        if not token:
            return {}
        return {"X-Access-Token": token}

    # ── internals: bootstrap ────────────────────────────────────

    async def _run_bootstrap(self) -> None:
        """Provision a fresh sandbox: uv → venv → agentscope → script.

        Each command runs through :meth:`_exec`; a non-zero exit
        raises :class:`RuntimeError` with the captured stderr so
        startup failures are visible in logs (mirroring the docker
        build-tail strategy).
        """
        if _is_released_install():
            log_bootstrap_attempt(self.workspace_id, "released")
            install_cmd = render_install_agentscope_cmd_released(
                _agentscope_version(),
            )
        else:
            log_bootstrap_attempt(self.workspace_id, "dev")
            tar_bytes = build_source_tarball()
            await self._backend.write_file(DEV_SRC_TAR, tar_bytes)
            install_cmd = render_install_agentscope_cmd_dev()

        commands = bootstrap_commands(
            extra_pip=self.extra_pip,
            install_agentscope_cmd=install_cmd,
        )
        for cmd in commands:
            r = await self._backend.exec_shell(
                ["sh", "-c", cmd],
                timeout=600.0,
            )
            if not r.ok():
                raise RuntimeError(
                    f"E2BWorkspace bootstrap failed (exit {r.exit_code}) "
                    f"for: {cmd!r}\n"
                    f"stderr: {r.stderr.decode(errors='replace')}\n"
                    f"stdout: {r.stdout.decode(errors='replace')}",
                )

        # Upload helper scripts used by builtin tools.
        await self._backend.write_file(
            GLOB_HELPER_SCRIPT,
            _read_glob_helper_bytes(),
        )

        # Upload the gateway script last so its presence is the
        # idempotency marker we probe in :meth:`initialize`.
        await self._backend.write_file(
            GATEWAY_SCRIPT,
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
            raw = await self._backend.read_file(SANDBOX_MCP_FILE)
        except FileNotFoundError:
            return list(self.default_mcps)
        try:
            data = json.loads(raw.decode("utf-8"))
            return [MCPClient.model_validate(m) for m in data]
        except Exception as e:
            logger.warning(
                "E2BWorkspace: failed to parse %s, falling back to "
                "default_mcps: %s",
                SANDBOX_MCP_FILE,
                e,
            )
            return list(self.default_mcps)

    async def _save_mcp_file(self) -> None:
        """Persist ``self._mcps`` to ``$workdir/.mcp`` inside the sandbox.

        Failures are logged but not raised.
        """
        payload = json.dumps(
            [m.model_dump(mode="json") for m in self._mcps],
            indent=2,
            ensure_ascii=False,
        )
        try:
            await self._backend.exec_shell(
                ["mkdir", "-p", SANDBOX_WORKDIR],
            )
            await self._backend.write_file(
                SANDBOX_MCP_FILE,
                payload.encode("utf-8"),
            )
        except Exception as e:
            logger.warning(
                "E2BWorkspace: failed to save %s: %s",
                SANDBOX_MCP_FILE,
                e,
            )

    async def _write_gateway_config(self) -> None:
        """Drop the gateway's ``--config`` JSON into the sandbox."""
        cfg = {
            "token": self._gateway_token,
            "servers": [m.model_dump(mode="json") for m in self._mcps],
        }
        await self._backend.exec_shell(
            ["mkdir", "-p", GATEWAY_HOME],
        )
        await self._backend.write_file(
            GATEWAY_CONFIG,
            json.dumps(cfg, indent=2, ensure_ascii=False).encode("utf-8"),
        )

    async def _start_gateway_process(self) -> None:
        """Launch the gateway inside the sandbox as a detached process."""
        cmd = (
            f"nohup {shlex.quote(GATEWAY_VENV_PY)} -u "
            f"{shlex.quote(GATEWAY_SCRIPT)} "
            f"--config {shlex.quote(GATEWAY_CONFIG)} "
            f"--port {self.gateway_port} "
            f"> {shlex.quote(GATEWAY_LOG)} 2>&1 &"
        )
        await self._backend.exec_shell(["sh", "-c", cmd])

    async def _wait_for_gateway(self, timeout: float = 30.0) -> None:
        """Block until the gateway answers ``/health`` with 200."""
        assert self._gateway is not None
        deadline = asyncio.get_event_loop().time() + timeout
        delay = 0.1
        while asyncio.get_event_loop().time() < deadline:
            if await self._gateway.health():
                return
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 1.0)
        try:
            log = await self._backend.read_file(GATEWAY_LOG)
            tail = log[-2000:].decode(errors="replace")
        except Exception:
            tail = "<no gateway log available>"
        raise RuntimeError(
            f"gateway did not become healthy within {timeout}s. "
            f"Tail of {GATEWAY_LOG}:\n{tail}",
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
                f"ls -A {shlex.quote(SANDBOX_SKILLS_DIR)} "
                f"2>/dev/null || true",
            ],
        )
        if listing.ok() and listing.stdout.strip():
            return
        for path in self.skill_paths:
            try:
                await self.add_skill(path)
            except Exception as e:
                logger.warning(
                    "E2BWorkspace: skip skill %r: %s",
                    path,
                    e,
                )

    # ── internals: data offload ────────────────────────────────

    async def _offload_data_block(self, block: DataBlock) -> DataBlock:
        """Persist a base64 :class:`DataBlock` under ``data/``.

        Mirrors :meth:`DockerWorkspace._offload_data_block` exactly,
        only the I/O primitive differs. Hashing the *base64* text
        rather than decoded bytes keeps the key short-circuit: a
        repeat offload of the same block writes the same file.
        """
        if not isinstance(block.source, Base64Source):
            return block
        h = hashlib.sha256(block.source.data.encode()).hexdigest()
        ext = mimetypes.guess_extension(block.source.media_type) or ".bin"
        path = f"{SANDBOX_DATA_DIR}/{h}{ext}"
        await self._backend.exec_shell(
            ["mkdir", "-p", SANDBOX_DATA_DIR],
        )
        await self._backend.write_file(
            path,
            base64.b64decode(block.source.data),
        )
        return DataBlock(
            id=block.id,
            name=block.name,
            source=URLSource(
                url=AnyUrl(f"file://{path}"),
                media_type=block.source.media_type,
            ),
        )
