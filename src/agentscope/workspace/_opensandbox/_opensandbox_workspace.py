# -*- coding: utf-8 -*-
"""OpenSandboxWorkspace -- sandboxed workspace backed by OpenSandbox."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import mimetypes
import os
import posixpath
import shlex
import uuid
from copy import deepcopy
from datetime import timedelta
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
from .._gateway_client import GatewayClient
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
    GATEWAY_PID,
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
from ._opensandbox_backend import OpenSandboxBackend

_DEFAULT_INSTRUCTIONS = """<workspace>
You have an OpenSandbox-based workspace. All tool calls execute **inside
the sandbox** at ``{workdir}``.
</workspace>"""


class OpenSandboxWorkspace(WorkspaceBase):
    """Workspace backed by an OpenSandbox sandbox."""

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
        """Construct an :class:`OpenSandboxWorkspace` lifecycle shell."""
        super().__init__(workspace_id=workspace_id)
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
        self.default_mcps = list(default_mcps or [])
        self.skill_paths = list(skill_paths or [])

        self._sandbox: Any = None
        self._backend: OpenSandboxBackend | None = None
        self._gateway: Any = None
        self._gateway_token = ""
        self._mcps: list[MCPClient] = []
        self._gateway_clients: dict[str, Any] = {}
        self._mcp_lock = asyncio.Lock()
        self._skill_lock = asyncio.Lock()

    @property
    def sandbox_id(self) -> str | None:
        """OpenSandbox sandbox id, or ``None`` before initialize."""
        return self._sandbox.id if self._sandbox else None

    async def initialize(self) -> None:
        """Reattach or create the sandbox, then start the gateway.

        Steps:

        1. Look up an existing sandbox via ``SandboxManager`` filtered
           by ``workspace_id`` metadata. If found, connect to it when
           it is running or resume it when it is paused.
        2. If not found, ``Sandbox.create(...)`` provisions a fresh
           sandbox tagged with our metadata and configured image /
           lifecycle parameters.
        3. Wait until the sandbox reports healthy before binding the
           backend and issuing ``commands`` / ``files`` calls.
        4. Run bootstrap if the gateway script is missing, restore MCPs
           from ``$workdir/.mcp`` if present, else seed
           ``default_mcps``.
        5. Mint a fresh gateway bearer token, stop any stale gateway
           process from an earlier resume cycle, write the gateway
           config, launch the gateway, and wait for the authenticated
           MCP endpoint.
        6. Pull the gateway-side MCP view back as
           :class:`GatewayMCPClient` instances, persist the MCP set, and
           seed skills if needed.

        Idempotent: a no-op when already alive.
        """
        if self.is_alive:
            return
        await self._attach_or_create_sandbox()
        self._backend = OpenSandboxBackend(self._sandbox, SANDBOX_WORKDIR)
        await self._start_gateway_stack()
        self.is_alive = True

    async def close(self) -> None:
        """Pause the sandbox and release host-side handles."""
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
            self._backend = None
        self.is_alive = False

    async def get_instructions(self) -> str:
        """Return the workspace system-prompt fragment."""
        return self.instructions.format(workdir=SANDBOX_WORKDIR)

    async def list_tools(self) -> list[ToolBase]:
        """Built-in tools backed by the OpenSandbox sandbox."""
        if self._backend is None:
            raise RuntimeError(
                "OpenSandboxWorkspace is not initialized: its sandbox "
                "backend is unavailable. Use 'async with workspace:' or "
                "call 'await workspace.initialize()' before 'list_tools()'.",
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
        """Return currently attached gateway MCP handles."""
        return list(self._gateway_clients.values())

    async def list_skills(self) -> list[Skill]:
        """Enumerate skills by scanning ``skills/`` inside the sandbox."""
        import frontmatter as fm

        assert self._backend is not None
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
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to load skill %s: %s", md_path, exc)
        return skills

    async def add_mcp(self, mcp_client: MCPClient) -> None:
        """Register a new MCP server on the in-sandbox gateway."""
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
        """Unregister an MCP server by name."""
        async with self._mcp_lock:
            gw_client = self._gateway_clients.pop(name, None)
            if gw_client is None:
                logger.warning("MCP %r not found in workspace", name)
                return
            try:
                await gw_client.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("MCP %r close failed: %s", name, exc)
            self._mcps = [m for m in self._mcps if m.name != name]
            await self._save_mcp_file()

    async def add_skill(self, skill_path: str) -> None:
        """Upload a local skill directory into ``skills/``."""
        skill_md = os.path.join(skill_path, "SKILL.md")
        if not os.path.isfile(skill_md):
            raise ValueError(
                f"Invalid skill at {skill_path!r}: SKILL.md not found",
            )

        assert self._backend is not None
        async with self._skill_lock:
            await self._backend.exec_shell(["mkdir", "-p", SANDBOX_SKILLS_DIR])
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
                    with open(local, "rb") as handle:
                        data = handle.read()
                    await self._backend.write_file(remote, data)

            logger.info(
                "OpenSandboxWorkspace: added skill %r at %s/%s",
                dir_name,
                SANDBOX_SKILLS_DIR,
                dir_name,
            )

    async def remove_skill(self, name: str) -> None:
        """Delete a skill directory by its agent-facing name."""
        skills = await self.list_skills()
        target_dir: str | None = None
        for skill in skills:
            if skill.name == name:
                target_dir = skill.dir
                break
        if target_dir is None:
            available = [skill.name for skill in skills]
            raise KeyError(
                f"Skill {name!r} not found. Available: {available}",
            )
        assert self._backend is not None
        await self._backend.delete_path(target_dir)

    async def offload_context(self, session_id: str, msgs: list[Msg]) -> str:
        """Persist a batch of messages as JSONL inside the sandbox."""
        assert self._backend is not None
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
        assert self._backend is not None
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
                        offloaded = await self._offload_data_block(block)
                        url = str(offloaded.source.url)
                    else:
                        url = str(block.source.url)
                    parts.append(
                        f"<data url='{url}' name='{block.name}' "
                        f"media_type='{block.source.media_type}'/>",
                    )

        await self._backend.exec_shell(["mkdir", "-p", base])
        await self._backend.write_file(path, "".join(parts).encode("utf-8"))
        return path

    async def reset(self) -> None:
        """Return the workspace to an empty state."""
        if self._backend is None:
            raise RuntimeError(
                "OpenSandboxWorkspace is not initialized: its sandbox "
                "backend is unavailable. Use 'async with workspace:' or "
                "call 'await workspace.initialize()' before 'reset()'.",
            )

        async with self._mcp_lock, self._skill_lock:
            for gw_client in list(self._gateway_clients.values()):
                try:
                    await gw_client.close()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "MCP %r close failed during reset: %s",
                        gw_client.name,
                        exc,
                    )
            self._gateway_clients.clear()
            self._mcps = []

            for path in (
                SANDBOX_SESSIONS_DIR,
                SANDBOX_DATA_DIR,
                SANDBOX_SKILLS_DIR,
            ):
                await self._backend.delete_path(path)

            await self._save_mcp_file()

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

    async def _start_gateway_stack(self) -> None:
        """Bootstrap, start, and attach to the in-sandbox MCP gateway."""
        marker = await self._backend.exec_shell(
            ["test", "-e", GATEWAY_SCRIPT],
            cwd="/",
        )
        if not marker.ok():
            # The backend defaults to ``SANDBOX_WORKDIR``. On a fresh
            # sandbox that directory may not exist yet, so create it
            # from ``/`` before running the idempotent bootstrap steps.
            await self._backend.exec_shell(
                ["mkdir", "-p", SANDBOX_WORKDIR],
                cwd="/",
            )
            await self._run_bootstrap()

        self._mcps = await self._restore_or_seed_mcps()
        self._gateway_token = uuid.uuid4().hex

        # Each initialization mints a new bearer token. A gateway left
        # over from a previous resume cycle would reject new-token
        # requests, so stop it before writing the fresh config and
        # launching the new process.
        await self._stop_gateway_process()
        await self._write_gateway_config()
        await self._start_gateway_process()

        endpoint = await self._sandbox.get_endpoint(self.gateway_port)
        self._gateway = GatewayClient(
            base_url=self._endpoint_url(endpoint),
            token=self._gateway_token,
            timeout=30.0,
            extra_headers=self._endpoint_headers(endpoint),
        )
        await self._wait_for_gateway()
        self._gateway_clients = {
            client.name: client for client in await self._gateway.list_mcps()
        }

        # Persist the MCP set unconditionally so seeded defaults become
        # the canonical sandbox-side ``.mcp`` file for the next restart.
        # ``_seed_skills`` is also idempotent: it returns when
        # ``skills/`` already contains entries.
        await self._save_mcp_file()
        await self._seed_skills()

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

    async def _restore_or_seed_mcps(self) -> list[MCPClient]:
        """Load persisted MCP specs, or fall back to default seeds."""
        if not await self._backend.file_exists(SANDBOX_MCP_FILE):
            return list(self.default_mcps)
        raw = await self._backend.read_file(SANDBOX_MCP_FILE)
        try:
            data = json.loads(raw.decode("utf-8"))
            return [MCPClient.model_validate(mcp) for mcp in data]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "OpenSandboxWorkspace: failed to parse %s, falling back "
                "to default_mcps: %s",
                SANDBOX_MCP_FILE,
                exc,
            )
            return list(self.default_mcps)

    async def _save_mcp_file(self) -> None:
        """Persist the current MCP set inside the sandbox."""
        payload = json.dumps(
            [mcp.model_dump(mode="json") for mcp in self._mcps],
            indent=2,
            ensure_ascii=False,
        )
        try:
            await self._backend.exec_shell(["mkdir", "-p", SANDBOX_WORKDIR])
            await self._backend.write_file(
                SANDBOX_MCP_FILE,
                payload.encode("utf-8"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "OpenSandboxWorkspace: failed to save %s: %s",
                SANDBOX_MCP_FILE,
                exc,
            )

    async def _write_gateway_config(self) -> None:
        """Write the gateway config consumed by the in-sandbox app."""
        cfg = {
            "token": self._gateway_token,
            "servers": [mcp.model_dump(mode="json") for mcp in self._mcps],
        }
        await self._backend.exec_shell(["mkdir", "-p", GATEWAY_HOME])
        await self._backend.write_file(
            GATEWAY_CONFIG,
            json.dumps(cfg, indent=2, ensure_ascii=False).encode("utf-8"),
        )

    async def _start_gateway_process(self) -> None:
        """Launch the gateway in the sandbox as a detached process."""
        cmd = (
            f"nohup {shlex.quote(GATEWAY_VENV_PY)} -u "
            f"{shlex.quote(GATEWAY_SCRIPT)} "
            f"--config {shlex.quote(GATEWAY_CONFIG)} "
            f"--port {self.gateway_port} "
            f"> {shlex.quote(GATEWAY_LOG)} 2>&1 & "
            f"echo $! > {shlex.quote(GATEWAY_PID)}"
        )
        await self._backend.exec_shell(["sh", "-c", cmd])

    async def _stop_gateway_process(self) -> None:
        """Stop any previous in-sandbox gateway before starting a new one."""
        cmd = (
            f"if [ -f {shlex.quote(GATEWAY_PID)} ]; then "
            f"pid=$(cat {shlex.quote(GATEWAY_PID)} 2>/dev/null || true); "
            'if [ -n "$pid" ]; then '
            'kill -TERM "$pid" 2>/dev/null || true; '
            "i=0; "
            "while [ $i -lt 50 ]; do "
            'kill -0 "$pid" 2>/dev/null || break; '
            "sleep 0.1; "
            "i=$((i + 1)); "
            "done; "
            'kill -0 "$pid" 2>/dev/null && '
            'kill -KILL "$pid" 2>/dev/null || true; '
            "i=0; "
            "while [ $i -lt 50 ]; do "
            'kill -0 "$pid" 2>/dev/null || break; '
            "sleep 0.1; "
            "i=$((i + 1)); "
            "done; "
            "fi; "
            f"rm -f {shlex.quote(GATEWAY_PID)}; "
            "fi"
        )
        await self._backend.exec_shell(["sh", "-c", cmd])

    async def _wait_for_gateway(self, timeout: float = 30.0) -> None:
        """Poll until the gateway becomes healthy."""
        assert self._gateway is not None
        deadline = asyncio.get_event_loop().time() + timeout
        delay = 0.1
        while asyncio.get_event_loop().time() < deadline:
            if await self._gateway.health():
                try:
                    await self._gateway.list_mcps()
                    return
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "OpenSandboxWorkspace: gateway auth probe failed "
                        "(will retry): %s",
                        exc,
                    )
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
        """Copy ``self.skill_paths`` into ``skills/`` once, on first init."""
        if not self.skill_paths:
            return
        assert self._backend is not None
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
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "OpenSandboxWorkspace: skip skill %r: %s",
                    path,
                    exc,
                )

    async def _offload_data_block(self, block: DataBlock) -> DataBlock:
        """Persist a base64 :class:`DataBlock` under ``data/``."""
        if not isinstance(block.source, Base64Source):
            return block
        assert self._backend is not None
        digest = hashlib.sha256(block.source.data.encode()).hexdigest()
        ext = mimetypes.guess_extension(block.source.media_type) or ".bin"
        path = f"{SANDBOX_DATA_DIR}/{digest}{ext}"
        await self._backend.exec_shell(["mkdir", "-p", SANDBOX_DATA_DIR])
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

    def _endpoint_url(self, endpoint: Any) -> str:
        """Normalize an OpenSandbox endpoint object into a base URL string."""
        return self._ensure_endpoint_scheme(str(endpoint.endpoint))

    def _ensure_endpoint_scheme(self, value: str) -> str:
        """Add the configured protocol when OpenSandbox returns host:port."""
        if "://" in value:
            return value
        return f"{self.protocol}://{value}"

    @staticmethod
    def _endpoint_headers(endpoint: Any) -> dict[str, str]:
        """Extract proxy headers from an OpenSandbox endpoint object."""
        return dict(endpoint.headers)


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
