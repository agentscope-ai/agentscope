# -*- coding: utf-8 -*-
"""OpenSandboxWorkspace -- sandboxed workspace backed by OpenSandbox."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import os
import shlex
from typing import TYPE_CHECKING, Literal

from ..._logging import logger
from ...mcp import MCPClient
from .._sandboxed_base import SandboxedWorkspaceBase
from .._utils import _GATEWAY_BASE_REQUIREMENTS, DEFAULT_WORKSPACE_INSTRUCTIONS
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


# ── Gateway proxy-direct 开关（AGENTSCOPE_GATEWAY_PROXY_DIRECT）────────
# 默认开启（"1"）：沙箱 gateway 以 --host 0.0.0.0 启动，GatewayClient 改走
# OpenSandbox server-proxy 直连（跳过 exec_shell shim 的 spawn 开销，
# 工具调用 ~1s → ~0.5s）。仅 OpenSandbox 沙箱模式生效（本类 override
# _gateway_proxy_url 才返回非 None）；Docker/E2B 等无 server-proxy 的
# 沙箱不受影响，保持 loopback + shim。传输层故障自动 fallback 回 shim。
# 关闭（"0"/"false"/"no"/"off"）：gateway 维持 127.0.0.1 绑定，走原
# exec_shell shim 通道，行为与官方一致。
AGENTSCOPE_GATEWAY_PROXY_DIRECT_ENABLED = os.getenv(
    "AGENTSCOPE_GATEWAY_PROXY_DIRECT", "1"
).strip().lower() not in ("0", "false", "no", "off")


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
        instructions: str = DEFAULT_WORKSPACE_INSTRUCTIONS,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        skip_system_bootstrap: bool = False,
        pypi_index_url: str | None = None,
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
            instructions (`str`, defaults to `DEFAULT_WORKSPACE_INSTRUCTIONS`):
                Instructions that will be injected into the system prompt,
                which should receive placeholders "{workdir}".
            default_mcps (`list[MCPClient] | None`, optional):
                MCPs registered on first init when no persisted
                ``.mcp`` exists.
            skill_paths (`list[str] | None`, optional):
                Local skill dirs seeded into ``skills/`` on first init.
            skip_system_bootstrap (`bool`, defaults to ``False``):
                When ``True``, skip the ``apt-get`` and ``uv`` installation
                steps during bootstrap. Use this with a pre-built image that
                already has ``curl``, ``ripgrep``, and ``uv`` installed to
                speed up workspace initialization.
            pypi_index_url (`str | None`, defaults to ``None``):
                PyPI index URL for ``uv pip install`` during bootstrap.
                Set to a mirror URL (e.g. ``https://mirrors.aliyun.com/pypi/simple/``)
                to accelerate package downloads in China. ``None`` uses the
                default PyPI registry.
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
        self.skip_system_bootstrap = skip_system_bootstrap
        self._pypi_index_url = pypi_index_url

        self._sandbox: Sandbox | None = None
        self._backend: OpenSandboxBackend | None = None

    @property
    def sandbox_id(self) -> str | None:
        """OpenSandbox sandbox id, or ``None`` before initialize."""
        return self._sandbox.id if self._sandbox else None

    @property
    def _gateway_python(self) -> str:
        """Sandbox-side path of the gateway python interpreter.
        
        When skip_system_bootstrap=True, use system python instead of venv.
        """
        if self.skip_system_bootstrap:
            return "/usr/local/bin/python"
        return self.get_backend().join_path(
            self._gateway_venv,
            "bin",
            "python",
        )

    async def _gateway_proxy_url(self) -> tuple[str, dict[str, str]] | None:
        """OpenSandbox server-proxy direct transport (default on).

        Only active while a sandbox exists (i.e. sandbox mode) and the
        ``AGENTSCOPE_GATEWAY_PROXY_DIRECT`` switch is enabled: returns
        the server-proxy ``(base_url, headers)`` for the gateway port.
        ``_sandboxed_base`` then launches the gateway with
        ``--host 0.0.0.0`` and wires :class:`GatewayClient` to the
        proxy route (with automatic fallback to the in-sandbox shim).

        Returns ``None`` — keeping loopback binding + shim transport —
        when the switch is off, no sandbox is attached, or the SDK
        cannot produce an endpoint (provider-side limitation).
        """
        if not AGENTSCOPE_GATEWAY_PROXY_DIRECT_ENABLED:
            return None
        if self._sandbox is None:
            return None
        try:
            endpoint = await self._sandbox.get_endpoint(self.gateway_port)
        except Exception as exc:
            logger.warning(
                "OpenSandboxWorkspace: get_endpoint(%s) failed (%s); "
                "falling back to the in-sandbox shim transport.",
                self.gateway_port,
                exc,
            )
            return None
        url = str(endpoint.endpoint or "").rstrip("/")
        if not url:
            return None
        # SDK endpoint may be a bare host:port (no scheme); httpx requires
        # an explicit http:// prefix for the proxy-direct transport.
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        return url, dict(endpoint.headers or {})

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
        # 2026-08-04: 强制走 opensandbox-server 的 server-proxy 路由。
        # SDK 默认 use_server_proxy=False 会返回 127.0.0.1:<docker-proxy 动态端口>，
        # 沙箱重建后旧 URL 失效（实测 56498 漂移问题），且容器需 --network host 才能路由。
        # 开启后返回 172.19.124.30:8101/v1/sandboxes/.../proxy/<port> 固定可路由地址，
        # 容器可脱离 host 网络、走普通 bridge。
        kwargs["use_server_proxy"] = True
        return ConnectionConfig(**kwargs)

    def _docker_safe_workspace_id(self) -> str:
        """Return a Docker-label-safe workspace identifier (≤ 63 chars).

        Docker metadata labels must be ≤ 63 characters and match
        ``[a-zA-Z0-9]([a-zA-Z0-9_.-]*[a-zA-Z0-9])?``.  When the
        raw ``workspace_id`` exceeds 62 characters, we hash it to a
        deterministic 16-character BLAKE2b hex digest.
        """
        if len(self.workspace_id) <= 62:
            return self.workspace_id
        h = hashlib.blake2b(self.workspace_id.encode(), digest_size=8)
        return h.hexdigest()

    async def _find_existing_sandbox(self) -> SandboxInfo | None:
        """Return the most recent sandbox matching this workspace id."""
        from opensandbox.models.sandboxes import SandboxFilter, SandboxState
        from opensandbox import SandboxManager

        manager = await SandboxManager.create(
            connection_config=self._connection_config(),
        )
        sandbox_filter = SandboxFilter(
            states=[SandboxState.RUNNING, SandboxState.PAUSED],
            metadata={
                METADATA_WORKSPACE_ID_KEY: self._docker_safe_workspace_id(),
            },
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
                METADATA_WORKSPACE_ID_KEY: self._docker_safe_workspace_id(),
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
        sandbox = await Sandbox.create(**kwargs)
        logger.info(
            "Sandbox created: id=%s, execd_url=%s",
            sandbox.id,
            getattr(sandbox, "execd_url", "N/A"),
        )
        return sandbox

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
        # If using a pre-built image with all dependencies already installed,
        # skip all bootstrap commands. The gateway script will be uploaded
        # directly by the base class.
        if self.skip_system_bootstrap:
            logger.info(
                "OpenSandboxWorkspace: skip_system_bootstrap=True, "
                "skipping all bootstrap commands (assuming pre-built image)"
            )
            return []

        pip_pkgs = list(_GATEWAY_BASE_REQUIREMENTS) + list(self.extra_pip)
        # Quote every requirement so entries with spaces or shell
        # metacharacters cannot break ``sh -c`` or inject inside the sandbox.
        pip_args = " ".join(shlex.quote(p) for p in pip_pkgs)

        # Build uv pip install command with optional PyPI index URL for
        # Chinese mirror support.
        pypi_index = ""
        if self._pypi_index_url:
            pypi_index = f" --index-url {self._pypi_index_url}"

        return [
            # Replace Debian sources with Aliyun mirrors (China network
            # optimization), then install system packages used by bootstrap
            # and builtin tools. Falls back silently if Debian sources files
            # use non-standard paths.
            "sed -i 's|deb.debian.org|mirrors.aliyun.com|g' "
            "/etc/apt/sources.list.d/*.sources 2>/dev/null || true; "
            "sed -i 's|http://deb.debian.org|http://mirrors.aliyun.com|g' "
            "/etc/apt/sources.list 2>/dev/null || true; "
            "apt-get update -qq "
            "&& apt-get install -y --no-install-recommends curl "
            "ca-certificates ripgrep "
            "&& rm -rf /var/lib/apt/lists/*",
            # uv → prefer pre-installed uv (CN images ship it); otherwise
            # install from Aliyun PyPI mirror. astral.sh has no CN mirror.
            "command -v uv >/dev/null 2>&1 || "
            "python3 -m pip install --break-system-packages -q "
            f"-i {self._pypi_index_url or 'https://mirrors.aliyun.com/pypi/simple/'} uv",
            # Gateway venv + base requirements + agentscope from PyPI.
            # ``uv venv`` creates the gateway home as a parent dir.
            f"uv venv {self._gateway_venv}",
            f"uv pip install{pypi_index} --python {self._gateway_python} {pip_args}",
            f"uv pip install{pypi_index} --python {self._gateway_python} "
            f"--no-deps 'agentscope'",
        ]
