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
  + agentscope (``--no-deps``) and uploads the gateway script. The
  probe + install loop lives on :class:`SandboxedWorkspaceBase`; this
  subclass only supplies the sandbox-specific shell commands via
  :meth:`_bootstrap_commands`. Bootstrap runs at most once per sandbox
  lifetime (the presence of the gateway script on disk is the marker).
* **MCP gateway.** Identical to Docker: a FastAPI process inside the
  sandbox. All host-side calls drive the gateway through
  :class:`GatewayClient`, which runs an in-sandbox ``python3 -c`` shim
  via :meth:`E2BBackend.exec_shell` — no host→sandbox network
  reachability is required.
* **Service-layer index.** The host stores only ``workspace_id``;
  the sandbox carries ``METADATA_WORKSPACE_ID_KEY = workspace_id`` in
  its E2B metadata. Manager code calls ``AsyncSandbox.list(query=...)``
  with that filter to find the sandbox on cache miss.

Configuration is per-instance: every workspace owns one sandbox. The
manager handles cache, TTL eviction and metadata-based reattachment.
"""

import asyncio
import shlex
from typing import Any

from ..._logging import logger
from ...mcp import MCPClient
from .._sandboxed_base import SandboxedWorkspaceBase
from .._utils import _GATEWAY_BASE_REQUIREMENTS, DEFAULT_WORKSPACE_INSTRUCTIONS
from ._constants import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_TEMPLATE,
    DEFAULT_TIMEOUT,
    GATEWAY_HOME,
    METADATA_WORKSPACE_ID_KEY,
    SANDBOX_WORKDIR,
)
from ._e2b_backend import E2BBackend


# ── the workspace ──────────────────────────────────────────────────


class E2BWorkspace(SandboxedWorkspaceBase):
    """Workspace backed by an E2B cloud sandbox.

    ``default_mcps`` and ``skill_paths`` are seed-time inputs and are
    not retained as instance state past :meth:`initialize`.
    """

    _gateway_home = GATEWAY_HOME
    # E2B ``base`` template already ships Python + curl, so every
    # bootstrap step is expected to complete inside 10 minutes even on
    # the slowest CI runners.
    _bootstrap_cmd_timeout = 600.0

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
        instructions: str = DEFAULT_WORKSPACE_INSTRUCTIONS,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
    ) -> None:
        """Construct an :class:`E2BWorkspace`.

        The sandbox is *not* started here — call :meth:`initialize`
        (or use the workspace as an ``async`` context manager).

        Args:
            workspace_id (`str | None`, optional):
                Stable identifier; also stored in sandbox metadata for
                reattachment.
            template (`str`, defaults to `DEFAULT_TEMPLATE`):
                E2B template id.
            api_key (`str`, defaults to `""`):
                E2B API key (``""`` falls back to ``E2B_API_KEY``).
            domain (`str`, defaults to `""`):
                Optional custom E2B domain.
            timeout_seconds (`int`, defaults to `DEFAULT_TIMEOUT`):
                Sandbox keep-alive timeout.
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the in-sandbox gateway listens on.
            env (`dict[str, str] | None`, optional):
                Environment variables baked into the sandbox.
            sandbox_metadata (`dict[str, str] | None`, optional):
                Extra metadata merged with the workspace-id tag.
            extra_pip (`list[str] | None`, optional):
                Extra Python packages installed into the gateway venv
                during bootstrap.
            instructions (`str`, defaults to `DEFAULT_WORKSPACE_INSTRUCTIONS`):
                System-prompt fragment template (supports ``{workdir}``).
            default_mcps (`list[MCPClient] | None`, optional):
                MCPs registered on first init when no persisted
                ``.mcp`` exists.
            skill_paths (`list[str] | None`, optional):
                Local skill dirs seeded into ``skills/`` on first init.
        """
        super().__init__(
            workspace_id=workspace_id,
            default_mcps=default_mcps,
            skill_paths=skill_paths,
        )

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
        self.instructions = instructions.format(
            backend="E2B-based",
            workdir=self.workdir,
        )

        # ── runtime state (E2B-only) ────────────────────────────
        self._sandbox: Any = None  # e2b.AsyncSandbox
        self._backend: E2BBackend | None = None

    # ── lifecycle hooks ─────────────────────────────────────────

    @property
    def sandbox_id(self) -> str | None:
        """E2B sandbox id, or ``None`` if not started."""
        return self._sandbox.sandbox_id if self._sandbox else None

    async def _provision_backend(self) -> None:
        """Reattach or create the sandbox and bind the backend.

        First-time bootstrap (uv → gateway venv → agentscope → gateway
        script upload) is driven by
        :meth:`SandboxedWorkspaceBase._ensure_bootstrapped` once
        ``initialize`` has bound the backend; every step is idempotent
        so an interrupted bootstrap re-runs cleanly.
        """
        await self._attach_or_create_sandbox()
        self._backend = E2BBackend(self._sandbox, workdir=SANDBOX_WORKDIR)

    async def _teardown_backend(self) -> None:
        """Pause the sandbox (keep filesystem) and drop the handle.

        ``sandbox.pause()`` — not ``kill()`` — so the next
        :meth:`initialize` can reattach via metadata lookup and
        auto-resume. Errors are swallowed.
        """
        if self._sandbox is not None:
            try:
                await self._sandbox.pause()
            except Exception as e:
                logger.warning("E2BWorkspace: pause failed: %s", e)
            self._sandbox = None

    # ── instructions ────────────────────────────────────────────

    async def get_instructions(self) -> str:
        """Return the system-prompt fragment for this workspace.

        Substitutes ``{workdir}`` in the configured template with
        the sandbox-side path (``/home/user/workspace``). The agent
        always sees sandbox-internal paths.
        """
        return self.instructions.format(workdir=SANDBOX_WORKDIR)

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

    # ── internals: bootstrap ────────────────────────────────────

    def _bootstrap_commands(self) -> list[str]:
        """Shell commands that provision this E2B sandbox once.

        Only runs when the gateway script is missing (fresh sandbox or
        a prior bootstrap that was interrupted). Every step is
        idempotent so a resumed sandbox can re-run cleanly.

        ``--no-deps`` on agentscope is mandatory: the gateway only
        imports :class:`agentscope.mcp.MCPClient` whose transitive
        needs (``mcp / pydantic / httpx``) are already installed via
        the gateway base requirements. Pulling the full dep tree
        drags in heavy / Rust-built packages the E2B image cannot
        compile.
        """
        pip_pkgs = list(_GATEWAY_BASE_REQUIREMENTS) + list(self.extra_pip)
        # Quote every requirement string so entries with spaces or
        # shell metacharacters cannot break the ``sh -c`` command or
        # become a command-injection vector inside the sandbox.
        pip_args = " ".join(shlex.quote(p) for p in pip_pkgs)

        return [
            # 1. System deps + uv installer, both via sudo so uv lands
            # at /usr/local/bin (on PATH — no absolute-path plumbing
            # needed downstream).
            "sudo apt-get update -qq "
            "&& sudo apt-get install -y --no-install-recommends ripgrep "
            "&& sudo rm -rf /var/lib/apt/lists/*",
            "curl -LsSf https://astral.sh/uv/install.sh "
            "| sudo env UV_INSTALL_DIR=/usr/local/bin "
            "INSTALLER_NO_MODIFY_PATH=1 sh",
            # 2. Gateway venv + base requirements + agentscope.
            f"uv venv {self._gateway_venv}",
            f"uv pip install --python {self._gateway_python} {pip_args}",
            f"uv pip install --python {self._gateway_python} "
            f"--no-deps 'agentscope'",
        ]
