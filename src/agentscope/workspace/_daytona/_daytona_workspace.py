# -*- coding: utf-8 -*-
"""DaytonaWorkspace — sandboxed workspace backed by Daytona.

Architecture
------------

Mirrors :class:`agentscope.workspace.E2BWorkspace` and
:class:`agentscope.workspace.DockerWorkspace` at the AgentScope
boundary, but swaps the provider runtime for the Daytona SDK:

* **Lifecycle.** ``initialize()`` is inherited from
  :class:`SandboxedWorkspaceBase`; it calls
  :meth:`_provision_backend` to look up a sandbox by the
  ``agentscope.workspace.id`` label, start / recover an existing
  candidate when possible, or create a new sandbox and run bootstrap.
  ``close()`` calls :meth:`_teardown_backend`, which uses Daytona
  ``stop(force=False)`` so filesystem persistence survives reattach.
* **Persistence.** Sandbox filesystem state is the persistence layer.
  ``.mcp``, ``skills/``, ``sessions/`` and ``data/`` are managed by
  :class:`WorkspaceBase` under the SDK-reported workdir.
* **Bootstrap.** First-time provisioning installs ripgrep, uv, the
  gateway virtualenv, AgentScope itself, the gateway script and the
  glob helper. Bootstrap is detected by probing the gateway script path
  inside the sandbox and is safe to rerun when a previous attempt was
  interrupted.
* **MCP gateway.** Identical to Docker/E2B after the shared-base
  migration: a FastAPI process runs inside the sandbox. Host-side calls
  drive the gateway through :class:`GatewayClient`, which runs an
  in-sandbox ``python3 -c`` shim via :meth:`DaytonaBackend.exec_shell`.
  Daytona preview URLs are not part of the internal gateway path.
* **SDK paths.** The real workdir and user home are read from Daytona
  before deriving gateway paths. The implementation does not assume a
  fixed OS user, root home, or ``/home/daytona`` layout.
* **Service-layer index.** The host stores only ``workspace_id``; the
  sandbox carries ``METADATA_WORKSPACE_ID_KEY = workspace_id`` in its
  Daytona labels. Manager code asks the workspace to reattach by that
  label on cache miss.

Configuration is per-workspace. The manager handles cache and TTL
eviction; each workspace owns its own ``AsyncDaytona`` client and
runtime sandbox handle.
"""

from __future__ import annotations

import posixpath
from typing import Any

from ..._logging import logger
from ...mcp import MCPClient
from .._sandboxed_base import SandboxedWorkspaceBase
from .._utils import (
    _agentscope_version,
    _is_released_install,
    _read_gateway_script_bytes,
    _read_glob_helper_bytes,
)
from ._bootstrap import (
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
    METADATA_WORKSPACE_ID_KEY,
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


class DaytonaWorkspace(SandboxedWorkspaceBase):
    """Workspace backed by a Daytona sandbox.

    ``default_mcps`` and ``skill_paths`` are seed-time inputs and are
    not interpreted until :meth:`initialize`, after the Daytona backend
    has been provisioned and the shared sandbox lifecycle can restore
    persisted state.
    """

    # Gateway paths are derived from Daytona SDK path APIs during
    # ``_provision_backend``; unlike Docker/E2B, they are not module
    # constants because Daytona snapshots may choose a different user
    # home.
    _glob_helper_path: str
    _gateway_home: str
    _gateway_config: str
    _gateway_log: str
    _gateway_script: str
    _gateway_python: str

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

        The sandbox is *not* started here — call :meth:`initialize`
        (or use the workspace as an ``async`` context manager).

        Args:
            workspace_id (`str | None`, optional):
                Stable identifier; also stored in Daytona labels for
                reattachment. ``None`` generates a fresh UUID.
            api_key (`str`, defaults to `""`):
                Daytona API key. ``""`` lets the SDK read credentials
                from its normal environment.
            api_url (`str`, defaults to `""`):
                Optional Daytona API URL for self-hosted deployments.
                Empty string defers to SDK defaults.
            target (`str`, defaults to `""`):
                Optional Daytona target / region. Empty string defers
                to SDK defaults.
            timeout_seconds (`int`, defaults to `DEFAULT_TIMEOUT`):
                Timeout forwarded to Daytona create/start/recover/stop
                calls.
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the in-sandbox gateway listens on.
            env (`dict[str, str] | None`, optional):
                Environment variables passed to Daytona create params
                as ``env_vars``.
            sandbox_metadata (`dict[str, str] | None`, optional):
                Extra labels merged with the workspace-id label. Useful
                for dashboard filtering by user / agent.
            extra_pip (`list[str] | None`, optional):
                Extra Python packages installed into the gateway venv
                during bootstrap.
            instructions (`str`, defaults to `_DEFAULT_INSTRUCTIONS`):
                System-prompt fragment template (supports ``{workdir}``).
            default_mcps (`list[MCPClient] | None`, optional):
                MCPs registered on first init when no persisted
                ``.mcp`` exists.
            skill_paths (`list[str] | None`, optional):
                Local skill dirs seeded into ``skills/`` on first init.
            os_user (`str | None`, optional):
                Optional Daytona OS user. ``None`` means AgentScope
                lets Daytona and the selected snapshot decide.
        """
        super().__init__(
            workspace_id=workspace_id,
            default_mcps=default_mcps,
            skill_paths=skill_paths,
        )

        # ── serializable config ─────────────────────────────────
        self.workdir = ""
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

        # ── SDK-derived paths ───────────────────────────────────
        self._user_home: str
        self._gateway_venv: str
        self._uv_bin: str
        self._dev_src_tar: str
        self._dev_src_dir: str

        # ── runtime state (Daytona-only) ────────────────────────
        self._daytona: Any = None
        self._sandbox: Any = None
        self._backend: DaytonaBackend | None = None

    # ── lifecycle hooks ─────────────────────────────────────────

    @property
    def sandbox_id(self) -> str | None:
        """Daytona sandbox id, or ``None`` if not started."""
        return self._sandbox.id if self._sandbox is not None else None

    async def _provision_backend(self) -> None:
        """Reattach or create the sandbox and bind the backend.

        First-time provisioning also runs bootstrap (tools → uv →
        gateway venv → agentscope → helper scripts). Bootstrap is
        detected by the gateway script path derived from the Daytona SDK
        and every step is idempotent so an interrupted bootstrap
        re-runs cleanly.
        """
        await self._attach_or_create_sandbox()
        await self._derive_sdk_paths()
        self._backend = DaytonaBackend(self._sandbox, workdir=self.workdir)

        # Ensure the backend default cwd exists before helper commands
        # such as ``file_exists`` run with ``cwd=self.workdir``.
        await self._backend.exec_shell(["mkdir", "-p", self.workdir], cwd="/")

        if not await self._backend.file_exists(self._gateway_script):
            await self._run_bootstrap()

    async def _teardown_backend(self) -> None:
        """Gracefully stop the sandbox and release the SDK client.

        ``sandbox.stop(force=False)`` — not force stop / archive — so
        the next :meth:`initialize` can reattach via labels and reuse
        the persistent filesystem. Errors are swallowed.
        """
        if self._sandbox is not None:
            try:
                await self._sandbox.stop(
                    timeout=self.timeout_seconds,
                    force=False,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("DaytonaWorkspace: stop failed: %s", e)
            self._sandbox = None

        if self._daytona is not None:
            try:
                await self._daytona.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("DaytonaWorkspace: client close failed: %s", e)
            self._daytona = None

    # ── instructions ────────────────────────────────────────────

    async def get_instructions(self) -> str:
        """Return the system-prompt fragment for this workspace.

        Substitutes ``{workdir}`` in the configured template with the
        SDK-reported sandbox workdir. Before initialization, a readable
        placeholder is used because Daytona paths are not known yet.
        """
        workdir = self.workdir or "<sandbox workdir>"
        return self.instructions.format(workdir=workdir)

    # ── internals: Daytona client / sandbox attach / create ─────

    async def _get_daytona_client(self) -> Any:
        """Return a per-workspace ``AsyncDaytona`` client.

        Daytona SDK clients own network resources, so the workspace
        does not share a global client with the manager or with other
        workspaces. Empty config fields are omitted so the SDK can read
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

        self._sandbox = await client.create(
            self._create_params(),
            timeout=self.timeout_seconds,
        )

    async def _find_existing_sandbox(self) -> Any:
        """Find the most recent usable Daytona sandbox for this workspace.

        Daytona may return started, stopped, paused, transitional, or
        error-state sandboxes. Candidate filtering is intentionally
        conservative: unrecoverable errors are ignored, while stopped,
        paused and pausing sandboxes can be resumed again.

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
                SandboxState.PAUSING,
                SandboxState.PAUSED,
                SandboxState.RESUMING,
            ],
        )

        candidates: list[Any] = []
        try:
            async for sandbox in client.list(query):
                candidates.append(sandbox)
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
        state = _state_value(sandbox.state)
        if state == "error":
            return bool(sandbox.recoverable)
        return state in {
            "started",
            "stopped",
            "starting",
            "stopping",
            "pausing",
            "paused",
            "resuming",
        }

    async def _ensure_existing_sandbox_ready(self) -> None:
        """Start or recover an existing sandbox when needed.

        Daytona exposes different state transitions for stopped,
        paused, pausing, stopping, starting, resuming and recoverable error
        sandboxes. This method normalizes those states into a running
        sandbox before bootstrap / gateway setup continues.
        """
        state = _state_value(self._sandbox.state)
        if state == "error":
            await self._sandbox.recover(timeout=self.timeout_seconds)
        elif state in {"stopped", "paused"}:
            await self._sandbox.start(timeout=self.timeout_seconds)
        elif state in {"stopping", "pausing"}:
            await self._sandbox.wait_for_sandbox_stop(
                timeout=self.timeout_seconds,
            )
            await self._sandbox.start(timeout=self.timeout_seconds)
        elif state in {"starting", "resuming"}:
            await self._sandbox.wait_for_sandbox_start(
                timeout=self.timeout_seconds,
            )

        await self._sandbox.refresh_data()

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
        """Derive gateway paths from Daytona SDK path APIs.

        Daytona docs show common ``/home/daytona`` examples, but the
        SDK is the source of truth. AgentScope's shared workspace layout
        derives from ``self.workdir`` via :class:`WorkspaceBase`; only
        Daytona-specific gateway/runtime paths are set here.
        """
        self.workdir = await self._sandbox.get_work_dir()
        self._user_home = await self._sandbox.get_user_home_dir()

        self._gateway_home = posixpath.join(
            self._user_home,
            GATEWAY_HOME_NAME,
        )
        self._gateway_venv = posixpath.join(
            self._gateway_home,
            GATEWAY_VENV_NAME,
        )
        self._gateway_python = posixpath.join(
            self._gateway_venv,
            "bin",
            "python",
        )
        self._gateway_script = posixpath.join(
            self._gateway_home,
            GATEWAY_SCRIPT_NAME,
        )
        self._glob_helper_path = posixpath.join(
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
        backend = self.get_backend()
        if _is_released_install():
            log_bootstrap_attempt(self.workspace_id, "released")
            install_cmd = render_install_agentscope_cmd_released(
                uv_bin=self._uv_bin,
                gateway_venv_py=self._gateway_python,
                version=_agentscope_version(),
            )
        else:
            log_bootstrap_attempt(self.workspace_id, "dev")
            tar_bytes = build_source_tarball()
            await backend.write_file(self._dev_src_tar, tar_bytes)
            install_cmd = render_install_agentscope_cmd_dev(
                uv_bin=self._uv_bin,
                gateway_venv_py=self._gateway_python,
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
            gateway_venv_py=self._gateway_python,
            uv_bin=self._uv_bin,
            extra_pip=self.extra_pip,
            install_agentscope_cmd=install_cmd,
        ):
            result = await backend.exec_shell(
                ["sh", "-c", cmd],
                timeout=600.0,
            )
            if not result.ok():
                raise RuntimeError(
                    f"DaytonaWorkspace bootstrap failed "
                    f"(exit {result.exit_code}) for: {cmd!r}\n"
                    f"stderr: {result.stderr.decode(errors='replace')}\n"
                    f"stdout: {result.stdout.decode(errors='replace')}",
                )

        await backend.write_file(
            self._glob_helper_path,
            _read_glob_helper_bytes(),
        )
        await backend.write_file(
            self._gateway_script,
            _read_gateway_script_bytes(),
        )


# ── SDK response helpers ──────────────────────────────────────────


def _state_value(state: Any) -> str:
    """Normalize SDK enum/string sandbox states."""
    value = getattr(state, "value", state)
    return "" if value is None else str(value).lower()


def _candidate_sort_key(sandbox: Any) -> str:
    """Newest-first sort key for duplicate SDK candidates."""
    return str(
        sandbox.last_activity_at
        or sandbox.updated_at
        or sandbox.created_at
        or sandbox.id,
    )
