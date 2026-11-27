# -*- coding: utf-8 -*-
"""Regression test for a cancelled WebRTC offer request.

Once the offer handshake has registered a :class:`WebRTCSession` and
started it, the session keeps the distributed chat lock while it waits
for the browser. If the HTTP request is cancelled while it waits for
that lock (the client closed the tab, the proxy timed out), the handler
must tear the session down; otherwise the runner keeps the session
"running" forever and every later turn or offer is rejected with 409.
"""

import asyncio
import sys
import types
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app._router._realtime import _create_realtime_offer

# Imported so ``unittest.mock.patch`` can resolve the submodules below.
# pylint: disable=unused-import
import agentscope.app._service._webrtc_audio_transport  # noqa: F401
import agentscope.app._service._webrtc_session  # noqa: F401
from agentscope.app._router._schema import RealtimeOfferRequest
from agentscope.app.storage import (
    RealtimeModelConfig,
    SessionConfig,
    SessionRecord,
)


class _FakePeerConnection:
    """Minimal stand-in for ``aiortc.RTCPeerConnection``."""

    last: "_FakePeerConnection | None" = None

    def __init__(self, configuration: object = None) -> None:
        del configuration
        self.handlers: dict[str, object] = {}
        self.closed = False
        self.connectionState = "new"
        _FakePeerConnection.last = self

    def on(self, event: str) -> object:
        """Register (and return) a decorator like aiortc does."""

        def _register(handler: object) -> object:
            self.handlers[event] = handler
            return handler

        return _register

    async def setRemoteDescription(self, description: object) -> None:
        """Accept the browser offer."""

    def addTrack(self, track: object) -> None:
        """Accept the output track."""

    async def createAnswer(self) -> object:
        """Produce an opaque answer."""
        return object()

    async def setLocalDescription(self, answer: object) -> None:
        """Record the answer."""

    @property
    def localDescription(self) -> object:
        """Return the negotiated answer."""
        return SimpleNamespace(sdp="answer-sdp", type="answer")

    async def close(self) -> None:
        """Record the close."""
        self.closed = True


class _FakeTransport:
    """Minimal stand-in for ``WebRTCAudioTransport``."""

    last: "_FakeTransport | None" = None

    def __init__(self, **_: object) -> None:
        self.closed = False
        self.on_disconnect = None
        self.output_track = object()
        _FakeTransport.last = self

    async def close(self) -> None:
        """Record the close."""
        self.closed = True


class _WedgedRunner:
    """A runner whose lock acquisition never completes."""

    last: "_WedgedRunner | None" = None

    def __init__(self, *, transport: object, **_: object) -> None:
        self.transport = transport
        self.started = False
        self.closed = False
        self.waiting = asyncio.Event()
        _WedgedRunner.last = self

    def start(self) -> None:
        """Mark the runner as running."""
        self.started = True

    def request_close(self) -> None:
        """Accept the transport's disconnect callback."""

    async def wait_until_lock_acquired(self, timeout: float) -> bool:
        """Block until the request is cancelled."""
        del timeout
        self.waiting.set()
        await asyncio.Event().wait()
        return False

    async def close(self) -> None:
        """Record the close."""
        self.closed = True


class _Bus:
    """Report the session as free."""

    async def is_locked(self, key: str) -> bool:
        """No run holds the session."""
        del key
        return False


class _Storage:
    """Return one session with a realtime model configured."""

    def __init__(self, session: SessionRecord) -> None:
        self.session = session

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Return a detached copy when the triple matches."""
        if (
            user_id != self.session.user_id
            or agent_id != self.session.agent_id
            or session_id != self.session.id
        ):
            return None
        return self.session.model_copy(deep=True)


class _Access:
    """Accept any agent the route resolves."""

    async def resolve_agent(self, user_id: str, agent_id: str) -> object:
        """Return an opaque agent handle."""
        del user_id, agent_id
        return object()


async def _get_realtime_model(
    user_id: str,
    config: object,
    access: object,
) -> object:
    """Return the sample rates the transport needs."""
    del user_id, config, access
    return SimpleNamespace(input_sample_rate=16000, output_sample_rate=24000)


def _fake_aiortc() -> types.ModuleType:
    """Build the subset of aiortc this endpoint imports."""
    module = types.ModuleType("aiortc")
    module.RTCConfiguration = lambda **kwargs: kwargs
    module.RTCIceServer = lambda **kwargs: kwargs
    module.RTCPeerConnection = _FakePeerConnection
    module.RTCSessionDescription = lambda **kwargs: kwargs
    return module


class RealtimeOfferCancelTest(IsolatedAsyncioTestCase):
    """A cancelled offer must not leave its runner holding the lock."""

    async def test_cancelled_offer_closes_runner_and_connection(self) -> None:
        """Cancellation while waiting for the lock tears everything down."""
        session = SessionRecord(
            id="session-1",
            user_id="user-1",
            agent_id="agent-1",
            config=SessionConfig(
                workspace_id="workspace-1",
                realtime_model_config=RealtimeModelConfig(
                    type="test",
                    credential_id="credential-1",
                    model="test-model",
                    parameters={},
                ),
            ),
        )
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    realtime_connections={},
                    realtime_ice_servers=[],
                ),
            ),
        )
        realtime_service = SimpleNamespace(
            create_agent=None,
        )

        with (
            patch.dict(sys.modules, {"aiortc": _fake_aiortc()}),
            patch(
                "agentscope.app._service._webrtc_audio_transport."
                "WebRTCAudioTransport",
                _FakeTransport,
            ),
            patch(
                "agentscope.app._service._webrtc_session.WebRTCSession",
                _WedgedRunner,
            ),
            patch(
                "agentscope.app._router._realtime.get_realtime_model",
                new=_get_realtime_model,
            ),
        ):
            task = asyncio.create_task(
                _create_realtime_offer(
                    session_id=session.id,
                    body=RealtimeOfferRequest(
                        agent_id=session.agent_id,
                        sdp="offer-sdp",
                    ),
                    request=request,
                    user_id=session.user_id,
                    storage=_Storage(session),
                    message_bus=_Bus(),
                    access=_Access(),
                    realtime_service=realtime_service,
                ),
            )
            for _ in range(200):
                if _WedgedRunner.last is not None:
                    break
                await asyncio.sleep(0.01)
            runner = _WedgedRunner.last
            self.assertIsNotNone(runner)
            await asyncio.wait_for(runner.waiting.wait(), timeout=1.0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1.0)

        self.assertIsNotNone(_FakeTransport.last)
        self.assertIsNotNone(_FakePeerConnection.last)
        self.assertTrue(runner.started)
        self.assertTrue(
            runner.closed,
            "the cancelled request left its runner running",
        )
        self.assertTrue(
            _FakeTransport.last.closed,
            "the cancelled request left its transport open",
        )
        self.assertTrue(
            _FakePeerConnection.last.closed,
            "the cancelled request left its peer connection open",
        )
