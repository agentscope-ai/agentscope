# -*- coding: utf-8 -*-
"""DockerWorkspace — Docker-container workspace.

Architecture (from diagram 2):

- Container lifecycle (initialize/close) via **aiodocker**.
- MCP operations (list_mcps, add_mcp, remove_mcp) via **HTTP** to the
  in-container MCP gateway.
- Skill operations (add_skill, remove_skill) via **aiodocker**
  (file copy / exec).
- Offload operations via **aiodocker** (exec + write).

Requires ``aiodocker``::

    pip install aiodocker
"""

import asyncio
import base64
import hashlib
import io
import mimetypes
import posixpath
import shlex
import tarfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, ClassVar

from pydantic import Field, PrivateAttr

from .._logging import logger
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
from .mcp_enhanced_workspace import WorkspaceWithMCP
from .types import ExecutionResult, SerializedWorkspaceState

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


@dataclass(frozen=True, slots=True)
class InternalEndpoint:
    """Host-accessible endpoint for a service running inside a container.

    Attributes:
        host: Hostname or IP address.
        port: Port number.
        is_tls_enabled: Whether TLS is active on this endpoint.
    """

    host: str
    port: int
    is_tls_enabled: bool = False


class DockerWorkspace(WorkspaceWithMCP):
    """Workspace backed by a Docker container.

    All operations (exec, file I/O, skill management) are performed
    inside the container via **aiodocker**.  MCP servers run inside
    the container and are accessed through an HTTP gateway.

    Args:
        image: Docker image to use for the container.
            Defaults to ``"ubuntu:22.04"``.
        working_dir: Working directory inside the container.
            Defaults to ``"/workspace"``.
        exposed_ports: Additional container ports to expose to the
            host (the gateway port is always included).
        volumes: Host-to-container volume bindings as
            ``{host_path: container_path}``.
        env: Environment variables to set inside the container
            as ``{key: value}``.
        startup_commands: Shell commands to run inside the container
            after creation (before the MCP gateway starts).  A
            non-zero exit code from any command aborts initialization.
        instructions: Workspace-specific system prompt fragment
            returned by :meth:`get_instructions`.
        mcp_servers: MCP server configurations (inherited from
            :class:`WorkspaceWithMCP`).
        gateway_port: MCP gateway port (inherited from
            :class:`WorkspaceWithMCP`).  Defaults to ``5600``.

    Usage::

        workspace = DockerWorkspace(image="my-image:latest")
        await workspace.initialize()
        agent = Agent(..., workspace=workspace)
        # ... agent runs ...
        await workspace.close()
    """

    # ── class-level constants ─────────────────────────────────────

    DEFAULT_IMAGE: ClassVar[str] = "ubuntu:22.04"
    DEFAULT_WORKING_DIR: ClassVar[str] = "/workspace"
    GATEWAY_PORT: ClassVar[int] = 5600
    SKILLS_DIR: ClassVar[str] = "/workspace/skills"

    # ── serializable configuration fields ─────────────────────────

    image: str = DEFAULT_IMAGE
    working_dir: str = DEFAULT_WORKING_DIR
    exposed_ports: list[int] = Field(default_factory=list)
    volumes: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    startup_commands: list[str] = Field(default_factory=list)
    instructions: str = _DEFAULT_INSTRUCTIONS

    # ── runtime state (excluded from serialisation) ───────────────

    _client: Any = PrivateAttr(default=None)  # aiodocker.Docker
    _container: Any = PrivateAttr(default=None)  # aiodocker.DockerContainer
    _port_mapping: dict[int, int] = PrivateAttr(default_factory=dict)

    @property
    def container_id(self) -> str | None:
        """Docker container ID, or ``None`` if not started."""
        return self._container.id if self._container else None

    # ── lifecycle ──────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Provision container, run startup commands, start gateway."""
        if self._started:
            return

        import aiodocker

        self._client = aiodocker.Docker()

        # Pull image if needed
        try:
            await self._client.images.inspect(self.image)
        except aiodocker.exceptions.DockerError:
            repo, _, tag = self.image.partition(":")
            await self._client.images.pull(
                from_image=repo,
                tag=tag or "latest",
            )

        # Collect ports to expose (include gateway port)
        ports = list(self.exposed_ports)
        if self.gateway_port not in ports:
            ports.append(self.gateway_port)

        # Build container config for aiodocker
        config: dict[str, Any] = {
            "Image": self.image,
            "Cmd": ["sleep", "infinity"],
            "WorkingDir": self.working_dir,
            "Labels": {
                "agentscope.workspace": "true",
                "agentscope.workspace.id": self.workspace_id,
            },
        }
        if self.env:
            config["Env"] = [f"{k}={v}" for k, v in self.env.items()]

        # ExposedPorts
        if ports:
            config["ExposedPorts"] = {f"{p}/tcp": {} for p in ports}

        # HostConfig
        host_config: dict[str, Any] = {}
        if self.volumes:
            host_config["Binds"] = [
                f"{src}:{dst}:rw" for src, dst in self.volumes.items()
            ]
        if ports:
            host_config["PortBindings"] = {
                f"{p}/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}]
                for p in ports
            }
        config["HostConfig"] = host_config

        self._container = await self._client.containers.create_or_replace(
            name=f"as_ws_{self.workspace_id}",
            config=config,
        )
        await self._container.start()

        # Resolve port mapping
        if ports:
            info = await self._container.show()
            ports_info = info.get("NetworkSettings", {}).get("Ports", {})
            for p in ports:
                bindings = ports_info.get(f"{p}/tcp", [])
                if bindings:
                    self._port_mapping[p] = int(bindings[0]["HostPort"])

        # Create working dir
        await self._exec(f"mkdir -p {self.working_dir}")

        # Run startup commands
        for cmd in self.startup_commands:
            r = await self._exec(cmd)
            if not r.is_ok():
                raise RuntimeError(
                    "DockerWorkspace startup_commands failed "
                    f"(exit {r.exit_code}) for: {cmd!r}\n"
                    f"stderr: {r.stderr.decode(errors='replace')}\n"
                    f"stdout: {r.stdout.decode(errors='replace')}",
                )

        await super().initialize()
        self._started = True

    async def reset(self) -> None:
        """Reset container workspace to a clean state.

        Clears session data and offloaded files inside the container.
        """
        await self._exec(
            f"rm -rf {self.working_dir}/sessions {self.working_dir}/data",
        )

    async def is_alive(self) -> bool:
        """``True`` if the Docker container is still running."""
        if not self._container:
            return False
        try:
            info = await self._container.show()
            state = info.get("State", {})
            return state.get("Running", False)
        except Exception:
            return False

    async def close(self) -> None:
        """Kill and remove the Docker container; release resources."""
        if self._gateway_mcp_client and self._gateway_mcp_client.is_connected:
            try:
                await self._gateway_mcp_client.close()
            except Exception:
                pass
            self._gateway_mcp_client = None

        if self._container:
            try:
                await self._container.kill()
            except Exception:
                pass
            try:
                await self._container.delete(force=True)
            except Exception:
                pass
            self._container = None

        if self._client:
            await self._client.close()
            self._client = None
        self._started = False

    # ── instructions ───────────────────────────────────────────────

    async def get_instructions(self) -> str:
        """Return the workspace-specific system prompt fragment."""
        return self.instructions

    # ── tool & MCP discovery ───────────────────────────────────────

    async def list_tools(self) -> list[ToolBase]:
        """No built-in tools — all tools come via the MCP gateway."""
        return []

    # list_mcps is provided by WorkspaceWithMCP

    # ── skill discovery ────────────────────────────────────────────

    async def list_skills(self) -> list["Skill"]:
        """Discover skills by scanning ``SKILLS_DIR`` inside the container."""
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
        """Offload context to a JSONL file inside the container."""
        base = f"{self.working_dir}/sessions/{session_id}"
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
        """Persist a tool result inside the container."""
        base = f"{self.working_dir}/sessions/{session_id}"
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

    # add_mcp / remove_mcp are provided by WorkspaceWithMCP

    # ── dynamic skill management (aiodocker) ─────────────────────

    async def add_skill(self, skill_path: str) -> None:
        """Copy a local skill directory into the container via tar archive."""
        import os

        skill_md = os.path.join(skill_path, "SKILL.md")
        if not os.path.isfile(skill_md):
            raise ValueError(
                f"Invalid skill at {skill_path}: SKILL.md not found",
            )

        dir_name = os.path.basename(os.path.abspath(skill_path))
        await self._exec(f"mkdir -p {self.SKILLS_DIR}")

        # tar the local skill directory and put it into the container
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            tf.add(skill_path, arcname=dir_name)
        tar_data = buf.getvalue()

        await self._container.put_archive(self.SKILLS_DIR, tar_data)
        logger.info(
            "DockerWorkspace: added skill %r at %s",
            dir_name,
            f"{self.SKILLS_DIR}/{dir_name}",
        )

    async def remove_skill(self, name: str) -> None:
        """Remove a skill directory from the container by name."""
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
        dest = shlex.quote(target_dir)
        r = await self._exec(f"rm -rf {dest}")
        if not r.is_ok():
            raise RuntimeError(
                f"Failed to remove skill {name!r}: {r.stderr.decode()}",
            )
        logger.info("DockerWorkspace: removed skill %r", name)

    # ── export / restore state ─────────────────────────────────────

    async def export_state(self) -> SerializedWorkspaceState:
        """Serialize workspace identity for later restore."""
        return SerializedWorkspaceState(
            backend_type="docker",
            payload={
                "container_id": self._container.id,
                "workspace_id": self.workspace_id,
                "working_dir": self.working_dir,
                "image": self.image,
                "gateway_port": self.gateway_port,
                "mcp_servers": [s.model_dump() for s in self.mcp_servers],
            },
        )

    # ── internal: container operations ─────────────────────────────

    async def _exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
    ) -> ExecutionResult:
        """Execute a shell command inside the Docker container.

        Args:
            command: Shell command string to execute.
            timeout: Maximum seconds to wait. ``None`` means
                no limit.
        """

        async def _run() -> ExecutionResult:
            exec_obj = await self._container.exec(
                cmd=["sh", "-c", command],
                workdir=self.working_dir,
            )
            stream = exec_obj.start()
            stdout_parts: list[bytes] = []
            stderr_parts: list[bytes] = []
            async for msg in stream:
                # aiodocker streams may return (stream_type, data) or
                # just data depending on tty setting
                if isinstance(msg, tuple):
                    stream_type, data = msg
                    if stream_type == 1:
                        stdout_parts.append(data)
                    else:
                        stderr_parts.append(data)
                else:
                    stdout_parts.append(msg)

            inspect = await exec_obj.inspect()
            code = inspect.get("ExitCode", -1)
            if code is None:
                code = -1
            return ExecutionResult(
                exit_code=code,
                stdout=b"".join(stdout_parts),
                stderr=b"".join(stderr_parts),
            )

        if timeout is None:
            return await _run()
        try:
            return await asyncio.wait_for(_run(), timeout=timeout)
        except asyncio.TimeoutError:
            return ExecutionResult(
                exit_code=-1,
                stdout=b"",
                stderr=b"timed out",
            )

    async def _read(self, path: str) -> bytes:
        """Read a file from the container, returning raw bytes.

        Args:
            path: Absolute or relative path inside the container.
        """
        p = PurePosixPath(path)
        if not p.is_absolute():
            p = PurePosixPath(self.working_dir) / p

        tar_stream = await self._container.get_archive(str(p))
        # aiodocker get_archive returns a dict with 'data' (tar bytes)
        # or an async generator depending on version
        if isinstance(tar_stream, dict):
            raw = tar_stream["data"]
        else:
            chunks: list[bytes] = []
            async for chunk in tar_stream:
                chunks.append(chunk)
            raw = b"".join(chunks)

        with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as tf:
            for member in tf.getmembers():
                if member.isfile():
                    f = tf.extractfile(member)
                    if f:
                        return f.read()
        raise FileNotFoundError(f"not found in container: {path}")

    async def _write(self, path: str, data: bytes) -> None:
        """Write raw bytes to a file inside the container.

        Args:
            path: Absolute or relative path inside the container.
            data: Raw bytes to write.
        """
        p = PurePosixPath(path)
        if not p.is_absolute():
            p = PurePosixPath(self.working_dir) / p

        # Ensure parent directory exists
        await self._exec(f"mkdir -p {shlex.quote(str(p.parent))}")

        # Build tar archive with the file
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo(name=p.name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        buf.seek(0)

        await self._container.put_archive(str(p.parent), buf.getvalue())

    def _resolve_port(self, port: int) -> InternalEndpoint:
        """Map a container port to its host-side endpoint."""
        host_port = self._port_mapping.get(port)
        if not host_port:
            raise ValueError(f"port {port} is not exposed")
        return InternalEndpoint(host="127.0.0.1", port=host_port)

    # ── WorkspaceWithMCP hooks ──────────────────────────────────────

    async def _write_remote(self, path: str, data: bytes) -> None:
        """Write bytes to a file inside the container."""
        await self._write(path, data)

    async def _resolve_base_url(self, port: int) -> str:
        """Map container port to a localhost URL."""
        endpoint = self._resolve_port(port)
        return f"http://{endpoint.host}:{endpoint.port}"

    async def _offload_data(self, data_block: DataBlock) -> DataBlock:
        """Decode base64 data, save in container, return URL block.

        Args:
            data_block: A :class:`DataBlock` with base64 source.
        """
        h = hashlib.sha256(data_block.source.data.encode()).hexdigest()
        ext = mimetypes.guess_extension(data_block.source.media_type) or ".bin"
        data_dir = f"{self.working_dir}/data"
        path = f"{data_dir}/{h}{ext}"
        await self._exec(f"mkdir -p {data_dir}")
        await self._write(path, base64.b64decode(data_block.source.data))
        from pydantic import AnyUrl

        return DataBlock(
            id=data_block.id,
            name=data_block.name,
            source=URLSource(
                url=AnyUrl(f"file://{path}"),
                media_type=data_block.source.media_type,
            ),
        )
