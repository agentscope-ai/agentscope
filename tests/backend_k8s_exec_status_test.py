# -*- coding: utf-8 -*-
"""Test cases for the :class:`K8sBackend` exec-stream status mapping.

``kubectl exec``-style streams carry the command's verdict on channel 3,
as a final ``Status`` object. Everything the stream delivers before that
frame is output, not an outcome — and aiohttp reports a broken
connection as ``WSMsgType.ERROR`` (``258``), which is not a data frame
at all. ``ExecResult.exit_code`` documents ``-1`` as the sentinel for
exactly that case: ``timeout, connection error, …``.

The sockets below are fakes installed into ``sys.modules``, so no cluster
or ``kubernetes_asyncio`` package is needed.
"""

import sys
import types
from typing import Any, AsyncIterator
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import ExecResult
from agentscope.workspace import K8sBackend

TEXT = 1  # aiohttp WSMsgType.TEXT
ERROR = 258  # aiohttp WSMsgType.ERROR


def _stdout(data: bytes) -> types.SimpleNamespace:
    """One channel-1 (stdout) frame."""
    return types.SimpleNamespace(type=TEXT, data=bytes([1]) + data)


def _stderr(data: bytes) -> types.SimpleNamespace:
    """One channel-2 (stderr) frame."""
    return types.SimpleNamespace(type=TEXT, data=bytes([2]) + data)


def _status(payload: bytes) -> types.SimpleNamespace:
    """One channel-3 (error/status) frame."""
    return types.SimpleNamespace(type=TEXT, data=bytes([3]) + payload)


SUCCESS = _status(b'{"metadata":{},"status":"Success"}')
EXIT_3 = _status(
    b'{"metadata":{},"status":"Failure","details":'
    b'{"causes":[{"reason":"ExitCode","message":"3"}]}}',
)


def _reset() -> types.SimpleNamespace:
    """The frame aiohttp yields when the connection dies mid-stream."""
    return types.SimpleNamespace(
        type=ERROR,
        data=ConnectionResetError(104, "Connection reset by peer"),
    )


class _FakeSocket:
    """The exec stream one command produces, frames and writes alike."""

    def __init__(self, messages: list[Any]) -> None:
        """Take ownership of ``messages`` for this exec."""
        self._messages = messages
        self.sent: list[bytes] = []

    async def __aenter__(self) -> "_FakeSocket":
        """The socket is ready as soon as it is entered."""
        return self

    async def __aexit__(self, *exc: object) -> bool:
        """Nothing to clean up on a fake."""
        return False

    def __aiter__(self) -> AsyncIterator[Any]:
        """Yield the recorded frames, then end the stream."""

        async def _generate() -> AsyncIterator[Any]:
            """Drive ``__aiter__`` from the recorded frame list."""
            for message in self._messages:
                yield message

        return _generate()

    async def send_bytes(self, data: bytes) -> None:
        """Record a stdin write."""
        self.sent.append(data)


class _FakeK8s:
    """The ``kubernetes_asyncio`` surface ``_k8s_backend`` imports."""

    def __init__(self, streams: list[list[Any]]) -> None:
        """Serve one frame list per exec, in order."""
        self.streams = list(streams)
        self.sockets: list[_FakeSocket] = []
        self.calls: list[tuple] = []

    def next_socket(self) -> _FakeSocket:
        """The socket handed to the most recent exec."""
        return self.sockets[-1]


def _install_fake_sdk(fake: _FakeK8s) -> dict:
    """Point ``kubernetes_asyncio`` at ``fake``; return what to restore."""

    class _CoreV1Api:
        def __init__(self, api_client: Any = None) -> None:
            pass

        async def connect_get_namespaced_pod_exec(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> _FakeSocket:
            """Hand out the next scripted stream for this exec."""
            fake.calls.append((args, kwargs))
            sock = _FakeSocket(fake.streams.pop(0))
            fake.sockets.append(sock)
            return sock

    class _WsApiClient:
        def __init__(self, configuration: Any = None) -> None:
            pass

        async def __aenter__(self) -> "_WsApiClient":
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

    names = {
        "kubernetes_asyncio": types.ModuleType("kubernetes_asyncio"),
        "kubernetes_asyncio.client": types.ModuleType(
            "kubernetes_asyncio.client",
        ),
        "kubernetes_asyncio.stream": types.ModuleType(
            "kubernetes_asyncio.stream",
        ),
    }
    names["kubernetes_asyncio.client"].CoreV1Api = _CoreV1Api
    names["kubernetes_asyncio.stream"].WsApiClient = _WsApiClient
    names["kubernetes_asyncio"].client = names["kubernetes_asyncio.client"]
    names["kubernetes_asyncio"].stream = names["kubernetes_asyncio.stream"]

    saved = {name: sys.modules.get(name) for name in names}
    sys.modules.update(names)
    return saved


def _restore(saved: dict) -> None:
    """Put the real modules back for the rest of the suite."""
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _backend() -> K8sBackend:
    """A backend bound to a Pod that never has to exist."""
    return K8sBackend(
        api_client=types.SimpleNamespace(configuration=None),
        namespace="agentscope",
        pod_name="pod-1",
        container_name="main",
        workdir="/workspace",
    )


class K8sExecStatusTest(IsolatedAsyncioTestCase):
    """``exec_shell`` must read a missing verdict as unknown, not success."""

    def setUp(self) -> None:
        """Start each test with no fake SDK installed."""
        self._saved: dict = {}

    async def asyncTearDown(self) -> None:
        """Remove the fake SDK whatever the test did."""
        _restore(self._saved)
        self._saved = {}

    async def _exec(self, streams: list[list[Any]]) -> ExecResult:
        """Run one ``echo``-sized command over ``streams``."""
        fake = _FakeK8s(streams)
        self._saved = _install_fake_sdk(fake)
        return await _backend().exec_shell(["python", "train.py"])

    async def test_connection_reset_is_not_a_success(self) -> None:
        """A stream cut by an error frame reports -1 with its output."""
        result = await self._exec([[_stdout(b"epoch 0\n"), _reset()]])
        self.assertEqual(
            result,
            ExecResult(exit_code=-1, stdout=b"epoch 0\n", stderr=b""),
        )

    async def test_stream_ending_without_a_verdict_is_not_a_success(
        self,
    ) -> None:
        """A stream that stops before channel 3 reports -1, too."""
        result = await self._exec([[_stdout(b"partial\n")]])
        self.assertEqual(
            result,
            ExecResult(exit_code=-1, stdout=b"partial\n", stderr=b""),
        )

    async def test_success_verdict_still_reports_zero(self) -> None:
        """The ordinary happy path keeps returning ``ok()``."""
        result = await self._exec([[_stdout(b"hello\n"), SUCCESS]])
        self.assertEqual(
            result,
            ExecResult(exit_code=0, stdout=b"hello\n", stderr=b""),
        )

    async def test_reported_exit_code_survives(self) -> None:
        """A verdict the Pod did send is never overwritten."""
        result = await self._exec([[_stderr(b"boom\n"), EXIT_3]])
        self.assertEqual(
            result,
            ExecResult(exit_code=3, stdout=b"", stderr=b"boom\n"),
        )

    async def test_unparsable_verdict_still_reports_one(self) -> None:
        """A damaged verdict stays a failure rather than becoming -1."""
        result = await self._exec(
            [[_stdout(b"out\n"), _status(b"{not json")]],
        )
        self.assertEqual(
            result,
            ExecResult(exit_code=1, stdout=b"out\n", stderr=b""),
        )


class K8sWriteStatusTest(IsolatedAsyncioTestCase):
    """A cut write stream must not be reported as a landed file."""

    def setUp(self) -> None:
        """Start each test with no fake SDK installed."""
        self._saved: dict = {}

    async def asyncTearDown(self) -> None:
        """Remove the fake SDK whatever the test did."""
        _restore(self._saved)
        self._saved = {}

    async def _write(self, streams: list[list[Any]]) -> Any:
        """Write one file whose mkdir and tar use ``streams``."""
        fake = _FakeK8s([[SUCCESS], streams[0]])
        self._saved = _install_fake_sdk(fake)
        backend = _backend()
        outcome: Any = None
        try:
            await backend.write_file("/workspace/blob.bin", b"x" * 8)
        except RuntimeError as exc:
            outcome = exc
        return fake, outcome

    async def test_reset_during_write_raises(self) -> None:
        """A stream cut before the verdict fails the write loudly."""
        _fake, outcome = await self._write([[_stderr(b"cut\n"), _reset()]])
        self.assertEqual(
            type(outcome),
            RuntimeError,
        )
        self.assertEqual(
            str(outcome),
            "write to '/workspace/blob.bin' failed: the exec stream "
            "ended before tar reported an exit status, so the file "
            "may be truncated.",
        )

    async def test_success_verdict_completes_the_write(self) -> None:
        """The archived bytes go out on stdin and nothing is raised."""
        fake, outcome = await self._write([[SUCCESS]])
        self.assertEqual(outcome, None)
        sent = fake.next_socket().sent
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0][:1], b"\x00")
        self.assertEqual(sent[1], b"\x00")
        self.assertEqual(
            fake.calls[1],
            (
                ("pod-1", "agentscope"),
                {
                    "command": ["tar", "xf", "-", "-C", "/workspace"],
                    "container": "main",
                    "stderr": True,
                    "stdin": True,
                    "stdout": True,
                    "tty": False,
                    "_preload_content": False,
                },
            ),
        )
