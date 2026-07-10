# -*- coding: utf-8 -*-
"""OpenSandboxWorkspace -- sandboxed workspace backed by OpenSandbox."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import shlex
from typing import TYPE_CHECKING, Literal

from ..._logging import logger
from ...mcp import MCPClient
from .._sandboxed_base import SandboxedWorkspaceBase
from .._utils import _GATEWAY_BASE_REQUIREMENTS
from ._constants import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_IMAGE,
    BOOTSTRAP_COMMAND_TIMEOUT,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_TIMEOUT,
    GATEWAY_HOME,
    METADATA_WORKSPACE_ID_KEY,
    SANDBOX_WORKDIR,
)
from ._opensandbox_backend import OpenSandboxBackend

if TYPE_CHECKING:
    from opensandbox import Sandbox
    from opensandbox.config.connection import ConnectionConfig
    from opensandbox.models.sandboxes import (
        NetworkPolicy,
        SandboxInfo,
    )


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
</workspace>"""


class OpenSandboxWorkspace(SandboxedWorkspaceBase):
    """Workspace backed by an OpenSandbox sandbox.

    ``default_mcps`` and ``skill_paths`` are seed-time inputs and are
    not retained as instance state past :meth:`initialize`.
    """

    _gateway_home = GATEWAY_HOME
    # The slim base image streams apt-get + uv + pip for several
    # minutes on first bootstrap, so cap each bootstrap command at the
    # same budget the SDK HTTP layer is configured for.
    _bootstrap_cmd_timeout = BOOTSTRAP_COMMAND_TIMEOUT

    def __init__(
        self,
        *,
        workspace_id: str | None = None,
        image: str = DEFAULT_IMAGE,
        api_key: str = "",
        domain: str = "",
        protocol: Literal["http", "https"] = "http",
        request_timeout_seconds: float | None = DEFAULT_REQUEST_TIMEOUT,
        timeout_seconds: int = DEFAULT_TIMEOUT,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        env: dict[str, str] | None = None,
        sandbox_metadata: dict[str, str] | None = None,
        resource: dict[str, str] | None = None,
        entrypoint: list[str] | None = None,
        network_policy: NetworkPolicy | None = None,
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
                Protocol to use (http/https)
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
            network_policy (`NetworkPolicy | None`, optional):
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

        self._sandbox: Sandbox | None = None
        self._backend: OpenSandboxBackend | None = None

    @property
    def sandbox_id(self) -> str | None:
        """OpenSandbox sandbox id, or ``None`` before initialize."""
        return self._sandbox.id if self._sandbox else None

    async def _provision_backend(self) -> None:
        """Reattach or create the sandbox and bind the backend.

        First-time bootstrap (uv → gateway venv → agentscope → gateway
        script upload) is driven by
        :meth:`SandboxedWorkspaceBase._setup_mcp_gateway` once
        ``initialize`` has bound the backend and created the workspace
        layout (which lays down ``workdir`` / ``_gateway_home`` first),
        so this hook only has to attach or create the sandbox. Every
        bootstrap step is idempotent, so an interrupted bootstrap
        re-runs cleanly on the next ``initialize``.
        """
        existing = await self._find_existing_sandbox()
        if existing is not None:
            self._sandbox = await self._attach_existing_sandbox(existing)
        else:
            self._sandbox = await self._create_sandbox()
        await self._wait_until_running()

        self._backend = OpenSandboxBackend(self._sandbox, SANDBOX_WORKDIR)

    async def _teardown_backend(self) -> None:
        """Pause the sandbox (keep filesystem) and drop the handle.

        ``sandbox.pause()`` — not ``kill()`` — so the next
        :meth:`initialize` can reattach via metadata lookup and
        resume. Errors are swallowed.
        """
        if self._sandbox is not None:
            try:
                await self._sandbox.pause()
            except Exception as exc:
                logger.warning("OpenSandboxWorkspace: pause failed: %s", exc)
            try:
                await self._sandbox.close()
            except Exception as exc:
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

    def _connection_config(self) -> ConnectionConfig:
        """Build OpenSandbox connection config on demand."""
        from opensandbox.config.connection import ConnectionConfig

        kwargs: dict = {"protocol": self.protocol}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.domain:
            kwargs["domain"] = self.domain
        if self.request_timeout_seconds is not None:
            kwargs["request_timeout"] = timedelta(
                seconds=self.request_timeout_seconds,
            )
        return ConnectionConfig(**kwargs)

    async def _find_existing_sandbox(self) -> SandboxInfo | None:
        """Return the most recent sandbox matching this workspace id."""
        from opensandbox.models.sandboxes import SandboxFilter
        from opensandbox import SandboxManager

        manager = await SandboxManager.create(
            connection_config=self._connection_config(),
        )
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
        candidates = infos.sandbox_infos
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

    async def _create_sandbox(self) -> Sandbox:
        """Create a fresh sandbox with workspace metadata applied."""
        from opensandbox import Sandbox

        kwargs: dict = {
            "image": self.image,
            "connection_config": self._connection_config(),
            "metadata": {
                **self.sandbox_metadata,
                METADATA_WORKSPACE_ID_KEY: self.workspace_id,
            },
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

    async def _attach_existing_sandbox(self, info: SandboxInfo) -> Sandbox:
        """Connect or resume depending on the OpenSandbox info state."""
        from opensandbox import Sandbox

        state = info.status.state.lower()

        if state == "paused":
            return await Sandbox.resume(
                sandbox_id=info.id,
                connection_config=self._connection_config(),
                resume_timeout=timedelta(seconds=self.timeout_seconds),
            )

        if state == "running":
            return await Sandbox.connect(
                sandbox_id=info.id,
                connection_config=self._connection_config(),
                connect_timeout=timedelta(seconds=self.timeout_seconds),
            )

        raise RuntimeError(
            f"OpenSandbox sandbox {info.id!r} is not attachable "
            f"(state={state!r})",
        )

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
            except Exception as exc:
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

    def _bootstrap_commands(self) -> list[str]:
        """Return the provisioning shell command sequence.

        Called once by :meth:`SandboxedWorkspaceBase._setup_mcp_gateway`
        when the gateway script is missing (fresh sandbox, or a prior
        bootstrap that was interrupted before the script was written).
        The base class runs each command with
        :attr:`_bootstrap_cmd_timeout` and then uploads the glob helper
        and gateway script itself, so this hook only builds the command
        list.

        The workspace layout (``data/``, ``skills/``, ``sessions/``,
        gateway home) is created by the base class
        :meth:`_ensure_workspace_layout` before bootstrap runs, so
        bootstrap only installs the runtime. ``uv`` lands at
        ``/usr/local/bin`` (on the default PATH, root needs no sudo) and
        is invoked bare, matching K8s/E2B.

        Returns:
            A list of shell command strings, to be executed in order. Each
            must exit 0; a non-zero exit aborts bootstrap.
        """
        pip_pkgs = list(_GATEWAY_BASE_REQUIREMENTS) + list(self.extra_pip)
        # Quote every requirement so entries with spaces or shell
        # metacharacters cannot break ``sh -c`` or inject inside the sandbox.
        pip_args = " ".join(shlex.quote(p) for p in pip_pkgs)

        return [
            # 1. System packages used by bootstrap and builtin tools. The
            # default image runs as root, so no sudo is needed. ``procps``
            # backs gateway-process cleanup; ``ripgrep`` backs the Grep tool.
            "apt-get update -qq "
            "&& apt-get install -y --no-install-recommends curl "
            "ca-certificates procps ripgrep "
            "&& rm -rf /var/lib/apt/lists/*",
            # 2. Astral uv → /usr/local/bin (on PATH). INSTALLER_NO_MODIFY_PATH
            # suppresses shell rc edits.
            "curl -LsSf https://astral.sh/uv/install.sh "
            "| env UV_INSTALL_DIR=/usr/local/bin "
            "INSTALLER_NO_MODIFY_PATH=1 sh",
            # 3. Gateway venv + base requirements + agentscope from PyPI.
            # ``uv venv`` creates the gateway home as a parent dir.
            f"uv venv {self._gateway_venv}",
            f"uv pip install --python {self._gateway_python} {pip_args}",
            f"uv pip install --python {self._gateway_python} "
            f"--no-deps 'agentscope'",
        ]
