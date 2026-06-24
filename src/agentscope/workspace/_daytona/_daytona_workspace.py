# -*- coding: utf-8 -*-
"""Daytona workspace implementation."""

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


class DaytonaWorkspace(WorkspaceBase):
    """Workspace backed by a Daytona sandbox."""

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
        """Construct a Daytona workspace without starting the sandbox."""
        super().__init__(workspace_id=workspace_id)

        self.workdir = ""
        self._user_home = ""

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

        self.default_mcps: list[MCPClient] = list(default_mcps or [])
        self.skill_paths: list[str] = list(skill_paths or [])

        self._daytona: Any = None
        self._sandbox: Any = None
        self._backend: DaytonaBackend | None = None
        self._gateway: GatewayClient | None = None
        self._gateway_token: str = ""
        self._mcps: list[MCPClient] = []
        self._gateway_clients: dict[str, GatewayMCPClient] = {}
        self._mcp_lock = asyncio.Lock()
        self._skill_lock = asyncio.Lock()

    @property
    def sandbox_id(self) -> str | None:
        """Daytona sandbox id, or ``None`` if not started."""
        return _sandbox_id(self._sandbox) if self._sandbox is not None else None

    async def initialize(self) -> None:
        """Reattach or create the sandbox, then start the gateway."""
        if self.is_alive:
            return

        await self._attach_or_create_sandbox()
        await self._derive_sdk_paths()
        self._backend = DaytonaBackend(self._sandbox, workdir=self.workdir)

        if not await self._backend.file_exists(self._gateway_script):
            await self._backend.exec_shell(["mkdir", "-p", self.workdir])
            await self._run_bootstrap()

        self._mcps = await self._restore_or_seed_mcps()
        self._gateway_token = uuid.uuid4().hex

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
        await self._save_mcp_file()
        await self._seed_skills()

        self.is_alive = True

    async def reset(self) -> None:
        """Clear MCP registrations and persistent workspace state."""
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
        """Gracefully stop the sandbox and release host-side resources."""
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

    async def get_instructions(self) -> str:
        """Return the system-prompt fragment for this workspace."""
        workdir = self.workdir or "<sandbox workdir>"
        return self.instructions.format(workdir=workdir)

    async def list_tools(self) -> list[ToolBase]:
        """Return the six builtin tools backed by Daytona."""
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
        """Return one gateway-backed MCP client per registered MCP."""
        return list(self._gateway_clients.values())

    async def list_skills(self) -> list[Skill]:
        """Enumerate skills by scanning ``skills/`` inside the sandbox."""
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
            except Exception as e:  # noqa: BLE001
                logger.warning("MCP %r close failed: %s", name, e)
            self._mcps = [m for m in self._mcps if m.name != name]
            await self._save_mcp_file()

    async def add_skill(self, skill_path: str) -> None:
        """Upload a local skill directory into sandbox ``skills/``."""
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

    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
    ) -> str:
        """Persist a batch of messages as JSONL inside the sandbox."""
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

    async def _get_daytona_client(self) -> Any:
        """Return a per-workspace AsyncDaytona client."""
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
        """Reattach to an existing sandbox by label, or create one."""
        existing = await self._find_existing_sandbox()
        client = await self._get_daytona_client()

        if existing is not None:
            self._sandbox = existing
            await self._ensure_existing_sandbox_ready()
            return

        params = self._create_params()
        self._sandbox = await client.create(params, timeout=self.timeout_seconds)

    async def _find_existing_sandbox(self) -> Any:
        """Find the most recent usable Daytona sandbox for this workspace."""
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
        """Return whether a listed sandbox can be attached."""
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
        """Start or recover an existing sandbox when needed."""
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
        """Build Daytona create params from initialized workspace config."""
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

    async def _derive_sdk_paths(self) -> None:
        """Derive all sandbox paths from Daytona SDK path APIs."""
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
        """Provision a fresh sandbox."""
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

        await self._backend.write_file(
            self._glob_helper_script,
            _read_glob_helper_bytes(),
        )
        await self._backend.write_file(
            self._gateway_script,
            _read_gateway_script_bytes(),
        )

    async def _restore_or_seed_mcps(self) -> list[MCPClient]:
        """Read persisted MCP config or use default MCPs."""
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
        """Persist ``self._mcps`` to the sandbox workdir."""
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
        """Write the in-sandbox gateway config file."""
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
        """Launch the gateway inside the sandbox."""
        cmd = (
            f"nohup {shlex.quote(self._gateway_venv_py)} -u "
            f"{shlex.quote(self._gateway_script)} "
            f"--config {shlex.quote(self._gateway_config)} "
            f"--port {self.gateway_port} "
            f"> {shlex.quote(self._gateway_log)} 2>&1 &"
        )
        await self._backend.exec_shell(["sh", "-c", cmd])

    async def _wait_for_gateway(self, timeout: float = 30.0) -> None:
        """Block until the gateway answers health checks."""
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
        """Copy configured skills into the sandbox once."""
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

    async def _offload_data_block(self, block: DataBlock) -> DataBlock:
        """Persist a base64 :class:`DataBlock` under ``data/``."""
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
        """Headers required by Daytona preview proxy."""
        token = getattr(preview, "token", None)
        if not token:
            return {}
        return {DAYTONA_PREVIEW_TOKEN_HEADER: str(token)}


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
