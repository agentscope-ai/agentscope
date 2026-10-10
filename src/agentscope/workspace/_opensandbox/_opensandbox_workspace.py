# -*- coding: utf-8 -*-
"""OpenSandboxWorkspace -- sandboxed workspace backed by OpenSandbox."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta
import shlex
from typing import TYPE_CHECKING, Literal

from ..._logging import logger
from ...mcp import MCPClient
from .._sandboxed_base import SandboxedWorkspaceBase
from .._gateway_client import GatewayClient
from .._utils import _GATEWAY_BASE_REQUIREMENTS, DEFAULT_WORKSPACE_INSTRUCTIONS
from ._constants import (
    DEFAULT_GATEWAY_PORT,
    BOOTSTRAP_COMMAND_TIMEOUT,
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_TIMEOUT,
    GATEWAY_HOME,
    LEGACY_METADATA_WORKSPACE_ID_KEY,
    METADATA_WORKSPACE_ID_KEY,
    SANDBOX_WORKDIR,
)
from ._opensandbox_backend import OpenSandboxBackend
from ._template import PREPARED_STATE_FILE, prepared_template_state

if TYPE_CHECKING:
    from opensandbox import Sandbox
    from opensandbox.config.connection import ConnectionConfig
    from opensandbox.models.sandboxes import (
        NetworkPolicy,
        SandboxInfo,
    )


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
        template_id: str | None = None,
        image: str | None = None,
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
        instructions: str = DEFAULT_WORKSPACE_INSTRUCTIONS,
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
            template_id (`str | None`, optional):
                Succeeded FastSandbox template. Defaults to the
                ``OPENSANDBOX_TEMPLATE_ID`` environment variable unless
                ``image`` explicitly selects image-based creation.
            image (`str | None`, optional):
                Explicit image-based creation for legacy deployments.
                Mutually exclusive with ``template_id``. By default,
                workspace creation requires a FastSandbox template.
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
            instructions (`str`, defaults to `DEFAULT_WORKSPACE_INSTRUCTIONS`):
                Instructions that will be injected into the system prompt,
                which should receive placeholders "{workdir}".
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
        self.template_id = (
            template_id
            if template_id is not None or image is not None
            else os.getenv("OPENSANDBOX_TEMPLATE_ID")
        )
        if self.template_id is not None:
            self.template_id = self.template_id.strip()
            if not self.template_id:
                raise ValueError("template_id must not be blank")
        if self.template_id and image is not None:
            raise ValueError("template_id and image are mutually exclusive")
        if not self.template_id and not image:
            raise ValueError(
                "FastSandbox requires template_id or OPENSANDBOX_TEMPLATE_ID; "
                "pass image explicitly for image-based creation",
            )
        if self.template_id and (env or resource or entrypoint):
            raise ValueError(
                "FastSandbox templates fix env, resource and entrypoint; "
                "configure these when building the template",
            )
        self.image = image
        self._workspace_metadata_key = (
            METADATA_WORKSPACE_ID_KEY
            if self.template_id
            else LEGACY_METADATA_WORKSPACE_ID_KEY
        )
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
        self._fresh_template_sandbox = False

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
        self._fresh_template_sandbox = False
        existing = await self._find_existing_sandbox()
        if existing is not None:
            self._sandbox = await self._attach_existing_sandbox(existing)
        else:
            self._sandbox = await self._create_sandbox()
            self._fresh_template_sandbox = bool(self.template_id)
        await self._wait_until_running()

        self._backend = OpenSandboxBackend(self._sandbox, SANDBOX_WORKDIR)

    async def _initialize_prepared_workspace(self) -> bool:
        """Reuse an empty gateway only on a freshly created template VM.

        Existing workspaces need persisted MCP restoration and a fresh
        registry. Templates without this contract retain normal bootstrap.
        """
        if not self._fresh_template_sandbox or self.extra_pip:
            return False
        backend = self.get_backend()
        try:
            state = json.loads(await backend.read_file(PREPARED_STATE_FILE))
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            return False
        if state != prepared_template_state(self.gateway_port):
            return False

        gateway = GatewayClient(
            backend=backend,
            gateway_port=self.gateway_port,
            timeout=30.0,
            gateway_log_path=self._gateway_log,
        )
        if not await gateway.health():
            await gateway.aclose()
            return False
        self._gateway = gateway
        self._mcp_specs = {}
        await self._setup_skills()
        return True

    async def _teardown_backend(self) -> None:
        """Pause the sandbox (keep filesystem) and drop the handle.

        Wait for ``Paused`` after ``sandbox.pause()`` so the next
        :meth:`initialize` can reattach via metadata lookup and
        resume. Errors are swallowed.
        """
        if self._sandbox is not None:
            try:
                await self._sandbox.pause()
                await self._wait_until_paused(self._sandbox.id)
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
        return self.instructions.format(
            backend="OpenSandbox",
            workdir=self.workdir,
        )

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
            states=["Running", "Pausing", "Paused"],
            metadata={self._workspace_metadata_key: self.workspace_id},
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
            "connection_config": self._connection_config(),
            "metadata": {
                **self.sandbox_metadata,
                self._workspace_metadata_key: self.workspace_id,
            },
            "timeout": timedelta(seconds=self.timeout_seconds),
            "ready_timeout": timedelta(seconds=self.timeout_seconds),
        }
        if self.template_id:
            create = getattr(Sandbox, "create_from_template", None)
            if create is None:
                raise RuntimeError(
                    "Install an OpenSandbox SDK with create_from_template "
                    "support to use FastSandbox workspaces",
                )
            return await create(
                template_id=self.template_id,
                network_policy=self.network_policy,
                **kwargs,
            )
        kwargs["image"] = self.image
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

        if state == "pausing":
            await self._wait_until_paused(info.id)
            state = "paused"

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

    async def _wait_until_paused(self, sandbox_id: str) -> None:
        """Wait for the durable checkpoint before allowing reattachment."""
        from opensandbox import SandboxManager

        manager = await SandboxManager.create(self._connection_config())
        deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        try:
            while asyncio.get_running_loop().time() < deadline:
                info = await manager.get_sandbox_info(sandbox_id)
                state = info.status.state.lower()
                if state == "paused":
                    return
                if state in {"failed", "terminated", "stopping"}:
                    raise RuntimeError(
                        f"Sandbox {sandbox_id!r} cannot pause "
                        f"(state={state!r})",
                    )
                await asyncio.sleep(0.5)
            raise TimeoutError(
                f"Sandbox {sandbox_id!r} did not reach Paused within "
                f"{self.timeout_seconds}s",
            )
        finally:
            await manager.close()

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
            # System packages used by bootstrap and builtin tools. The
            # default image runs as root, so no sudo is needed. ``ripgrep``
            # backs the Grep tool.
            "if command -v apk >/dev/null 2>&1; then "
            "apk add --no-cache bash curl ca-certificates ripgrep "
            "python3 procps "
            "&& ln -sf /usr/bin/python3 /usr/local/bin/python; "
            "elif command -v apt-get >/dev/null 2>&1; then "
            "apt-get update -qq "
            "&& apt-get install -y --no-install-recommends curl "
            "ca-certificates ripgrep procps "
            "&& rm -rf /var/lib/apt/lists/*; "
            "else echo 'Workspace bootstrap requires apk or apt-get' >&2; "
            "exit 1; fi",
            # Astral uv → /usr/local/bin (on PATH). INSTALLER_NO_MODIFY_PATH
            # suppresses shell rc edits.
            "curl -LsSf https://astral.sh/uv/install.sh "
            "| env UV_INSTALL_DIR=/usr/local/bin "
            "INSTALLER_NO_MODIFY_PATH=1 sh",
            # Gateway venv + base requirements + agentscope from PyPI.
            # ``uv venv`` creates the gateway home as a parent dir.
            f"uv venv {self._gateway_venv}",
            f"uv pip install --python {self._gateway_python} {pip_args}",
            f"uv pip install --python {self._gateway_python} "
            f"--no-deps 'agentscope'",
        ]
