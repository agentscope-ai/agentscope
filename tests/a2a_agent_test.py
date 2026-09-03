# -*- coding: utf-8 -*-
# flake8: noqa: E402
# pylint: disable=wrong-import-position
"""Tests for the A2A agent adapter."""
import base64
import json

from collections.abc import AsyncGenerator
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import pytest

pytest.importorskip("a2a", reason="A2A tests require the a2a extra")

from a2a import types
from a2a.client import ClientConfig, ClientFactory
from a2a.utils.constants import TransportProtocol
from google.protobuf import json_format
import httpx
from utils import AnyString

from agentscope.agent import A2AAgent
from agentscope.event import (
    CustomEvent,
    ReplyFinishedReason,
    DataBlockDeltaEvent,
    DataBlockStartEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
)
from agentscope.message import Base64Source, DataBlock, URLSource, UserMsg


def _state(name: str) -> int:
    """Resolve a protobuf task state by name."""
    return types.TaskState.Value(name)


def _card(
    *versions: str,
    protocol_binding: str = "JSONRPC",
) -> types.AgentCard:
    """Build a small Agent Card for tests."""
    return types.AgentCard(
        name="remote-agent",
        description="test agent",
        supported_interfaces=[
            types.AgentInterface(
                url=f"http://example.test/{version}",
                protocol_binding=protocol_binding,
                protocol_version=version,
            )
            for version in versions
        ],
    )


def _message_response(
    text: str,
    *,
    context_id: str = "context-1",
) -> types.StreamResponse:
    """Build a direct A2A Message response."""
    return types.StreamResponse(
        message=types.Message(
            message_id="response-message",
            context_id=context_id,
            role=types.Role.Value("ROLE_AGENT"),
            parts=[types.Part(text=text)],
        ),
    )


def _task_response(
    state: str,
    *,
    artifacts: list[types.Artifact] | None = None,
    status_message: types.Message | None = None,
) -> types.StreamResponse:
    """Build an A2A Task response."""
    status = types.TaskStatus(state=_state(state))
    if status_message is not None:
        status.message.CopyFrom(status_message)
    return types.StreamResponse(
        task=types.Task(
            id="task-1",
            context_id="context-1",
            status=status,
            artifacts=artifacts or [],
        ),
    )


class _FakeClient:
    """Deterministic fake for the official SDK Client interface."""

    def __init__(
        self,
        responses: list[list[object]],
        *,
        subscriptions: list[list[object]] | None = None,
        get_tasks: list[types.Task] | None = None,
        canceled_tasks: list[types.Task] | None = None,
        listed_tasks: list[types.ListTasksResponse] | None = None,
        close_errors: list[BaseException] | None = None,
    ) -> None:
        self.responses = responses
        self.subscriptions = subscriptions or []
        self.get_tasks = get_tasks or []
        self.canceled_tasks = canceled_tasks or []
        self.listed_tasks = listed_tasks or []
        self.close_errors = close_errors or []
        self.requests: list[types.SendMessageRequest] = []
        self.subscribe_requests: list[types.SubscribeToTaskRequest] = []
        self.get_requests: list[types.GetTaskRequest] = []
        self.cancel_requests: list[types.CancelTaskRequest] = []
        self.list_requests: list[types.ListTasksRequest] = []
        self.close_count = 0

    async def send_message(
        self,
        request: types.SendMessageRequest,
        *,
        context: Any = None,
    ) -> AsyncGenerator[types.StreamResponse, None]:
        """Record the request and yield one configured response stream."""
        del context
        self.requests.append(request)
        for response in self.responses.pop(0):
            if isinstance(response, BaseException):
                raise response
            yield response

    async def subscribe(
        self,
        request: types.SubscribeToTaskRequest,
        *,
        context: Any = None,
    ) -> AsyncGenerator[types.StreamResponse, None]:
        """Yield one configured task subscription."""
        del context
        self.subscribe_requests.append(request)
        for response in self.subscriptions.pop(0):
            if isinstance(response, BaseException):
                raise response
            yield response

    async def get_task(
        self,
        request: types.GetTaskRequest,
        *,
        context: Any = None,
    ) -> types.Task:
        """Return one configured Task snapshot."""
        del context
        self.get_requests.append(request)
        return self.get_tasks.pop(0)

    async def list_tasks(
        self,
        request: types.ListTasksRequest,
        *,
        context: Any = None,
    ) -> types.ListTasksResponse:
        """Return one configured page of Tasks."""
        del context
        self.list_requests.append(request)
        return self.listed_tasks.pop(0)

    async def cancel_task(
        self,
        request: types.CancelTaskRequest,
        *,
        context: Any = None,
    ) -> types.Task:
        """Return one configured cancellation result."""
        del context
        self.cancel_requests.append(request)
        return self.canceled_tasks.pop(0)

    async def close(self) -> None:
        """Record client closure."""
        self.close_count += 1
        if self.close_errors:
            raise self.close_errors.pop(0)


class A2AAgentConstructionTest(IsolatedAsyncioTestCase):
    """Test A2AAgent construction and compatibility boundaries."""

    async def test_default_client_configuration(self) -> None:
        """The default client streams over the two supported bindings."""
        client = _FakeClient([])
        card = _card("0.3", "1.0")
        with patch("a2a.client.ClientFactory") as factory_class:
            factory_class.return_value.create.return_value = client
            agent = A2AAgent(card)

        config = factory_class.call_args.args[0]
        self.assertTrue(config.streaming)
        self.assertFalse(config.polling)
        self.assertListEqual(
            [binding.value for binding in config.supported_protocol_bindings],
            ["JSONRPC", "HTTP+JSON"],
        )
        # The card reaches the SDK untouched, so the factory can fall back to
        # its A2A 0.3 compatibility transport when a peer offers nothing newer.
        self.assertIs(
            factory_class.return_value.create.call_args.args[0],
            card,
        )
        await agent.aclose()

    async def test_default_client_rejects_unsupported_binding(self) -> None:
        """Fail before the SDK factory sees an unusable advertised binding."""
        with self.assertRaisesRegex(
            ValueError,
            r"default client requires.*advertised bindings: \['GRPC'\]",
        ):
            A2AAgent(_card("1.0", protocol_binding="GRPC"))

    async def test_injected_client_may_use_another_binding(self) -> None:
        """Transport restrictions belong only to the default client path."""
        client = _FakeClient([])
        agent = A2AAgent(
            _card("1.0", protocol_binding="GRPC"),
            client=client,
        )
        await agent.aclose()
        self.assertEqual(client.close_count, 1)


class A2AAgentTest(IsolatedAsyncioTestCase):
    """Test the public A2AAgent interaction behavior."""

    async def test_official_sdk_jsonrpc_transport_smoke(self) -> None:
        """Run the adapter through the official SDK JSON-RPC transport."""
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            requests.append(payload)
            message = types.Message(
                message_id="remote-message",
                context_id="transport-context",
                role=types.Role.Value("ROLE_AGENT"),
                parts=[types.Part(text="through the SDK")],
            )
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "message": json_format.MessageToDict(message),
                    },
                },
            )

        httpx_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        )
        card = _card("1.0")
        client = ClientFactory(
            ClientConfig(
                httpx_client=httpx_client,
                streaming=True,
                polling=False,
                supported_protocol_bindings=[TransportProtocol.JSONRPC],
            ),
        ).create(card)
        async with A2AAgent(card, client=client) as agent:
            reply = await agent.reply(
                UserMsg(name="user", content="hello"),
            )

        self.assertEqual(reply.get_text_content(), "through the SDK")
        self.assertEqual(requests[0]["method"], "SendMessage")
        self.assertEqual(agent.context_id, "transport-context")
        self.assertTrue(httpx_client.is_closed)

    async def test_direct_message_and_context_continuity(self) -> None:
        """A direct Message maps to events and carries context forward."""
        client = _FakeClient(
            [[_message_response("first")], [_message_response("second")]],
        )
        agent = A2AAgent(_card("1.0"), client=client)

        events = [
            event
            async for event in agent.reply_stream(
                UserMsg(name="user", content="hello"),
            )
        ]
        self.assertEqual(
            [type(event) for event in events],
            [
                ReplyStartEvent,
                TextBlockStartEvent,
                TextBlockDeltaEvent,
                TextBlockEndEvent,
                ReplyEndEvent,
            ],
        )
        self.assertEqual(events[2].delta, "first")
        self.assertEqual(
            events[-1].metadata,
            {
                "a2a": {
                    "context_id": "context-1",
                    "task_id": None,
                    "task_state": None,
                    "artifact_ids": [],
                },
            },
        )

        reply = await agent.reply(UserMsg(name="user", content="again"))
        self.assertEqual(client.requests[0].message.context_id, "")
        self.assertEqual(client.requests[1].message.context_id, "context-1")
        self.assertDictEqual(
            reply.model_dump(),
            {
                "name": "remote-agent",
                "content": [
                    {
                        "type": "text",
                        "text": "second",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "role": "assistant",
                "id": AnyString(),
                "created_at": AnyString(),
                "finished_at": None,
                "finished_reason": ReplyFinishedReason.COMPLETED,
                "structured_output": None,
                "metadata": {
                    "a2a": {
                        "context_id": "context-1",
                        "task_id": None,
                        "task_state": None,
                        "artifact_ids": [],
                    },
                },
                "error": None,
                "usage": None,
            },
        )

    async def test_completed_task_artifact_is_not_duplicated(self) -> None:
        """A completed Task snapshot produces one artifact block."""
        artifact = types.Artifact(
            artifact_id="artifact-1",
            parts=[types.Part(text="answer")],
        )
        client = _FakeClient(
            [[_task_response("TASK_STATE_COMPLETED", artifacts=[artifact])]],
        )
        agent = A2AAgent(_card("1.0"), client=client)

        events = [
            event
            async for event in agent.reply_stream(
                UserMsg(name="user", content="hello"),
            )
        ]
        self.assertEqual(
            [
                event.delta
                for event in events
                if isinstance(event, TextBlockDeltaEvent)
            ],
            ["answer"],
        )
        self.assertEqual(
            events[-1].metadata["a2a"],
            {
                "context_id": "context-1",
                "task_id": "task-1",
                "task_state": "TASK_STATE_COMPLETED",
                "artifact_ids": ["artifact-1"],
            },
        )

    async def test_completed_task_uses_status_message_only_as_fallback(
        self,
    ) -> None:
        """A completed Task without artifacts may use its status message."""
        status_message = types.Message(
            message_id="status-message",
            role=types.Role.Value("ROLE_AGENT"),
            parts=[types.Part(text="fallback")],
        )
        client = _FakeClient(
            [
                [
                    _task_response(
                        "TASK_STATE_COMPLETED",
                        status_message=status_message,
                    ),
                ],
            ],
        )
        agent = A2AAgent(_card("1.0"), client=client)

        reply = await agent.reply(UserMsg(name="user", content="hello"))
        self.assertEqual(reply.get_text_content(), "fallback")

        progress = types.StreamResponse(
            status_update=types.TaskStatusUpdateEvent(
                task_id="task-1",
                context_id="context-1",
                status=types.TaskStatus(
                    state=_state("TASK_STATE_WORKING"),
                    message=types.Message(
                        message_id="progress",
                        role=types.Role.Value("ROLE_AGENT"),
                        parts=[types.Part(text="not final")],
                    ),
                ),
            ),
        )
        provisional_artifact = types.StreamResponse(
            artifact_update=types.TaskArtifactUpdateEvent(
                task_id="task-1",
                context_id="context-1",
                artifact=types.Artifact(
                    artifact_id="removed",
                    parts=[types.Part(text="provisional")],
                ),
            ),
        )
        agent = A2AAgent(
            _card("1.0"),
            client=_FakeClient(
                [
                    [
                        progress,
                        provisional_artifact,
                        _task_response("TASK_STATE_COMPLETED"),
                    ],
                ],
            ),
        )
        reply = await agent.reply(UserMsg(name="user", content="hello"))
        self.assertEqual(reply.content, [])
        self.assertEqual(reply.metadata["a2a"]["artifact_ids"], [])

    async def test_streamed_append_and_final_snapshot_are_reduced(
        self,
    ) -> None:
        """Append updates stream while the final snapshot is deduplicated."""
        updates = [
            types.StreamResponse(
                status_update=types.TaskStatusUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    status=types.TaskStatus(
                        state=_state("TASK_STATE_WORKING"),
                        message=types.Message(
                            message_id="working-message",
                            role=types.Role.Value("ROLE_AGENT"),
                            parts=[types.Part(text="not final output")],
                        ),
                    ),
                ),
            ),
            types.StreamResponse(
                artifact_update=types.TaskArtifactUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    artifact=types.Artifact(
                        artifact_id="artifact-1",
                        parts=[types.Part(text="hel")],
                    ),
                    append=False,
                ),
            ),
            types.StreamResponse(
                artifact_update=types.TaskArtifactUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    artifact=types.Artifact(
                        artifact_id="artifact-1",
                        parts=[types.Part(text="lo")],
                    ),
                    append=True,
                    last_chunk=True,
                ),
            ),
            _task_response(
                "TASK_STATE_COMPLETED",
                artifacts=[
                    types.Artifact(
                        artifact_id="artifact-1",
                        parts=[types.Part(text="hello")],
                    ),
                ],
            ),
        ]
        agent = A2AAgent(_card("1.0"), client=_FakeClient([updates]))

        events = [
            event
            async for event in agent.reply_stream(
                UserMsg(name="user", content="hello"),
            )
        ]
        self.assertEqual(
            [
                event.delta
                for event in events
                if isinstance(event, TextBlockDeltaEvent)
            ],
            ["hel", "lo"],
        )
        self.assertEqual(
            len([event for event in events if isinstance(event, CustomEvent)]),
            2,
        )
        self.assertEqual(
            len(
                [
                    event
                    for event in events
                    if isinstance(event, TextBlockStartEvent)
                ],
            ),
            1,
        )
        self.assertEqual(
            len(
                [
                    event
                    for event in events
                    if isinstance(event, TextBlockEndEvent)
                ],
            ),
            1,
        )

    async def test_completed_status_update_terminates_stream(self) -> None:
        """A terminal status update need not be followed by a Task snapshot."""
        responses = [
            types.StreamResponse(
                artifact_update=types.TaskArtifactUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    artifact=types.Artifact(
                        artifact_id="artifact-1",
                        parts=[types.Part(text="complete")],
                    ),
                ),
            ),
            types.StreamResponse(
                status_update=types.TaskStatusUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    status=types.TaskStatus(
                        state=_state("TASK_STATE_COMPLETED"),
                    ),
                ),
            ),
        ]
        agent = A2AAgent(_card("1.0"), client=_FakeClient([responses]))

        reply = await agent.reply(UserMsg(name="user", content="hello"))

        self.assertEqual(reply.get_text_content(), "complete")
        self.assertEqual(
            reply.metadata["a2a"]["task_state"],
            "TASK_STATE_COMPLETED",
        )

    async def test_raw_artifact_chunks_are_combined_once(self) -> None:
        """Raw chunks use independent base64 deltas and one final block."""
        updates = [
            types.StreamResponse(
                artifact_update=types.TaskArtifactUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    artifact=types.Artifact(
                        artifact_id="binary",
                        parts=[
                            types.Part(raw=b"first", media_type="image/png"),
                        ],
                    ),
                ),
            ),
            types.StreamResponse(
                artifact_update=types.TaskArtifactUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    artifact=types.Artifact(
                        artifact_id="binary",
                        parts=[
                            types.Part(raw=b"second", media_type="image/png"),
                        ],
                    ),
                    append=True,
                    last_chunk=True,
                ),
            ),
            _task_response(
                "TASK_STATE_COMPLETED",
                artifacts=[
                    types.Artifact(
                        artifact_id="binary",
                        parts=[
                            types.Part(
                                raw=b"firstsecond",
                                media_type="image/png",
                            ),
                        ],
                    ),
                ],
            ),
        ]
        client = _FakeClient([updates])
        agent = A2AAgent(_card("1.0"), client=client)
        reply = await agent.reply(UserMsg(name="user", content="image"))

        self.assertEqual(
            reply.content[0].source.data,
            base64.b64encode(b"firstsecond").decode("ascii"),
        )

    async def test_invalid_artifact_append_sequences_are_rejected(
        self,
    ) -> None:
        """Append requires a known artifact whose chunk stream is open."""
        unknown_append = types.StreamResponse(
            artifact_update=types.TaskArtifactUpdateEvent(
                task_id="task-1",
                artifact=types.Artifact(
                    artifact_id="unknown",
                    parts=[types.Part(text="chunk")],
                ),
                append=True,
            ),
        )
        agent = A2AAgent(
            _card("1.0"),
            client=_FakeClient([[unknown_append]]),
        )
        with self.assertRaisesRegex(RuntimeError, "unknown artifact"):
            await agent.reply(UserMsg(name="user", content="hello"))

        closed_then_append = [
            types.StreamResponse(
                artifact_update=types.TaskArtifactUpdateEvent(
                    task_id="task-1",
                    artifact=types.Artifact(
                        artifact_id="answer",
                        parts=[types.Part(text="done")],
                    ),
                    last_chunk=True,
                ),
            ),
            types.StreamResponse(
                artifact_update=types.TaskArtifactUpdateEvent(
                    task_id="task-1",
                    artifact=types.Artifact(
                        artifact_id="answer",
                        parts=[types.Part(text="too late")],
                    ),
                    append=True,
                ),
            ),
        ]
        agent = A2AAgent(
            _card("1.0"),
            client=_FakeClient([closed_then_append]),
        )
        with self.assertRaisesRegex(RuntimeError, "after its last chunk"):
            await agent.reply(UserMsg(name="user", content="hello"))

    async def test_observations_clear_only_after_success(self) -> None:
        """Observed inputs survive failures and clear on a successful reply."""
        client = _FakeClient(
            [[RuntimeError("transport failed")], [_message_response("ok")]],
        )
        agent = A2AAgent(_card("1.0"), client=client)
        await agent.observe(UserMsg(name="observer", content="observed"))

        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            await agent.reply(UserMsg(name="user", content="direct"))
        self.assertEqual(
            [part.text for part in client.requests[0].message.parts],
            ["observed", "direct"],
        )

        await agent.reply(UserMsg(name="user", content="retry"))
        self.assertEqual(
            [part.text for part in client.requests[1].message.parts],
            ["observed", "retry"],
        )
        self.assertEqual(
            agent._observed_msgs,  # pylint: disable=protected-access
            [],
        )

    async def test_unsupported_content_fails_explicitly(self) -> None:
        """Unsupported Part content is rejected rather than lost."""
        unsupported_output = A2AAgent(
            _card("1.0"),
            client=_FakeClient(
                [
                    [
                        types.StreamResponse(
                            message=types.Message(
                                message_id="response-message",
                                role=types.Role.Value("ROLE_AGENT"),
                                parts=[types.Part(raw=b"binary")],
                            ),
                        ),
                    ],
                ],
            ),
        )
        raw_reply = await unsupported_output.reply(
            UserMsg(name="user", content="hello"),
        )
        self.assertEqual(
            raw_reply.content[0].source.data,
            base64.b64encode(b"binary").decode("ascii"),
        )

        structured_part = types.Part()
        structured_part.data.struct_value.update({"key": "value"})
        structured_output = A2AAgent(
            _card("1.0"),
            client=_FakeClient(
                [
                    [
                        types.StreamResponse(
                            message=types.Message(
                                message_id="structured-message",
                                role=types.Role.Value("ROLE_AGENT"),
                                parts=[structured_part],
                            ),
                        ),
                    ],
                ],
            ),
        )
        with self.assertRaisesRegex(ValueError, "unsupported data"):
            await structured_output.reply(
                UserMsg(name="user", content="hello"),
            )

    async def test_artifact_replacement_after_output_is_rejected(self) -> None:
        """Replacement cannot contradict events already shown to callers."""
        responses = [
            types.StreamResponse(
                artifact_update=types.TaskArtifactUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    artifact=types.Artifact(
                        artifact_id="artifact-1",
                        parts=[types.Part(text="first")],
                    ),
                ),
            ),
            types.StreamResponse(
                artifact_update=types.TaskArtifactUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    artifact=types.Artifact(
                        artifact_id="artifact-1",
                        parts=[types.Part(text="replacement")],
                    ),
                ),
            ),
            _task_response(
                "TASK_STATE_COMPLETED",
                artifacts=[
                    types.Artifact(
                        artifact_id="artifact-1",
                        parts=[types.Part(text="replacement")],
                    ),
                ],
            ),
        ]
        agent = A2AAgent(
            _card("1.0"),
            client=_FakeClient([responses]),
        )
        with self.assertRaisesRegex(RuntimeError, "replacement.*unsupported"):
            await agent.reply(UserMsg(name="user", content="hello"))

    async def test_stream_rejects_context_id_switch(self) -> None:
        """One response stream cannot silently move to another context."""
        switched_responses = {
            "status": types.StreamResponse(
                status_update=types.TaskStatusUpdateEvent(
                    task_id="task-1",
                    context_id="context-2",
                    status=types.TaskStatus(
                        state=_state("TASK_STATE_COMPLETED"),
                    ),
                ),
            ),
            "artifact": types.StreamResponse(
                artifact_update=types.TaskArtifactUpdateEvent(
                    task_id="task-1",
                    context_id="context-2",
                    artifact=types.Artifact(
                        artifact_id="answer",
                        parts=[types.Part(text="wrong context")],
                    ),
                ),
            ),
            "task": types.StreamResponse(
                task=types.Task(
                    id="task-1",
                    context_id="context-2",
                    status=types.TaskStatus(
                        state=_state("TASK_STATE_COMPLETED"),
                    ),
                ),
            ),
        }
        for payload, switched_response in switched_responses.items():
            with self.subTest(payload=payload):
                agent = A2AAgent(
                    _card("1.0"),
                    client=_FakeClient(
                        [
                            [
                                _task_response("TASK_STATE_WORKING"),
                                switched_response,
                            ],
                        ],
                    ),
                )
                with self.assertRaisesRegex(
                    RuntimeError,
                    "changed context ID",
                ):
                    await agent.reply(UserMsg(name="user", content="hello"))
                self.assertEqual(agent.context_id, "context-1")

    async def test_direct_message_rejects_context_id_switch(self) -> None:
        """A direct Message must share the stream's established context."""
        responses = [
            types.StreamResponse(
                status_update=types.TaskStatusUpdateEvent(
                    task_id="task-1",
                    context_id="context-1",
                    status=types.TaskStatus(
                        state=_state("TASK_STATE_WORKING"),
                    ),
                ),
            ),
            _message_response("wrong context", context_id="context-2"),
        ]
        agent = A2AAgent(_card("1.0"), client=_FakeClient([responses]))
        with self.assertRaisesRegex(RuntimeError, "changed context ID"):
            await agent.reply(UserMsg(name="user", content="hello"))
        self.assertEqual(agent.context_id, "context-1")

    async def test_raw_and_url_parts_round_trip(self) -> None:
        """Raw bytes and URLs map without base64 corruption."""
        response = types.StreamResponse(
            message=types.Message(
                message_id="response-message",
                context_id="context-1",
                role=types.Role.Value("ROLE_AGENT"),
                parts=[
                    types.Part(
                        raw=b"response-bytes",
                        filename="response.bin",
                        media_type="application/octet-stream",
                    ),
                    types.Part(
                        url="https://example.test/result.png",
                        filename="result.png",
                        media_type="image/png",
                    ),
                ],
            ),
        )
        client = _FakeClient([[response]])
        agent = A2AAgent(_card("1.0"), client=client)
        request_data = base64.b64encode(b"request-bytes").decode("ascii")
        reply = await agent.reply(
            UserMsg(
                name="user",
                content=[
                    DataBlock(
                        source=Base64Source(
                            data=request_data,
                            media_type="application/octet-stream",
                        ),
                        name="request.bin",
                    ),
                    DataBlock(
                        source=URLSource(
                            url="https://example.test/input.png",
                            media_type="image/png",
                        ),
                    ),
                ],
            ),
        )
        self.assertEqual(
            client.requests[0].message.parts[0].raw,
            b"request-bytes",
        )
        self.assertEqual(
            client.requests[0].message.parts[1].url,
            "https://example.test/input.png",
        )
        self.assertEqual(
            reply.content[0].source.data,
            base64.b64encode(b"response-bytes").decode("ascii"),
        )
        self.assertEqual(
            str(reply.content[1].source.url),
            "https://example.test/result.png",
        )

    async def test_stream_exposes_raw_and_url_events(self) -> None:
        """Streaming exposes raw data and URL metadata without lossy text."""
        response = types.StreamResponse(
            message=types.Message(
                message_id="response-message",
                role=types.Role.Value("ROLE_AGENT"),
                parts=[
                    types.Part(raw=b"bytes", media_type="application/data"),
                    types.Part(
                        url="https://example.test/result",
                        media_type="application/data",
                    ),
                ],
            ),
        )
        agent = A2AAgent(_card("1.0"), client=_FakeClient([[response]]))
        events = [
            event
            async for event in agent.reply_stream(
                UserMsg(name="user", content="hello"),
            )
        ]
        data_starts = [
            event for event in events if isinstance(event, DataBlockStartEvent)
        ]
        self.assertEqual(len(data_starts), 2)
        self.assertEqual(
            [
                event.data
                for event in events
                if isinstance(event, DataBlockDeltaEvent)
            ],
            [base64.b64encode(b"bytes").decode("ascii")],
        )
        self.assertEqual(
            data_starts[1].metadata["a2a"]["url"],
            "https://example.test/result",
        )

    async def test_input_required_continues_same_task(self) -> None:
        """The next reply continues an input-required Task automatically."""
        client = _FakeClient(
            [
                [_task_response("TASK_STATE_INPUT_REQUIRED")],
                [
                    _task_response(
                        "TASK_STATE_COMPLETED",
                        artifacts=[
                            types.Artifact(
                                artifact_id="answer",
                                parts=[types.Part(text="done")],
                            ),
                        ],
                    ),
                ],
            ],
        )
        agent = A2AAgent(_card("1.0"), client=client)
        await agent.reply(UserMsg(name="user", content="start"))

        reply = await agent.reply(UserMsg(name="user", content="details"))
        self.assertEqual(client.requests[1].message.context_id, "context-1")
        self.assertEqual(client.requests[1].message.task_id, "task-1")
        self.assertEqual(reply.get_text_content(), "done")

    async def test_reply_follows_a_running_task_before_sending(self) -> None:
        """A running Task is followed, by subscription or the GetTask race
        fallback, before the new message is sent."""
        from a2a.utils.errors import UnsupportedOperationError

        completed_task = _task_response(
            "TASK_STATE_COMPLETED",
            artifacts=[
                types.Artifact(
                    artifact_id="answer",
                    parts=[types.Part(text="followed")],
                ),
            ],
        ).task
        client = _FakeClient(
            [
                [_task_response("TASK_STATE_WORKING")],
                [
                    _task_response(
                        "TASK_STATE_COMPLETED",
                        artifacts=[
                            types.Artifact(
                                artifact_id="answer",
                                parts=[types.Part(text="done")],
                            ),
                        ],
                    ),
                ],
            ],
            subscriptions=[[UnsupportedOperationError()]],
            get_tasks=[completed_task],
        )
        agent = A2AAgent(_card("1.0"), client=client)
        await agent.reply(UserMsg(name="user", content="start"))
        self.assertEqual(agent.task_state, "TASK_STATE_WORKING")

        reply = await agent.reply(UserMsg(name="user", content="next"))
        self.assertEqual(client.subscribe_requests[0].id, "task-1")
        self.assertEqual(client.get_requests[0].id, "task-1")
        # The followed Task completed, so the new message starts a new Task
        # and its own result is what the caller gets back.
        self.assertEqual(client.requests[1].message.task_id, "")
        self.assertEqual(reply.get_text_content(), "done")

    async def test_get_and_cancel_task_update_remote_state(self) -> None:
        """Task inspection and cancellation use the latest remote ID."""
        working = _task_response("TASK_STATE_WORKING").task
        canceled = _task_response("TASK_STATE_CANCELED").task
        client = _FakeClient(
            [[_task_response("TASK_STATE_WORKING")]],
            get_tasks=[working],
            canceled_tasks=[canceled],
        )
        agent = A2AAgent(_card("1.0"), client=client)
        await agent.reply(UserMsg(name="user", content="start"))

        await agent.get_task()
        result = await agent.cancel_task()
        self.assertEqual(result.status.state, _state("TASK_STATE_CANCELED"))
        self.assertEqual(agent.task_state, "TASK_STATE_CANCELED")
        self.assertEqual(client.get_requests[0].id, "task-1")
        self.assertEqual(client.cancel_requests[0].id, "task-1")

    async def test_lifecycle_and_compress_context_no_op(self) -> None:
        """Compression is a no-op and closure is idempotent."""
        client = _FakeClient([])
        agent = A2AAgent(_card("1.0"), client=client)
        await agent.compress_context()
        await agent.aclose()
        await agent.aclose()
        self.assertEqual(client.close_count, 1)

        with self.assertRaisesRegex(RuntimeError, "closed"):
            await agent.reply(UserMsg(name="user", content="hello"))

    async def test_close_failure_can_be_retried(self) -> None:
        """A failed transport close does not falsely mark the agent closed."""
        client = _FakeClient([], close_errors=[RuntimeError("close failed")])
        agent = A2AAgent(_card("1.0"), client=client)
        with self.assertRaisesRegex(RuntimeError, "close failed"):
            await agent.aclose()
        await agent.aclose()
        self.assertEqual(client.close_count, 2)


class A2AAgentTaskLifecycleTest(IsolatedAsyncioTestCase):
    """Tests for remote Task outcomes and for resuming a stored context."""

    async def test_task_state_decides_the_reply_outcome(self) -> None:
        """An interrupted Task ends the reply; a failed one reports an error."""
        agent = A2AAgent(
            _card("1.0"),
            client=_FakeClient(
                [
                    [
                        _task_response(
                            "TASK_STATE_INPUT_REQUIRED",
                            status_message=types.Message(
                                message_id="ask",
                                role=types.Role.Value("ROLE_AGENT"),
                                parts=[
                                    types.Part(text="Where are you flying?"),
                                ],
                            ),
                        ),
                    ],
                ],
            ),
        )
        reply = await agent.reply(UserMsg(name="user", content="book a seat"))
        self.assertDictEqual(
            reply.model_dump(),
            {
                "name": "remote-agent",
                "content": [
                    {
                        "type": "text",
                        "text": "Where are you flying?",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": None,
                    },
                ],
                "role": "assistant",
                "id": AnyString(),
                "created_at": AnyString(),
                "finished_at": None,
                "finished_reason": ReplyFinishedReason.COMPLETED,
                "structured_output": None,
                "metadata": {
                    "a2a": {
                        "context_id": "context-1",
                        "task_id": "task-1",
                        "task_state": "TASK_STATE_INPUT_REQUIRED",
                        "artifact_ids": [],
                    },
                },
                "error": None,
                "usage": None,
            },
        )

        outcomes = []
        for state in [
            "TASK_STATE_AUTH_REQUIRED",
            "TASK_STATE_CANCELED",
            "TASK_STATE_FAILED",
            "TASK_STATE_REJECTED",
            # A stream that ends while the Task still runs never resolved.
            "TASK_STATE_WORKING",
        ]:
            other = A2AAgent(
                _card("1.0"),
                client=_FakeClient([[_task_response(state)]]),
            )
            other_reply = await other.reply(
                UserMsg(name="user", content="hello"),
            )
            outcomes.append(
                (
                    state,
                    other_reply.finished_reason,
                    other_reply.error and other_reply.error.message,
                ),
            )
        self.assertListEqual(
            outcomes,
            [
                (
                    "TASK_STATE_AUTH_REQUIRED",
                    ReplyFinishedReason.COMPLETED,
                    None,
                ),
                ("TASK_STATE_CANCELED", ReplyFinishedReason.INTERRUPTED, None),
                (
                    "TASK_STATE_FAILED",
                    ReplyFinishedReason.ERROR,
                    "The remote A2A task ended in TASK_STATE_FAILED.",
                ),
                (
                    "TASK_STATE_REJECTED",
                    ReplyFinishedReason.ERROR,
                    "The remote A2A task ended in TASK_STATE_REJECTED.",
                ),
                (
                    "TASK_STATE_WORKING",
                    ReplyFinishedReason.ERROR,
                    "The remote A2A task ended in TASK_STATE_WORKING.",
                ),
            ],
        )

        detailed = A2AAgent(
            _card("1.0"),
            client=_FakeClient(
                [
                    [
                        _task_response(
                            "TASK_STATE_FAILED",
                            status_message=types.Message(
                                message_id="why",
                                role=types.Role.Value("ROLE_AGENT"),
                                parts=[
                                    types.Part(text="Upstream API is down"),
                                ],
                            ),
                        ),
                    ],
                ],
            ),
        )
        detailed_reply = await detailed.reply(
            UserMsg(name="user", content="hello"),
        )
        self.assertEqual(
            detailed_reply.error.message,
            "Upstream API is down",
        )

    async def test_stored_context_is_reused_and_stale_task_degrades(
        self,
    ) -> None:
        """A restored context continues; a dropped Task starts a new one."""
        client = _FakeClient(
            [
                [
                    _task_response(
                        "TASK_STATE_COMPLETED",
                        artifacts=[
                            types.Artifact(
                                artifact_id="answer",
                                parts=[types.Part(text="done")],
                            ),
                        ],
                    ),
                ],
            ],
            get_tasks=[_task_response("TASK_STATE_INPUT_REQUIRED").task],
        )
        agent = A2AAgent(
            _card("1.0"),
            client=client,
            context_id="stored-context",
            task_id="stored-task",
        )
        self.assertEqual(agent.context_id, "stored-context")
        self.assertEqual(agent.task_id, "stored-task")
        self.assertIsNone(agent.task_state)

        reply = await agent.reply(UserMsg(name="user", content="hello"))
        self.assertEqual(reply.get_text_content(), "done")
        self.assertEqual(
            client.requests[0].message.context_id,
            "stored-context",
        )
        # The stored Task's state is unknown locally, so the turn opens a new
        # Task in the stored context instead of continuing a possibly dead one.
        self.assertEqual(client.requests[0].message.task_id, "")

        restored = A2AAgent(
            _card("1.0"),
            client=_FakeClient(
                [],
                get_tasks=[
                    _task_response("TASK_STATE_INPUT_REQUIRED").task,
                ],
            ),
            context_id="stored-context",
            task_id="stored-task",
        )
        await restored.get_task()
        self.assertEqual(restored.task_state, "TASK_STATE_INPUT_REQUIRED")

    async def test_list_tasks_filters_by_context_and_follows_pages(
        self,
    ) -> None:
        """Listing spans every page and scopes to the current context."""
        first = _task_response("TASK_STATE_INPUT_REQUIRED").task
        second = _task_response("TASK_STATE_COMPLETED").task
        client = _FakeClient(
            [],
            listed_tasks=[
                types.ListTasksResponse(
                    tasks=[first],
                    next_page_token="page-2",
                ),
                types.ListTasksResponse(tasks=[second]),
            ],
        )
        agent = A2AAgent(
            _card("1.0"),
            client=client,
            context_id="stored-context",
        )

        tasks = await agent.list_tasks("TASK_STATE_INPUT_REQUIRED")
        self.assertListEqual(tasks, [first, second])
        self.assertListEqual(
            [
                (
                    request.context_id,
                    request.page_token,
                    request.status,
                )
                for request in client.list_requests
            ],
            [
                ("stored-context", "", _state("TASK_STATE_INPUT_REQUIRED")),
                (
                    "stored-context",
                    "page-2",
                    _state("TASK_STATE_INPUT_REQUIRED"),
                ),
            ],
        )

        empty = A2AAgent(_card("1.0"), client=_FakeClient([]))
        with self.assertRaisesRegex(RuntimeError, "no remote context"):
            await empty.list_tasks()
