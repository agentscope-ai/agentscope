# -*- coding: utf-8 -*-
"""Kubernetes Pod :class:`BackendBase` implementation.

Wraps the ``kubernetes_asyncio`` exec API and tar-stream file transfer
into the three backend primitives (``exec_shell``, ``read_file``,
``write_file``) so that builtin tools (Bash, Read, Write, Edit, Grep,
Glob) can operate inside a K8s Pod transparently.

File I/O uses **tar streams** piped through ``exec`` — the same
mechanism ``kubectl cp`` uses under the hood.  This mirrors
:class:`DockerBackend`'s ``get_archive`` / ``put_archive`` approach
and avoids the known reliability issues with raw stdin binary writes
over the ``kubernetes_asyncio`` WebSocket channel
(cf. kubernetes-client/python#1866).
"""

from __future__ import annotations

import asyncio
import io
import posixpath
import shlex
import tarfile
from typing import Any

from ...tool import BackendBase, ExecResult


class K8sBackend(BackendBase):
    """Backend that delegates to a running Kubernetes Pod.

    Only the three abstract primitives (``exec_shell``, ``read_file``,
    ``write_file``) are implemented here; the derived filesystem helpers
    are inherited from :class:`BackendBase`.

    Args:
        api_client (`Any`):
            A ``kubernetes_asyncio.client.ApiClient`` instance used
            to build ``CoreV1Api`` for exec calls.
        namespace (`str`):
            Kubernetes namespace the Pod lives in.
        pod_name (`str`):
            Name of the target Pod.
        container_name (`str`):
            Container name within the Pod.
        workdir (`str`):
            Default working directory for ``exec_shell`` calls.
    """

    def __init__(
        self,
        api_client: Any,
        namespace: str,
        pod_name: str,
        container_name: str,
        workdir: str,
    ) -> None:
        """Initialize the K8s backend.

        Args:
            api_client (`Any`):
                A ``kubernetes_asyncio.client.ApiClient`` instance.
            namespace (`str`):
                Kubernetes namespace the Pod lives in.
            pod_name (`str`):
                Name of the target Pod.
            container_name (`str`):
                Container name within the Pod.
            workdir (`str`):
                Default working directory for ``exec_shell`` calls.
        """
        self._api_client = api_client
        self._namespace = namespace
        self._pod_name = pod_name
        self._container_name = container_name
        self._workdir = workdir

    # ── exec ───────────────────────────────────────────────────────

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run a program inside the Pod via WebSocket ``exec``.

        Opens a multiplexed WebSocket channel with
        ``_preload_content=False`` and reads channel-tagged frames to
        separate stdout (channel 1), stderr (channel 2) and the exit
        status (channel 3).  ``cwd`` is emulated by wrapping the
        command in ``sh -c 'cd <cwd> && exec "$@"' -- <cmd...>``.

        Args:
            command (`list[str]`):
                Executable path/name followed by its arguments.
            cwd (`str | None`, optional):
                Working directory inside the Pod. When ``None`` the
                backend's default ``workdir`` is used.
            timeout (`float | None`, optional):
                Maximum number of seconds to wait. ``None`` waits
                indefinitely.

        Returns:
            `ExecResult`:
                The captured exit code, stdout, and stderr.
        """
        from kubernetes_asyncio import client as k8s_client
        from kubernetes_asyncio.stream import WsApiClient

        effective_cwd = cwd or self._workdir
        wrapped = [
            "sh",
            "-c",
            f'cd {shlex.quote(effective_cwd)} && exec "$@"',
            "--",
            *command,
        ]

        async def _run() -> ExecResult:
            async with WsApiClient(
                self._api_client.configuration,
            ) as ws_api:
                v1_ws = k8s_client.CoreV1Api(api_client=ws_api)
                ws = await v1_ws.connect_get_namespaced_pod_exec(
                    self._pod_name,
                    self._namespace,
                    command=wrapped,
                    container=self._container_name,
                    stderr=True,
                    stdin=False,
                    stdout=True,
                    tty=False,
                    _preload_content=False,
                )
                stdout_parts: list[bytes] = []
                stderr_parts: list[bytes] = []
                exit_code = 0

                async with ws as sock:
                    async for msg in sock:
                        if msg.type in (
                            1,  # WSMsgType.TEXT
                            2,  # WSMsgType.BINARY
                        ):
                            data = (
                                msg.data
                                if isinstance(msg.data, bytes)
                                else msg.data.encode("utf-8")
                            )
                            if not data:
                                continue
                            channel = data[0]
                            payload = data[1:]
                            if channel == 1:  # stdout
                                stdout_parts.append(payload)
                            elif channel == 2:  # stderr
                                stderr_parts.append(payload)
                            elif channel == 3:  # error/status
                                import json

                                try:
                                    status = json.loads(
                                        payload.decode("utf-8"),
                                    )
                                    if status.get("status") != "Success":
                                        exit_code = int(
                                            status.get(
                                                "details",
                                                {},
                                            )
                                            .get("causes", [{}])[0]
                                            .get("message", "1"),
                                        )
                                except (
                                    json.JSONDecodeError,
                                    ValueError,
                                    IndexError,
                                    KeyError,
                                ):
                                    exit_code = 1
                        else:
                            break

                return ExecResult(
                    exit_code=exit_code,
                    stdout=b"".join(stdout_parts),
                    stderr=b"".join(stderr_parts),
                )

        if timeout is None:
            return await _run()
        try:
            return await asyncio.wait_for(_run(), timeout=timeout)
        except asyncio.TimeoutError:
            return ExecResult(
                exit_code=-1,
                stdout=b"",
                stderr=b"timed out",
            )

    # ── file I/O (tar-stream, mirroring DockerBackend) ─────────────

    async def read_file(self, path: str) -> bytes:
        """Read a file from the Pod via tar stream extraction.

        Runs ``tar cf - -C <dir> <basename>`` inside the Pod, collects
        the tar bytes from stdout, and extracts the file content.  This
        handles binary files correctly — identical to DockerBackend's
        ``get_archive`` approach.

        Args:
            path (`str`):
                Path to the file inside the Pod.

        Returns:
            `bytes`:
                The raw file contents.

        Raises:
            `FileNotFoundError`:
                If the path does not exist inside the Pod.
        """
        dirname = posixpath.dirname(path) or "/"
        basename = posixpath.basename(path)

        result = await self.exec_shell(
            ["tar", "cf", "-", "-C", dirname, basename],
            cwd="/",
        )
        if not result.ok():
            stderr_text = result.stderr.decode(errors="replace")
            if "No such file" in stderr_text or "not found" in stderr_text:
                raise FileNotFoundError(
                    f"not found in Pod: {path}",
                )
            raise FileNotFoundError(
                f"not found in Pod: {path} (tar stderr: {stderr_text})",
            )

        try:
            tar = tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r")
        except tarfile.ReadError as exc:
            raise FileNotFoundError(
                f"not found in Pod: {path}",
            ) from exc

        try:
            for member in tar.getmembers():
                if member.isfile():
                    f = tar.extractfile(member)
                    if f:
                        return f.read()
        finally:
            tar.close()

        raise FileNotFoundError(f"not found in Pod: {path}")

    async def write_file(self, path: str, data: bytes) -> None:
        """Write raw bytes to a file inside the Pod via tar stream.

        Creates the parent directory first, then constructs an in-memory
        tar archive containing the file and pipes it to
        ``tar xf - -C <parent>`` inside the Pod.  This mirrors
        DockerBackend's ``put_archive`` approach and the mechanism
        ``kubectl cp`` uses internally.

        After sending the tar data, this method reads back the exec
        stream to capture any ``tar`` stderr and exit status.  A
        non-zero exit raises ``RuntimeError``.

        Args:
            path (`str`):
                Destination path inside the Pod.
            data (`bytes`):
                The raw bytes to write.

        Raises:
            `RuntimeError`:
                If ``tar xf`` exits non-zero inside the Pod.
        """
        parent = posixpath.dirname(path) or "/"
        name = posixpath.basename(path)

        await self.exec_shell(["mkdir", "-p", parent])

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        tar_bytes = buf.getvalue()

        from kubernetes_asyncio import client as k8s_client
        from kubernetes_asyncio.stream import WsApiClient

        async with WsApiClient(
            self._api_client.configuration,
        ) as ws_api:
            v1_ws = k8s_client.CoreV1Api(api_client=ws_api)
            ws = await v1_ws.connect_get_namespaced_pod_exec(
                self._pod_name,
                self._namespace,
                command=["tar", "xf", "-", "-C", parent],
                container=self._container_name,
                stderr=True,
                stdin=True,
                stdout=True,
                tty=False,
                _preload_content=False,
            )
            stderr_parts: list[bytes] = []
            exit_code = 0

            async with ws as sock:
                await sock.send_bytes(
                    bytes([0]) + tar_bytes,
                )
                await sock.send_bytes(bytes([0]))

                async for msg in sock:
                    if msg.type not in (1, 2):
                        break
                    raw = (
                        msg.data
                        if isinstance(msg.data, bytes)
                        else msg.data.encode("utf-8")
                    )
                    if not raw:
                        continue
                    channel = raw[0]
                    payload = raw[1:]
                    if channel == 2:
                        stderr_parts.append(payload)
                    elif channel == 3:
                        import json

                        try:
                            status = json.loads(
                                payload.decode("utf-8"),
                            )
                            if status.get("status") != "Success":
                                exit_code = 1
                        except (
                            json.JSONDecodeError,
                            ValueError,
                        ):
                            exit_code = 1

            if exit_code != 0:
                stderr_text = b"".join(stderr_parts).decode(
                    errors="replace",
                )
                raise RuntimeError(
                    f"write_file to {path!r} failed: "
                    f"tar xf exited {exit_code}: {stderr_text}",
                )
