# -*- coding: utf-8 -*-
"""Docker backend: runs commands inside a Docker container.

Provides real process isolation via Docker. Requires the Docker daemon to
be running and the ``docker`` package (docker-py) to be installed::

    pip install agentscope[sandbox]

All blocking docker-py calls are dispatched to a shared
:class:`~concurrent.futures.ThreadPoolExecutor` so they never block the
event loop.
"""

import asyncio
import io
import posixpath
import tarfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any

from .connection import SandboxConnection, register_sandbox_connection_type
from .exceptions import UnsupportedOperation
from .types import (
    SandboxExecutionResult,
    SandboxInitializationConfig,
    SandboxInternalEndpoint,
    SerializedSandboxState,
)


class DockerSandboxConnection(SandboxConnection):
    """Sandbox backed by a Docker container + ``docker-py``.

    The ``workspace`` is the working directory *inside* the container
    where ``exec``, ``read``, and ``write`` operate relative
    to. It defaults to ``/workspace`` and is also the ``cwd`` for commands.

    ``port_mapping`` is the *resolved* mapping
    ``{container_port: host_port}`` obtained after container creation,
    distinct from ``SandboxInitializationConfig.exposed_ports`` which only
    declares *which* ports to expose (the host ports are assigned by
    Docker at runtime).
    """

    DEFAULT_IMAGE: str = "ubuntu:22.04"
    DEFAULT_WORKSPACE: str = "/workspace"

    _EXECUTOR = ThreadPoolExecutor(
        max_workers=8,
        thread_name_prefix="agentscope-docker-sandbox",
    )

    _supports_exposed_ports = True
    _supports_snapshot = True

    @staticmethod
    def _import_docker() -> tuple[Any, Any, Any]:
        """Lazy-import ``docker`` SDK; raises ``ImportError`` with guidance."""
        try:
            import docker as _docker_sdk
            import docker.errors as _docker_errors
            from docker.models.containers import (
                Container as _Container,
            )
        except ImportError as exc:
            raise ImportError(
                "DockerSandboxConnection requires the `docker` "
                "package. Install with: pip install agentscope[sandbox]",
            ) from exc
        return _docker_sdk, _docker_errors, _Container

    def __init__(
        self,
        client: Any,
        container: Any,
        *,
        instance_id: str,
        workspace: str = DEFAULT_WORKSPACE,
        port_mapping: dict[int, int] | None = None,
    ) -> None:
        """Wrap an existing Docker client and running container.

        Args:
            client: ``docker.DockerClient`` instance.
            container: ``docker.models.containers.Container`` handle.
            instance_id: Unique id for this sandbox instance.
            workspace: Working directory inside the container; all
                relative ``read``/``write`` paths resolve under this.
            port_mapping: Resolved ``{container_port: host_port}``
                mapping. Populated by :meth:`create` after the
                container is started and Docker assigns host ports.
        """
        self._client = client
        self._container = container
        self._instance_id = instance_id
        self._workspace = workspace
        self._port_mapping = port_mapping or {}
        self._destroyed = False

    @property
    def backend_id(self) -> str:
        return "docker"

    @property
    def workspace_root(self) -> Path:
        """Logical workspace root inside the container (POSIX path string)."""
        return Path(self._workspace)

    # ─── factory ──────────────────────────────────────────────

    @classmethod
    async def create(
        cls,
        options: SandboxInitializationConfig,
    ) -> "DockerSandboxConnection":
        if options.backend_id != "docker":
            raise ValueError(
                f"expected backend 'docker', got {options.backend_id!r}",
            )
        docker_sdk, _, _ = cls._import_docker()

        image: str = options.extra.get("image", cls.DEFAULT_IMAGE)
        workspace: str = options.extra.get("workspace", cls.DEFAULT_WORKSPACE)
        instance_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()

        client = docker_sdk.from_env()

        def _ensure_image() -> None:
            try:
                client.images.get(image)
            except docker_sdk.errors.ImageNotFound:
                repo, _, tag = image.partition(":")
                client.images.pull(repo, tag=tag or None)

        await loop.run_in_executor(cls._EXECUTOR, _ensure_image)

        create_kwargs: dict[str, Any] = {
            "image": image,
            "command": ["sleep", "infinity"],
            "detach": True,
            "working_dir": workspace,
            "name": f"as_sandbox_{instance_id[:12]}",
            "labels": {
                "agentscope.sandbox": "true",
                "agentscope.sandbox.id": instance_id,
            },
        }

        if options.env:
            create_kwargs["environment"] = options.env

        if options.volumes:
            create_kwargs["volumes"] = {
                src: {"bind": dst, "mode": "rw"}
                for src, dst in options.volumes.items()
            }

        if options.exposed_ports:
            create_kwargs["ports"] = {
                f"{port}/tcp": ("127.0.0.1", None)
                for port in options.exposed_ports
            }

        container = await loop.run_in_executor(
            cls._EXECUTOR,
            lambda: client.containers.create(**create_kwargs),
        )
        await loop.run_in_executor(cls._EXECUTOR, container.start)

        host_port_map: dict[int, int] = {}
        if options.exposed_ports:
            await loop.run_in_executor(cls._EXECUTOR, container.reload)
            attrs = getattr(container, "attrs", {}) or {}
            ports_info = attrs.get("NetworkSettings", {}).get("Ports", {})
            for port in options.exposed_ports:
                bindings = ports_info.get(f"{port}/tcp", [])
                if bindings:
                    host_port_map[port] = int(bindings[0]["HostPort"])

        await loop.run_in_executor(
            cls._EXECUTOR,
            lambda: container.exec_run(["mkdir", "-p", workspace]),
        )

        conn = cls(
            client,
            container,
            instance_id=instance_id,
            workspace=workspace,
            port_mapping=host_port_map,
        )

        for cmd in options.startup_commands:
            await conn.exec(cmd, env=options.env)

        return conn

    @classmethod
    async def resume(
        cls,
        state: SerializedSandboxState,
    ) -> "DockerSandboxConnection":
        docker_sdk, docker_errors, _ = cls._import_docker()
        if state.backend_id != "docker":
            raise ValueError("backend mismatch for resume")

        container_id = state.payload.get("container_id")
        if not isinstance(container_id, str):
            raise ValueError("invalid resume payload: missing container_id")

        instance_id = state.payload.get("instance_id")
        if not isinstance(instance_id, str):
            instance_id = uuid.uuid4().hex

        workspace = state.payload.get("workspace", cls.DEFAULT_WORKSPACE)
        loop = asyncio.get_running_loop()

        client = docker_sdk.from_env()
        try:
            container = await loop.run_in_executor(
                cls._EXECUTOR,
                lambda: client.containers.get(container_id),
            )
        except docker_errors.NotFound as e:
            client.close()
            raise UnsupportedOperation(
                f"container {container_id} no longer exists",
            ) from e

        await loop.run_in_executor(cls._EXECUTOR, container.reload)
        if container.status != "running":
            await loop.run_in_executor(cls._EXECUTOR, container.start)

        return cls(
            client,
            container,
            instance_id=instance_id,
            workspace=workspace,
        )

    # ─── path resolution ─────────────────────────────────────

    def _resolve(self, path: str) -> str:
        """Resolve a sandbox-relative path inside the container workspace."""
        p = PurePosixPath(path)
        if p.is_absolute():
            resolved = posixpath.normpath(p.as_posix())
        else:
            resolved = posixpath.normpath(
                (PurePosixPath(self._workspace) / p).as_posix(),
            )
        ws = posixpath.normpath(self._workspace)
        if resolved != ws and not resolved.startswith(ws + "/"):
            raise ValueError(f"path escapes sandbox workspace: {path!r}")
        return resolved

    # ─── exec ─────────────────────────────────────────────────

    async def exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> SandboxExecutionResult:
        workdir = self._resolve(cwd) if cwd else self._workspace
        loop = asyncio.get_running_loop()

        def _run() -> SandboxExecutionResult:
            result = self._container.exec_run(
                ["sh", "-c", command],
                demux=True,
                workdir=workdir,
                environment=env,
            )
            stdout, stderr = result.output
            code = result.exit_code if result.exit_code is not None else -1
            return SandboxExecutionResult(
                exit_code=code,
                stdout=stdout or b"",
                stderr=stderr or b"",
            )

        if not timeout:
            return await loop.run_in_executor(self._EXECUTOR, _run)

        return await asyncio.wait_for(
            loop.run_in_executor(self._EXECUTOR, _run),
            timeout=timeout,
        )

    # ─── filesystem ───────────────────────────────────────────

    async def read(self, path: str) -> bytes:
        container_path = self._resolve(path)
        loop = asyncio.get_running_loop()

        def _read() -> bytes:
            bits, _stat = self._container.get_archive(container_path)
            raw = b"".join(bits)
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as tf:
                for member in tf.getmembers():
                    if member.isfile():
                        f = tf.extractfile(member)
                        if f:
                            return f.read()
            raise FileNotFoundError(f"file not found in container: {path}")

        return await loop.run_in_executor(self._EXECUTOR, _read)

    async def write(self, path: str, data: bytes) -> None:
        container_path = self._resolve(path)
        p = PurePosixPath(container_path)
        loop = asyncio.get_running_loop()

        await loop.run_in_executor(
            self._EXECUTOR,
            lambda: self._container.exec_run(
                ["mkdir", "-p", str(p.parent)],
            ),
        )

        def _write() -> None:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tf:
                info = tarfile.TarInfo(name=p.name)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
            buf.seek(0)
            self._container.put_archive(str(p.parent), buf.getvalue())

        await loop.run_in_executor(self._EXECUTOR, _write)

    # ─── lifecycle ────────────────────────────────────────────

    async def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        _, docker_errors, _ = self._import_docker()
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._EXECUTOR, self._container.kill)
        except docker_errors.APIError:
            pass
        try:
            await loop.run_in_executor(
                self._EXECUTOR,
                lambda: self._container.remove(force=True),
            )
        except docker_errors.APIError:
            pass
        self._client.close()

    async def close(self) -> None:
        """Soft close: stop container but don't remove it (for pool reuse)."""
        if self._destroyed:
            return
        self._destroyed = True
        _, docker_errors, _ = self._import_docker()
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._EXECUTOR, self._container.stop)
        except docker_errors.APIError:
            pass
        self._client.close()

    async def is_running(self) -> bool:
        if self._destroyed:
            return False
        _, docker_errors, _ = self._import_docker()
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                self._EXECUTOR,
                self._container.reload,
            )
            return self._container.status == "running"
        except docker_errors.APIError:
            return False

    # ─── capabilities ─────────────────────────────────────────

    async def snapshot_workspace(self) -> bytes:
        """Export the workspace directory as a tar archive."""
        loop = asyncio.get_running_loop()

        def _snapshot() -> bytes:
            bits, _stat = self._container.get_archive(self._workspace)
            return b"".join(bits)

        return await loop.run_in_executor(self._EXECUTOR, _snapshot)

    async def restore_workspace(self, data: bytes) -> None:
        """Restore the workspace directory from a tar archive."""
        loop = asyncio.get_running_loop()

        rm_workspace = (
            f"rm -rf {self._workspace}/* {self._workspace}"
            "/.[!.]* 2>/dev/null; true"
        )

        def _clear_workspace() -> None:
            self._container.exec_run(["sh", "-c", rm_workspace])

        await loop.run_in_executor(self._EXECUTOR, _clear_workspace)

        def _restore() -> None:
            self._container.put_archive("/", data)

        await loop.run_in_executor(self._EXECUTOR, _restore)

    async def resolve_exposed_port(
        self,
        port: int,
    ) -> SandboxInternalEndpoint:
        host_port = self._port_mapping.get(port)
        if not host_port:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                self._EXECUTOR,
                self._container.reload,
            )
            attrs = getattr(self._container, "attrs", {}) or {}
            ports_info = attrs.get("NetworkSettings", {}).get("Ports", {})
            bindings = ports_info.get(f"{port}/tcp", [])
            if bindings:
                host_port = int(bindings[0]["HostPort"])

        if not host_port:
            raise ValueError(f"port {port} is not exposed")
        return SandboxInternalEndpoint(host="127.0.0.1", port=host_port)

    # ─── optional: export_state ───────────────────────────────

    async def export_state(self) -> SerializedSandboxState:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._EXECUTOR, self._container.reload)
        return SerializedSandboxState(
            backend_id=self.backend_id,
            payload={
                "container_id": self._container.id,
                "instance_id": self._instance_id,
                "workspace": self._workspace,
            },
        )


register_sandbox_connection_type(DockerSandboxConnection)
