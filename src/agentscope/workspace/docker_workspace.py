# -*- coding: utf-8 -*-
"""DockerWorkspace — Docker-container workspace.

Architecture (from diagram 2):

- Container lifecycle (initialize/close) via **docker SDK**.
- MCP operations (list_mcps, add_mcp, remove_mcp) via **HTTP** to the
  in-container MCP gateway.
- Skill operations (add_skill, remove_skill) via **docker SDK**
  (file copy / exec).
- Offload operations via **docker SDK** (exec + write).

Requires ``docker`` (docker-py)::

    pip install docker
"""

import asyncio
import base64
import hashlib
import io
import mimetypes
import posixpath
import shlex
import tarfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any

from .workspace_base import WorkspaceBase
from .gateway import GatewayMixin
from .config import MCPServerConfig
from .types import ExecutionResult, InternalEndpoint, SerializedWorkspaceState
from ..mcp import MCPClient
from ..message import (
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    ToolResultBlock,
    URLSource,
)
from ..skill import Skill
from ..tool import ToolBase
from .._logging import logger

_EXECUTOR = ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix="agentscope-docker-ws",
)

_DEFAULT_INSTRUCTIONS = """<workspace>
You have access to a Docker-based workspace.

All tool calls are executed **inside the container**. Use the tools
provided by the MCP servers to interact with the container's filesystem
and processes.

### Filesystem layout
```
/workspace/
├── skills/      # reusable skills
└── sessions/    # offloaded context and tool results
```
</workspace>"""


class DockerWorkspace(GatewayMixin, WorkspaceBase):
    """Workspace backed by a Docker container.

    Usage::

        workspace = DockerWorkspace(image="my-image:latest")
        await workspace.initialize()
        agent = Agent(..., workspace=workspace)
        # ... agent runs ...
        await workspace.close()
    """

    DEFAULT_IMAGE = "ubuntu:22.04"
    DEFAULT_WORKING_DIR = "/workspace"
    GATEWAY_PORT = 5600
    SKILLS_DIR = "/workspace/skills"

    def __init__(
        self,
        image: str = DEFAULT_IMAGE,
        working_dir: str = DEFAULT_WORKING_DIR,
        mcp_servers: list[MCPServerConfig] | None = None,
        gateway_port: int = GATEWAY_PORT,
        exposed_ports: list[int] | None = None,
        volumes: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
        startup_commands: list[str] | None = None,
        instructions: str = _DEFAULT_INSTRUCTIONS,
    ) -> None:
        self._image = image
        self._working_dir = working_dir
        self._mcp_servers = list(mcp_servers or [])
        self._gateway_port = gateway_port
        self._exposed_ports = list(exposed_ports or [])
        self._volumes = dict(volumes or {})
        self._env = dict(env or {})
        self._startup_commands = list(startup_commands or [])
        self._instructions = instructions

        self._id = uuid.uuid4().hex[:12]
        self._client: Any = None  # docker.DockerClient
        self._container: Any = None  # Container
        self._port_mapping: dict[int, int] = {}
        self._gateway_token = ""
        self._gateway_mcpc: MCPClient | None = None
        self._gateway_base_url = ""
        self._started = False

    @property
    def workspace_id(self) -> str:
        return self._id

    @property
    def container_id(self) -> str | None:
        return self._container.id if self._container else None

    # ── lifecycle ──────────────────────────────────────────────────

    async def initialize(self) -> None:
        if self._started:
            return

        import docker
        import docker.errors as docker_errors

        loop = asyncio.get_running_loop()
        self._client = docker.from_env()

        # Pull image if needed
        try:
            await loop.run_in_executor(
                _EXECUTOR,
                lambda: self._client.images.get(self._image),
            )
        except docker_errors.ImageNotFound:
            repo, _, tag = self._image.partition(":")
            await loop.run_in_executor(
                _EXECUTOR,
                lambda: self._client.images.pull(repo, tag=tag or None),
            )

        # Collect ports to expose (include gateway port)
        ports = list(self._exposed_ports)
        if self._gateway_port not in ports:
            ports.append(self._gateway_port)

        create_kwargs: dict[str, Any] = {
            "image": self._image,
            "command": ["sleep", "infinity"],
            "detach": True,
            "working_dir": self._working_dir,
            "name": f"as_ws_{self._id}",
            "labels": {
                "agentscope.workspace": "true",
                "agentscope.workspace.id": self._id,
            },
        }
        if self._env:
            create_kwargs["environment"] = self._env
        if self._volumes:
            create_kwargs["volumes"] = {
                src: {"bind": dst, "mode": "rw"} for src, dst in self._volumes.items()
            }
        if ports:
            create_kwargs["ports"] = {f"{p}/tcp": ("127.0.0.1", None) for p in ports}

        self._container = await loop.run_in_executor(
            _EXECUTOR,
            lambda: self._client.containers.create(**create_kwargs),
        )
        await loop.run_in_executor(_EXECUTOR, self._container.start)

        # Resolve port mapping
        if ports:
            await loop.run_in_executor(_EXECUTOR, self._container.reload)
            attrs = getattr(self._container, "attrs", {}) or {}
            ports_info = attrs.get("NetworkSettings", {}).get("Ports", {})
            for p in ports:
                bindings = ports_info.get(f"{p}/tcp", [])
                if bindings:
                    self._port_mapping[p] = int(bindings[0]["HostPort"])

        # Create working dir
        await self._exec(f"mkdir -p {self._working_dir}")

        # Run startup commands
        for cmd in self._startup_commands:
            r = await self._exec(cmd)
            if not r.is_ok():
                raise RuntimeError(
                    "DockerWorkspace startup_commands failed "
                    f"(exit {r.exit_code}) for: {cmd!r}\n"
                    f"stderr: {r.stderr.decode(errors='replace')}\n"
                    f"stdout: {r.stdout.decode(errors='replace')}",
                )

        # Start in-container gateway (if MCP servers configured)
        if self._mcp_servers:
            await self._start_gateway()

        self._started = True

    async def is_alive(self) -> bool:
        if not self._container:
            return False
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(_EXECUTOR, self._container.reload)
            return self._container.status == "running"
        except Exception:
            return False

    async def close(self) -> None:
        if self._gateway_mcpc and self._gateway_mcpc.is_connected:
            try:
                await self._gateway_mcpc.close()
            except Exception:
                pass
            self._gateway_mcpc = None

        if self._container:
            loop = asyncio.get_running_loop()
            import docker.errors as docker_errors

            try:
                await loop.run_in_executor(
                    _EXECUTOR,
                    self._container.kill,
                )
            except docker_errors.APIError:
                pass
            try:
                await loop.run_in_executor(
                    _EXECUTOR,
                    lambda: self._container.remove(force=True),
                )
            except docker_errors.APIError:
                pass
            self._container = None

        if self._client:
            self._client.close()
            self._client = None
        self._started = False

    # ── instructions ───────────────────────────────────────────────

    async def get_instructions(self) -> str:
        return self._instructions

    # ── tool & MCP discovery ───────────────────────────────────────

    async def list_tools(self) -> list[ToolBase]:
        return []

    # list_mcps is provided by GatewayMixin

    # ── skill discovery ────────────────────────────────────────────

    async def list_skills(self) -> list["Skill"]:
        import frontmatter as fm

        r = await self._exec(
            f"find {self.SKILLS_DIR} -name SKILL.md 2>/dev/null || true",
        )
        if not r.is_ok():
            return []
        stdout = r.stdout.decode(errors="replace").strip()
        if not stdout:
            return []

        skills: list[Skill] = []
        for md_path in stdout.split("\n"):
            md_path = md_path.strip()
            if not md_path:
                continue
            try:
                raw = await self._read(md_path)
                text = raw.decode("utf-8")
                doc = fm.loads(text)
                name = doc.get("name")
                desc = doc.get("description")
                if not name or not desc:
                    continue
                skill_dir = posixpath.dirname(md_path)
                skills.append(
                    Skill(
                        name=str(name),
                        description=str(desc),
                        dir=skill_dir,
                        markdown=doc.content or "",
                        updated_at=0.0,
                    ),
                )
            except Exception as e:
                logger.warning("Failed to load skill %s: %s", md_path, e)
        return skills

    # ── offload ────────────────────────────────────────────────────

    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
        **kwargs: Any,
    ) -> str:
        base = f"sessions/{session_id}"
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
                        block = await self._offload_data(block)
                    content.append(block)
                msg.content = content
            lines.append(msg.model_dump_json())

        payload = "\n".join(lines) + "\n"
        await self._exec(f"mkdir -p {base}")

        existing = b""
        try:
            existing = await self._read(path)
        except (FileNotFoundError, OSError):
            pass
        await self._write(path, existing + payload.encode("utf-8"))
        return path

    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
        **kwargs: Any,
    ) -> str:
        base = f"sessions/{session_id}"
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
                        d = await self._offload_data(block)
                        url = str(d.source.url)
                    else:
                        url = str(block.source.url)
                    parts.append(
                        f"<data url='{url}' name='{block.name}' "
                        f"media_type='{block.source.media_type}'/>",
                    )

        await self._exec(f"mkdir -p {base}")
        await self._write(path, "".join(parts).encode("utf-8"))
        return path

    # add_mcp / remove_mcp are provided by GatewayMixin

    # ── dynamic skill management (docker SDK) ─────────────────────

    async def add_skill(self, skill_path: str) -> None:
        import os

        skill_md = os.path.join(skill_path, "SKILL.md")
        if not os.path.isfile(skill_md):
            raise ValueError(
                f"Invalid skill at {skill_path}: SKILL.md not found",
            )

        dir_name = os.path.basename(os.path.abspath(skill_path))
        dest = f"{self.SKILLS_DIR}/{dir_name}"
        await self._exec(f"mkdir -p {self.SKILLS_DIR}")

        # tar the local skill directory and put it into the container
        loop = asyncio.get_running_loop()

        def _tar_dir() -> bytes:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tf:
                tf.add(skill_path, arcname=dir_name)
            return buf.getvalue()

        tar_data = await loop.run_in_executor(_EXECUTOR, _tar_dir)

        def _put() -> None:
            self._container.put_archive(self.SKILLS_DIR, tar_data)

        await loop.run_in_executor(_EXECUTOR, _put)
        logger.info("DockerWorkspace: added skill %r at %s", dir_name, dest)

    async def remove_skill(self, name: str) -> None:
        dest = f"{self.SKILLS_DIR}/{shlex.quote(name)}"
        r = await self._exec(f"rm -rf {dest}")
        if not r.is_ok():
            raise RuntimeError(
                f"Failed to remove skill {name!r}: {r.stderr.decode()}",
            )
        logger.info("DockerWorkspace: removed skill %r", name)

    # ── export / restore state ─────────────────────────────────────

    async def export_state(self) -> SerializedWorkspaceState:
        return SerializedWorkspaceState(
            backend_type="docker",
            payload={
                "container_id": self._container.id,
                "workspace_id": self._id,
                "working_dir": self._working_dir,
                "image": self._image,
                "gateway_port": self._gateway_port,
                "mcp_servers": [
                    {
                        "name": s.name,
                        "transport": s.transport,
                        "command": s.command,
                        "args": s.args,
                        "url": s.url,
                    }
                    for s in self._mcp_servers
                ],
            },
        )

    # ── internal: container operations ─────────────────────────────

    async def _exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
    ) -> ExecutionResult:
        loop = asyncio.get_running_loop()

        def _run() -> ExecutionResult:
            result = self._container.exec_run(
                ["sh", "-c", command],
                demux=True,
                workdir=self._working_dir,
            )
            stdout, stderr = result.output
            code = result.exit_code if result.exit_code is not None else -1
            return ExecutionResult(
                exit_code=code,
                stdout=stdout or b"",
                stderr=stderr or b"",
            )

        if timeout is None:
            return await loop.run_in_executor(_EXECUTOR, _run)
        return await asyncio.wait_for(
            loop.run_in_executor(_EXECUTOR, _run),
            timeout=timeout,
        )

    async def _read(self, path: str) -> bytes:
        p = PurePosixPath(path)
        if not p.is_absolute():
            p = PurePosixPath(self._working_dir) / p
        loop = asyncio.get_running_loop()

        def _do() -> bytes:
            bits, _ = self._container.get_archive(str(p))
            raw = b"".join(bits)
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as tf:
                for member in tf.getmembers():
                    if member.isfile():
                        f = tf.extractfile(member)
                        if f:
                            return f.read()
            raise FileNotFoundError(f"not found in container: {path}")

        return await loop.run_in_executor(_EXECUTOR, _do)

    async def _write(self, path: str, data: bytes) -> None:
        p = PurePosixPath(path)
        if not p.is_absolute():
            p = PurePosixPath(self._working_dir) / p
        loop = asyncio.get_running_loop()

        await loop.run_in_executor(
            _EXECUTOR,
            lambda: self._container.exec_run(
                ["mkdir", "-p", str(p.parent)],
            ),
        )

        def _do() -> None:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tf:
                info = tarfile.TarInfo(name=p.name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            buf.seek(0)
            self._container.put_archive(str(p.parent), buf.getvalue())

        await loop.run_in_executor(_EXECUTOR, _do)

    def _resolve_port(self, port: int) -> InternalEndpoint:
        host_port = self._port_mapping.get(port)
        if not host_port:
            raise ValueError(f"port {port} is not exposed")
        return InternalEndpoint(host="127.0.0.1", port=host_port)

    # ── GatewayMixin hooks ─────────────────────────────────────────

    async def _gw_write_remote(self, path: str, data: bytes) -> None:
        await self._write(path, data)

    async def _gw_resolve_base_url(self, port: int) -> str:
        endpoint = self._resolve_port(port)
        return f"http://{endpoint.host}:{endpoint.port}"

    # _start_gateway, _build_gw_config, _wait_for_gateway,
    # _ensure_gateway_python_deps are inherited from GatewayMixin.

    async def _offload_data(self, data_block: DataBlock) -> DataBlock:
        h = hashlib.sha256(data_block.source.data.encode()).hexdigest()
        ext = mimetypes.guess_extension(data_block.source.media_type) or ".bin"
        path = f"data/{h}{ext}"
        await self._exec("mkdir -p data")
        await self._write(path, base64.b64decode(data_block.source.data))
        from pydantic import AnyUrl

        return DataBlock(
            id=data_block.id,
            name=data_block.name,
            source=URLSource(
                url=AnyUrl(f"file:///{path}"),
                media_type=data_block.source.media_type,
            ),
        )
