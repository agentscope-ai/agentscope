# -*- coding: utf-8 -*-
"""OpenSandboxWorkspace -- sandboxed workspace backed by OpenSandbox."""

from __future__ import annotations

import asyncio
import importlib
from datetime import timedelta
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
    DEFAULT_IMAGE,
    BOOTSTRAP_COMMAND_TIMEOUT,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_TIMEOUT,
    DEV_SRC_TAR,
    GATEWAY_CONFIG,
    GATEWAY_HOME,
    GATEWAY_LOG,
    GATEWAY_SCRIPT,
    GATEWAY_VENV_PY,
    GLOB_HELPER_SCRIPT,
    METADATA_WORKSPACE_ID_KEY,
    SANDBOX_WORKDIR,
    bootstrap_commands,
    build_source_tarball,
    log_bootstrap_attempt,
    render_install_agentscope_cmd_dev,
    render_install_agentscope_cmd_released,
)
from ._opensandbox_backend import OpenSandboxBackend

_DEFAULT_INSTRUCTIONS = """<workspace>
You have an OpenSandbox-based workspace. All tool calls execute **inside
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


class OpenSandboxWorkspace(SandboxedWorkspaceBase):
    """Workspace backed by an OpenSandbox sandbox.

    ``default_mcps`` and ``skill_paths`` are seed-time inputs and are
    not retained as instance state past :meth:`initialize`.
    """

    _glob_helper_path = GLOB_HELPER_SCRIPT
    _gateway_home = GATEWAY_HOME
    _gateway_config = GATEWAY_CONFIG
    _gateway_log = GATEWAY_LOG
    _gateway_script = GATEWAY_SCRIPT
    _gateway_python = GATEWAY_VENV_PY

    def __init__(
        self,
        *,
        workspace_id: str | None = None,
        image: str = DEFAULT_IMAGE,
        api_key: str = "",
        domain: str = "",
        protocol: str = "http",
        request_timeout_seconds: float | None = DEFAULT_REQUEST_TIMEOUT,
        timeout_seconds: int = DEFAULT_TIMEOUT,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        env: dict[str, str] | None = None,
        sandbox_metadata: dict[str, str] | None = None,
        resource: dict[str, str] | None = None,
        entrypoint: list[str] | None = None,
        network_policy: Any | None = None,
        extra_pip: list[str] | None = None,
        instructions: str = _DEFAULT_INSTRUCTIONS,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
    ) -> None:
        """Construct an :class:`OpenSandboxWorkspace`.

        The sandbox is *not* started here — call :meth:`initialize`
        (or use the workspace as an ``async`` context manager).

        Args:
            workspace_id (`str | None`, optional):
                Stable identifier; also stored in sandbox metadata for
                reattachment.
            image (`str`, defaults to `DEFAULT_IMAGE`):
                OpenSandbox image used when creating a fresh sandbox.
            api_key (`str`, defaults to `""`):
                OpenSandbox API key (``""`` lets the SDK use its
                environment fallback).
            domain (`str`, defaults to `""`):
                Optional OpenSandbox server domain.
            protocol (`str`, defaults to `"http"`):
                Protocol forwarded to the OpenSandbox connection config.
            request_timeout_seconds (`float | None`, optional):
                SDK HTTP request timeout. ``None`` leaves the SDK
                default in effect.
            timeout_seconds (`int`, defaults to `DEFAULT_TIMEOUT`):
                Sandbox keep-alive and create/connect/resume timeout.
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the in-sandbox gateway listens on.
            env (`dict[str, str] | None`, optional):
                Environment variables baked into newly-created sandboxes.
            sandbox_metadata (`dict[str, str] | None`, optional):
                Extra metadata merged with the workspace-id tag.
            resource (`dict[str, str] | None`, optional):
                OpenSandbox resource hints for newly-created sandboxes.
            entrypoint (`list[str] | None`, optional):
                Entrypoint override for newly-created sandboxes.
            network_policy (`Any | None`, optional):
                Creation-time OpenSandbox network policy. Runtime egress
                mutation is intentionally left to a follow-up.
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
        """
        super().__init__(
            workspace_id=workspace_id,
            default_mcps=default_mcps,
            skill_paths=skill_paths,
        )
        self.workdir = SANDBOX_WORKDIR
        self.image = image
        self.api_key = api_key
        self.domain = domain
        self.protocol = protocol
        self.request_timeout_seconds = request_timeout_seconds
        self.timeout_seconds = timeout_seconds
        self.gateway_port = gateway_port
        self.env = dict(env or {})
        self.sandbox_metadata = dict(sandbox_metadata or {})
        self.resource = dict(resource or {})
        self.entrypoint = list(entrypoint or [])
        self.network_policy = network_policy
        self.extra_pip = list(extra_pip or [])
        self.instructions = instructions

        self._sandbox: Any = None
        self._backend: OpenSandboxBackend | None = None

    @property
    def sandbox_id(self) -> str | None:
        """OpenSandbox sandbox id, or ``None`` before initialize."""
        return self._sandbox.id if self._sandbox else None

    async def _provision_backend(self) -> None:
        """Reattach or create the sandbox and bind the backend.

        First-time provisioning also runs bootstrap (uv → gateway
        venv → agentscope → gateway script upload). Bootstrap is
        detected by a single ``test -e`` probe on the gateway script,
        and every step is idempotent so an interrupted bootstrap
        re-runs cleanly.
        """
        await self._attach_or_create_sandbox()
        self._backend = OpenSandboxBackend(self._sandbox, SANDBOX_WORKDIR)

        marker = await self._backend.exec_shell(
            ["test", "-e", GATEWAY_SCRIPT],
            cwd="/",
        )
        if not marker.ok():
            # The backend pins ``cwd=SANDBOX_WORKDIR`` so the very
            # first bootstrap command (a ``mkdir -p``) would fail
            # before it ran when the dir does not yet exist. Use
            # ``cwd="/"`` to break the chicken-and-egg.
            await self._backend.exec_shell(
                ["mkdir", "-p", SANDBOX_WORKDIR],
                cwd="/",
            )
            await self._run_bootstrap()

    async def _teardown_backend(self) -> None:
        """Pause the sandbox (keep filesystem) and drop the handle.

        ``sandbox.pause()`` — not ``kill()`` — so the next
        :meth:`initialize` can reattach via metadata lookup and
        resume. Errors are swallowed.
        """
        if self._sandbox is not None:
            try:
                await self._sandbox.pause()
            except Exception as exc:  # noqa: BLE001
                logger.warning("OpenSandboxWorkspace: pause failed: %s", exc)
            try:
                await self._sandbox.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "OpenSandboxWorkspace: local close failed: %s",
                    exc,
                )
            self._sandbox = None

    async def get_instructions(self) -> str:
        """Return the system-prompt fragment for this workspace.

        Substitutes ``{workdir}`` in the configured template with
        the sandbox-side path (``/workspace``). The agent always sees
        sandbox-internal paths.
        """
        return self.instructions.format(workdir=SANDBOX_WORKDIR)

    def _merged_metadata(self) -> dict[str, str]:
        """Merge caller metadata with the stable workspace id tag."""
        return {
            **self.sandbox_metadata,
            METADATA_WORKSPACE_ID_KEY: self.workspace_id,
        }

    def _connection_config(self) -> Any:
        """Build OpenSandbox connection config on demand."""
        ConnectionConfig = _import_sdk_attr(
            "opensandbox.config.connection",
            "ConnectionConfig",
        )

        kwargs: dict[str, Any] = {"protocol": self.protocol}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.domain:
            kwargs["domain"] = self.domain
        if self.request_timeout_seconds is not None:
            kwargs["request_timeout"] = timedelta(
                seconds=self.request_timeout_seconds,
            )
        return ConnectionConfig(**kwargs)

    async def _find_existing_sandbox(self) -> Any:
        """Return the most recent sandbox matching this workspace id."""
        SandboxFilter = _import_sdk_attr(
            "opensandbox.models.sandboxes",
            "SandboxFilter",
        )
        SandboxManager = _import_sdk_attr("opensandbox", "SandboxManager")

        manager = await self._create_sandbox_manager(SandboxManager)
        sandbox_filter = SandboxFilter(
            states=["RUNNING", "PAUSED"],
            metadata={METADATA_WORKSPACE_ID_KEY: self.workspace_id},
        )
        try:
            infos = await manager.list_sandbox_infos(sandbox_filter)
        finally:
            try:
                await manager.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "OpenSandboxWorkspace: manager close failed: %s",
                    exc,
                )
        candidates = self._sandbox_infos(infos)
        if not candidates:
            return None
        if len(candidates) > 1:
            logger.warning(
                "OpenSandboxWorkspace: %d sandboxes match workspace_id=%r; "
                "attaching to most recent",
                len(candidates),
                self.workspace_id,
            )
        candidates.sort(key=lambda item: item.created_at, reverse=True)
        return candidates[0]

    async def _create_sandbox_manager(self, manager_cls: Any) -> Any:
        """Create an OpenSandbox SDK manager."""
        return await manager_cls.create(
            connection_config=self._connection_config(),
        )

    @staticmethod
    def _sandbox_infos(infos: Any) -> list[Any]:
        """Normalize paged OpenSandbox SDK results into a list."""
        if infos is None:
            return []
        return list(infos.sandbox_infos)

    @staticmethod
    def _info_id(info: Any) -> str:
        """Return the sandbox id from an OpenSandbox SDK info object."""
        if not info.id:
            raise RuntimeError("OpenSandbox sandbox info has no id")
        return str(info.id)

    async def _attach_or_create_sandbox(self) -> None:
        """Reattach to an existing sandbox by metadata, or create one.

        Resolution rule: a single sandbox is expected per
        ``workspace_id``. If multiple are returned (for example a
        leaked running + paused pair after an unclean shutdown), we
        attach to the newest by ``created_at`` and log a warning;
        manual cleanup is left to the operator.

        Always blocks until the sandbox is healthy so the caller can
        issue ``commands`` / ``files`` calls without hitting transient
        "not yet routable" errors after create, connect, or resume.
        """
        existing = await self._find_existing_sandbox()
        if existing is not None:
            self._sandbox = await self._attach_existing_sandbox(existing)
        else:
            self._sandbox = await self._create_sandbox()
        await self._wait_until_running()

    async def _create_sandbox(self) -> Any:
        """Create a fresh sandbox with workspace metadata applied."""
        opensandbox = _import_opensandbox()
        Sandbox = opensandbox.Sandbox

        kwargs: dict[str, Any] = {
            "image": self.image,
            "connection_config": self._connection_config(),
            "metadata": self._merged_metadata(),
            "timeout": timedelta(seconds=self.timeout_seconds),
            "ready_timeout": timedelta(seconds=self.timeout_seconds),
        }
        if self.env:
            kwargs["env"] = self.env
        if self.resource:
            kwargs["resource"] = self.resource
        if self.entrypoint:
            kwargs["entrypoint"] = self.entrypoint
        if self.network_policy is not None:
            kwargs["network_policy"] = self.network_policy
        return await Sandbox.create(**kwargs)

    async def _resume_sandbox(self, sandbox_id: str) -> Any:
        """Resume an existing sandbox by id."""
        opensandbox = _import_opensandbox()
        Sandbox = opensandbox.Sandbox

        return await Sandbox.resume(
            sandbox_id=sandbox_id,
            connection_config=self._connection_config(),
            resume_timeout=timedelta(seconds=self.timeout_seconds),
        )

    async def _connect_sandbox(self, sandbox_id: str) -> Any:
        """Connect to an already-running sandbox by id."""
        opensandbox = _import_opensandbox()
        Sandbox = opensandbox.Sandbox

        return await Sandbox.connect(
            sandbox_id=sandbox_id,
            connection_config=self._connection_config(),
            connect_timeout=timedelta(seconds=self.timeout_seconds),
        )

    async def _attach_existing_sandbox(self, info: Any) -> Any:
        """Connect or resume depending on the OpenSandbox info state."""
        sandbox_id = self._info_id(info)
        state = self._info_state(info)
        if state == "paused":
            return await self._resume_sandbox(sandbox_id)
        if state == "running":
            return await self._connect_sandbox(sandbox_id)
        raise RuntimeError(
            f"OpenSandbox sandbox {sandbox_id!r} is not attachable "
            f"(state={state!r})",
        )

    @staticmethod
    def _info_state(info: Any) -> str:
        """Return the normalized state from an OpenSandbox SDK info object."""
        state = info.status.state
        return str(state).lower()

    async def _wait_until_running(self, timeout: float = 30.0) -> None:
        """Poll until the sandbox reports healthy.

        ``Sandbox.create`` / ``Sandbox.connect`` / ``Sandbox.resume``
        normally perform their own readiness checks, but a freshly
        created, connected, or resumed sandbox may still briefly reject
        command / filesystem calls while the service endpoint settles.
        We poll the SDK health probe, treating transient SDK errors as
        "not yet" and retrying until the timeout.

        Args:
            timeout (`float`, defaults to `30.0`):
                Hard ceiling in seconds. Raises :class:`RuntimeError`
                if the sandbox is still not healthy after this long.
        """
        if hasattr(self._sandbox, "is_running"):
            probe = self._sandbox.is_running
            probe_name = "is_running"
        elif hasattr(self._sandbox, "is_healthy"):
            probe = self._sandbox.is_healthy
            probe_name = "is_healthy"
        else:
            # The real SDK create/connect/resume calls perform readiness
            # checks before returning; older/mocked SDK shapes may not expose
            # an extra probe.
            return

        deadline = asyncio.get_event_loop().time() + timeout
        delay = 0.1
        while asyncio.get_event_loop().time() < deadline:
            try:
                if await probe():
                    return
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "OpenSandboxWorkspace: %s probe error (will retry): %s",
                    probe_name,
                    exc,
                )
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 1.0)
        raise RuntimeError(
            f"OpenSandbox sandbox did not become ready within {timeout}s "
            f"(workspace_id={self.workspace_id!r})",
        )

    async def _run_bootstrap(self) -> None:
        """Provision the sandbox runtime, then upload helper scripts."""
        if _is_released_install():
            log_bootstrap_attempt(self.workspace_id, "released")
            install_cmd = render_install_agentscope_cmd_released(
                _agentscope_version(),
            )
        else:
            log_bootstrap_attempt(self.workspace_id, "dev")
            await self._backend.write_file(DEV_SRC_TAR, build_source_tarball())
            install_cmd = render_install_agentscope_cmd_dev()

        for cmd in bootstrap_commands(
            extra_pip=self.extra_pip,
            install_agentscope_cmd=install_cmd,
        ):
            result = await self._backend.exec_shell(
                ["sh", "-c", cmd],
                timeout=BOOTSTRAP_COMMAND_TIMEOUT,
            )
            if not result.ok():
                raise RuntimeError(
                    f"OpenSandboxWorkspace bootstrap failed "
                    f"(exit {result.exit_code}) for: {cmd!r}\n"
                    f"stderr: {result.stderr.decode(errors='replace')}\n"
                    f"stdout: {result.stdout.decode(errors='replace')}",
                )

        await self._backend.write_file(
            GLOB_HELPER_SCRIPT,
            _read_glob_helper_bytes(),
        )
        await self._backend.write_file(
            GATEWAY_SCRIPT,
            _read_gateway_script_bytes(),
        )


def _import_opensandbox() -> Any:
    """Import the OpenSandbox SDK with an actionable installation hint."""
    try:
        return importlib.import_module("opensandbox")
    except ModuleNotFoundError as exc:
        if exc.name != "opensandbox":
            raise
        raise ImportError(
            "OpenSandbox SDK is required for OpenSandboxWorkspace. "
            "Install the workspace extras with "
            '`pip install "agentscope[workspace]"`.',
        ) from exc


def _import_sdk_attr(module_name: str, attr: str) -> Any:
    """Import an OpenSandbox SDK attribute with the standard install hint."""
    try:
        module = importlib.import_module(module_name)
        return getattr(module, attr)
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("opensandbox"):
            raise ImportError(
                "OpenSandbox SDK is required for OpenSandboxWorkspace. "
                "Install the workspace extras with "
                '`pip install "agentscope[workspace]"`.',
            ) from exc
        raise
