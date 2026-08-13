# -*- coding: utf-8 -*-
"""Monkey-patch ``K8sBackend._exec_ws_stdin`` to fix the k3s exec hang.

Problem (framework ``agentscope.workspace._k8s._k8s_backend``):
``_exec_ws_stdin`` feeds data over the exec WebSocket stdin channel
(``tar xf -`` / ``cat > file``) and, after sending EOF, iterates
``async for msg in sock`` waiting for the server to close the
WebSocket.  Standard kube-apiserver sends the close frame once the
process exits; k3s' apiserver (through its agent tunnel) does not —
the loop hangs forever: no timeout, no proactive close.

The exec protocol actually marks completion explicitly: after the
process exits the apiserver sends a channel-3 exit-status frame
(``{"status": "Success"}``).  The framework parses that frame but
never ``break``s on it, so the read loop sits waiting for a close
frame that may never come.  This patch therefore:

1. breaks out of the read loop as soon as the channel-3 status frame
   arrives (no waiting for a server close frame),
2. closes the WebSocket proactively with a bounded close timeout,
3. wraps the read phase in ``asyncio.wait_for`` as a safety net so a
   broken apiserver can never hang a chat run.

Only ``_exec_ws_stdin`` (the stdin write path) is patched; the
read/exec path (``exec_shell``, ``stdin=False``) already works on
k3s and is left untouched.  ``write_file`` / ``write_stream`` keep
working with no encoding changes and no ARG_MAX limits.

Framework version synced with ``_k8s_backend.py::_exec_ws_stdin``;
re-check this file whenever the framework is upgraded.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger("bocomadp.k8s_exec_patch")

# How long to wait for the exit-status frame after the stdin EOF has
# been sent.  The command (``tar xf`` / ``cat``) finishes as soon as
# EOF arrives, so the status frame should be immediate; 60s is a
# generous upper bound only ever reached with a broken apiserver.
_READ_TIMEOUT = 60.0

_PATCH_MARK = "_bocomadp_k8s_exec_patched"


async def _patched_exec_ws_stdin(
    self: Any,
    command: list[str],
    stdin: Any,
    path: str,
) -> None:
    """Patched copy of ``K8sBackend._exec_ws_stdin`` — see module docstring."""
    from kubernetes_asyncio import client as k8s_client
    from kubernetes_asyncio.stream import WsApiClient

    async def _drain(sock: Any) -> tuple[int, bytes]:
        """Read stdout/stderr until the channel-3 exit-status frame."""
        stderr_parts: list[bytes] = []
        exit_code = 0
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
                try:
                    status = json.loads(payload.decode("utf-8"))
                    if status.get("status") != "Success":
                        exit_code = 1
                except (json.JSONDecodeError, ValueError):
                    exit_code = 1
                # Exit-status frame received: the command finished.
                # Break instead of waiting for a server close frame
                # (k3s never sends one).
                break
        return exit_code, b"".join(stderr_parts)

    async with WsApiClient(self._api_client.configuration) as ws_api:
        v1_ws = k8s_client.CoreV1Api(api_client=ws_api)
        ws = await v1_ws.connect_get_namespaced_pod_exec(
            self._pod_name,
            self._namespace,
            command=command,
            container=self._container_name,
            stderr=True,
            stdin=True,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        exit_code = 0
        stderr = b""
        async with ws as sock:
            try:
                if isinstance(stdin, list):
                    for chunk in stdin:
                        await sock.send_bytes(bytes([0]) + chunk)
                else:
                    async for chunk in stdin:
                        await sock.send_bytes(bytes([0]) + chunk)
                await sock.send_bytes(bytes([0]))

                try:
                    exit_code, stderr = await asyncio.wait_for(
                        _drain(sock),
                        timeout=_READ_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    # EOF was already sent: the write itself completed
                    # inside the Pod.  A missing exit-status frame just
                    # means a broken apiserver close handshake — treat
                    # as success rather than hanging the chat run.
                    logger.warning(
                        "k8s exec write: no exit-status frame within "
                        "%ss for %s; closing connection",
                        _READ_TIMEOUT,
                        path,
                    )
            finally:
                try:
                    await sock.close(timeout=5)
                except Exception:  # noqa: BLE001
                    pass

    if exit_code != 0:
        raise RuntimeError(
            f"write to {path!r} failed: {command[0]} exited "
            f"{exit_code}: {stderr.decode(errors='replace')}",
        )


def apply_k8s_exec_patch() -> None:
    """Replace ``K8sBackend._exec_ws_stdin`` (idempotent)."""
    from agentscope.workspace._k8s._k8s_backend import K8sBackend

    current = getattr(K8sBackend, "_exec_ws_stdin", None)
    if getattr(current, _PATCH_MARK, False):
        return
    setattr(_patched_exec_ws_stdin, _PATCH_MARK, True)
    K8sBackend._exec_ws_stdin = _patched_exec_ws_stdin
    logger.info("k8s exec write path patched (k3s close-frame fix)")


__all__ = ["apply_k8s_exec_patch"]
