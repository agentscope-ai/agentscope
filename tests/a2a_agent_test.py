# -*- coding: utf-8 -*-
# flake8: noqa: E402
# pylint: disable=wrong-import-position
"""Tests for the A2A agent adapter."""
from collections.abc import AsyncGenerator
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import pytest

pytest.importorskip("a2a", reason="A2A tests require the a2a extra")

from a2a import types
from a2a.utils.errors import TaskNotFoundError
from utils import AnyString

from agentscope.agent import A2AAgent
from agentscope.message import UserMsg
from agentscope.state import A2AAgentState


class _FakeClient:
    """Deterministic fake for the official SDK Client interface."""

    def __init__(
        self,
        responses: list[list[types.StreamResponse]] | None = None,
        *,
        get_tasks: list[types.Task | BaseException] | None = None,
    ) -> None:
        self.responses = responses or []
        self.get_tasks = get_tasks or []
        self.requests: list[types.SendMessageRequest] = []
        self.get_requests: list[types.GetTaskRequest] = []
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
        task = self.get_tasks.pop(0)
        if isinstance(task, BaseException):
            raise task
        return task

    async def close(self) -> None:
        """Record client closure."""
        self.close_count += 1


class A2AAgentConstructionTest(IsolatedAsyncioTestCase):
    """Test A2AAgent construction and transport selection."""

    async def test_default_client_configuration(self) -> None:
        """The default client streams over the two supported bindings."""
        client = _FakeClient()
        card = types.AgentCard(
            name="remote-agent",
            description="test agent",
            supported_interfaces=[
                types.AgentInterface(
                    url="http://example.test/0.3",
                    protocol_binding="JSONRPC",
                    protocol_version="0.3",
                ),
                types.AgentInterface(
                    url="http://example.test/1.0",
                    protocol_binding="JSONRPC",
                    protocol_version="1.0",
                ),
            ],
        )
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
        self.assertEqual(agent.name, "remote-agent")
        await agent.aclose()

    async def test_injected_client_may_use_another_binding(self) -> None:
        """Transport restrictions belong only to the default client path."""
        client = _FakeClient()
        agent = A2AAgent(
            types.AgentCard(
                name="remote-agent",
                description="test agent",
                supported_interfaces=[
                    types.AgentInterface(
                        url="http://example.test/1.0",
                        protocol_binding="GRPC",
                        protocol_version="1.0",
                    ),
                ],
            ),
            client=client,
        )
        await agent.aclose()
        await agent.aclose()
        self.assertEqual(client.close_count, 1)


class A2AAgentReplyTest(IsolatedAsyncioTestCase):
    """Test how A2A responses become AgentScope events and messages."""

    def setUp(self) -> None:
        """Build a card shared by the reply tests."""
        self.card = types.AgentCard(
            name="remote-agent",
            description="test agent",
            supported_interfaces=[
                types.AgentInterface(
                    url="http://example.test/1.0",
                    protocol_binding="JSONRPC",
                    protocol_version="1.0",
                ),
            ],
        )

    async def test_direct_message_response(self) -> None:
        """A bare Message becomes text blocks and leaves no Task behind."""
        client = _FakeClient(
            [
                [
                    types.StreamResponse(
                        message=types.Message(
                            message_id="msg-1",
                            context_id="context-1",
                            role=types.Role.ROLE_AGENT,
                            parts=[types.Part(text="hello")],
                        ),
                    ),
                ],
            ],
        )
        agent = A2AAgent(self.card, client=client)

        events = [
            _.model_dump(mode="json")
            async for _ in agent.reply_stream(
                UserMsg(name="user", content="hi"),
            )
        ]

        self.assertListEqual(
            events,
            [
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "REPLY_START",
                    "session_id": AnyString(),
                    "reply_id": AnyString(),
                    "name": "remote-agent",
                    "role": "assistant",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TEXT_BLOCK_START",
                    "reply_id": AnyString(),
                    "block_id": AnyString(),
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TEXT_BLOCK_DELTA",
                    "reply_id": AnyString(),
                    "block_id": AnyString(),
                    "delta": "hello",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {"a2a": {"message_id": "msg-1"}},
                    "type": "TEXT_BLOCK_END",
                    "reply_id": AnyString(),
                    "block_id": AnyString(),
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {"a2a": {"context_id": "context-1"}},
                    "type": "REPLY_END",
                    "session_id": AnyString(),
                    "reply_id": AnyString(),
                    "finished_reason": "completed",
                    "error": None,
                },
            ],
        )
        self.assertEqual(agent.state.context_id, "context-1")
        self.assertIsNone(agent.state.task_id)
        await agent.aclose()

    async def test_streamed_artifact_chunks_are_one_text_block(self) -> None:
        """Appended chunks continue one block; a binary Part ends it."""
        client = _FakeClient(
            [
                [
                    types.StreamResponse(
                        artifact_update=types.TaskArtifactUpdateEvent(
                            task_id="task-1",
                            context_id="context-1",
                            artifact=types.Artifact(
                                artifact_id="artifact-1",
                                parts=[types.Part(text="first ")],
                            ),
                        ),
                    ),
                    types.StreamResponse(
                        artifact_update=types.TaskArtifactUpdateEvent(
                            task_id="task-1",
                            context_id="context-1",
                            artifact=types.Artifact(
                                artifact_id="artifact-1",
                                parts=[types.Part(text="second")],
                            ),
                            append=True,
                        ),
                    ),
                    types.StreamResponse(
                        artifact_update=types.TaskArtifactUpdateEvent(
                            task_id="task-1",
                            context_id="context-1",
                            artifact=types.Artifact(
                                artifact_id="artifact-1",
                                parts=[
                                    types.Part(
                                        raw=b"bytes",
                                        media_type="image/png",
                                        filename="chart.png",
                                    ),
                                ],
                            ),
                            append=True,
                            last_chunk=True,
                        ),
                    ),
                    types.StreamResponse(
                        status_update=types.TaskStatusUpdateEvent(
                            task_id="task-1",
                            context_id="context-1",
                            status=types.TaskStatus(
                                state=types.TaskState.TASK_STATE_COMPLETED,
                            ),
                        ),
                    ),
                ],
            ],
        )
        agent = A2AAgent(self.card, client=client)

        msg = await agent.reply(UserMsg(name="user", content="draw"))

        self.assertDictEqual(
            msg.model_dump(mode="json"),
            {
                "id": AnyString(),
                "name": "remote-agent",
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "first second",
                        "id": AnyString(),
                        "created_at": AnyString(),
                        "finished_at": AnyString(),
                    },
                    {
                        "type": "data",
                        "id": AnyString(),
                        "source": {
                            "type": "base64",
                            "data": "Ynl0ZXM=",
                            "media_type": "image/png",
                        },
                        "name": "chart.png",
                        "created_at": AnyString(),
                        "finished_at": AnyString(),
                    },
                ],
                "metadata": {"a2a": {"context_id": "context-1"}},
                "created_at": AnyString(),
                "finished_at": AnyString(),
                "finished_reason": "completed",
                "error": None,
                "usage": None,
                "structured_output": None,
            },
        )
        # A completed Task is over, so the next reply starts a new one.
        self.assertIsNone(agent.state.task_id)
        await agent.aclose()

    async def test_task_snapshot_yields_artifacts_then_status(self) -> None:
        """A full Task snapshot is reduced in the order it presents data."""
        client = _FakeClient(
            [
                [
                    types.StreamResponse(
                        task=types.Task(
                            id="task-1",
                            context_id="context-1",
                            status=types.TaskStatus(
                                state=types.TaskState.TASK_STATE_COMPLETED,
                                message=types.Message(
                                    message_id="msg-1",
                                    role=types.Role.ROLE_AGENT,
                                    parts=[types.Part(text="done")],
                                ),
                            ),
                            artifacts=[
                                types.Artifact(
                                    artifact_id="artifact-1",
                                    parts=[types.Part(text="report")],
                                ),
                            ],
                        ),
                    ),
                ],
            ],
        )
        agent = A2AAgent(self.card, client=client)

        events = [
            _.model_dump(mode="json")
            async for _ in agent.reply_stream(
                UserMsg(name="user", content="go"),
            )
        ]

        self.assertListEqual(
            events,
            [
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "REPLY_START",
                    "session_id": AnyString(),
                    "reply_id": AnyString(),
                    "name": "remote-agent",
                    "role": "assistant",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TEXT_BLOCK_START",
                    "reply_id": AnyString(),
                    "block_id": AnyString(),
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TEXT_BLOCK_DELTA",
                    "reply_id": AnyString(),
                    "block_id": AnyString(),
                    "delta": "report",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {
                        "a2a": {
                            "task_id": "task-1",
                            "artifact_id": "artifact-1",
                        },
                    },
                    "type": "TEXT_BLOCK_END",
                    "reply_id": AnyString(),
                    "block_id": AnyString(),
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TEXT_BLOCK_START",
                    "reply_id": AnyString(),
                    "block_id": AnyString(),
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "type": "TEXT_BLOCK_DELTA",
                    "reply_id": AnyString(),
                    "block_id": AnyString(),
                    "delta": "done",
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {"a2a": {"task_id": "task-1"}},
                    "type": "TEXT_BLOCK_END",
                    "reply_id": AnyString(),
                    "block_id": AnyString(),
                },
                {
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {"a2a": {"context_id": "context-1"}},
                    "type": "REPLY_END",
                    "session_id": AnyString(),
                    "reply_id": AnyString(),
                    "finished_reason": "completed",
                    "error": None,
                },
            ],
        )
        await agent.aclose()

    async def test_raw_and_url_parts_become_data_blocks(self) -> None:
        """Both binary forms of a Part survive the round trip."""
        client = _FakeClient(
            [
                [
                    types.StreamResponse(
                        message=types.Message(
                            message_id="msg-1",
                            context_id="context-1",
                            role=types.Role.ROLE_AGENT,
                            parts=[
                                types.Part(
                                    raw=b"bytes",
                                    media_type="image/png",
                                    filename="chart.png",
                                ),
                                types.Part(
                                    url="https://example.test/report.pdf",
                                    media_type="application/pdf",
                                ),
                            ],
                        ),
                    ),
                ],
            ],
        )
        agent = A2AAgent(self.card, client=client)

        msg = await agent.reply(UserMsg(name="user", content="files"))

        self.assertListEqual(
            [_.model_dump(mode="json") for _ in msg.content],
            [
                {
                    "type": "data",
                    "id": AnyString(),
                    "source": {
                        "type": "base64",
                        "data": "Ynl0ZXM=",
                        "media_type": "image/png",
                    },
                    "name": "chart.png",
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                },
                {
                    "type": "data",
                    "id": AnyString(),
                    "source": {
                        "type": "url",
                        "url": "https://example.test/report.pdf",
                        "media_type": "application/pdf",
                    },
                    "name": None,
                    "created_at": AnyString(),
                    "finished_at": AnyString(),
                },
            ],
        )
        await agent.aclose()

    async def test_unsupported_part_is_rejected(self) -> None:
        """An empty Part carries no content the adapter can map."""
        client = _FakeClient(
            [
                [
                    types.StreamResponse(
                        message=types.Message(
                            message_id="msg-1",
                            context_id="context-1",
                            role=types.Role.ROLE_AGENT,
                            parts=[types.Part()],
                        ),
                    ),
                ],
            ],
        )
        agent = A2AAgent(self.card, client=client)

        with self.assertRaises(ValueError) as error:
            await agent.reply(UserMsg(name="user", content="hi"))

        self.assertEqual(
            str(error.exception),
            "A2AAgent supports text, raw, and URL parts; got unsupported "
            "empty content.",
        )
        await agent.aclose()

    async def test_input_blocks_become_parts_of_one_message(self) -> None:
        """Observed messages lead the input inside a single user Message."""
        client = _FakeClient(
            [
                [
                    types.StreamResponse(
                        message=types.Message(
                            message_id="msg-1",
                            context_id="context-1",
                            role=types.Role.ROLE_AGENT,
                            parts=[types.Part(text="ok")],
                        ),
                    ),
                ],
            ],
        )
        agent = A2AAgent(self.card, client=client)

        await agent.observe(UserMsg(name="user", content="earlier"))
        await agent.reply(UserMsg(name="user", content="later"))

        self.assertListEqual(
            [_.text for _ in client.requests[0].message.parts],
            ["earlier", "later"],
        )
        self.assertEqual(client.requests[0].message.role, types.Role.ROLE_USER)
        # Observations are consumed by the reply that sends them.
        self.assertListEqual(agent.state.observed_context, [])
        await agent.aclose()

    async def test_reply_without_any_input_is_rejected(self) -> None:
        """There is nothing to send without input or observed messages."""
        agent = A2AAgent(self.card, client=_FakeClient())

        with self.assertRaises(ValueError) as error:
            await agent.reply()

        self.assertEqual(
            str(error.exception),
            "A2AAgent reply requires at least one message.",
        )
        await agent.aclose()


class A2AAgentTaskContinuationTest(IsolatedAsyncioTestCase):
    """Test which remote Task the next message continues."""

    def setUp(self) -> None:
        """Build a card shared by the continuation tests."""
        self.card = types.AgentCard(
            name="remote-agent",
            description="test agent",
            supported_interfaces=[
                types.AgentInterface(
                    url="http://example.test/1.0",
                    protocol_binding="JSONRPC",
                    protocol_version="1.0",
                ),
            ],
        )

    async def test_final_state_decides_the_reply_outcome(self) -> None:
        """The state a stream ends on sets both outcome and continuation."""
        outcomes = []
        for state in [
            types.TaskState.TASK_STATE_COMPLETED,
            types.TaskState.TASK_STATE_INPUT_REQUIRED,
            types.TaskState.TASK_STATE_AUTH_REQUIRED,
            types.TaskState.TASK_STATE_CANCELED,
            types.TaskState.TASK_STATE_FAILED,
            types.TaskState.TASK_STATE_REJECTED,
        ]:
            client = _FakeClient(
                [
                    [
                        types.StreamResponse(
                            status_update=types.TaskStatusUpdateEvent(
                                task_id="task-1",
                                context_id="context-1",
                                status=types.TaskStatus(state=state),
                            ),
                        ),
                    ],
                ],
            )
            agent = A2AAgent(self.card, client=client)
            msg = await agent.reply(UserMsg(name="user", content="hi"))
            outcomes.append(
                (
                    types.TaskState.Name(state),
                    msg.finished_reason.value,
                    agent.state.task_id,
                ),
            )
            await agent.aclose()

        self.assertListEqual(
            outcomes,
            [
                ("TASK_STATE_COMPLETED", "completed", None),
                ("TASK_STATE_INPUT_REQUIRED", "completed", "task-1"),
                ("TASK_STATE_AUTH_REQUIRED", "completed", "task-1"),
                ("TASK_STATE_CANCELED", "interrupted", None),
                ("TASK_STATE_FAILED", "error", None),
                ("TASK_STATE_REJECTED", "error", None),
            ],
        )

    async def test_task_waiting_for_input_is_continued(self) -> None:
        """The next message joins the Task the server is waiting on."""
        client = _FakeClient(
            [
                [
                    types.StreamResponse(
                        status_update=types.TaskStatusUpdateEvent(
                            task_id="task-1",
                            context_id="context-1",
                            status=types.TaskStatus(
                                state=(
                                    types.TaskState.TASK_STATE_INPUT_REQUIRED
                                ),
                                message=types.Message(
                                    message_id="msg-1",
                                    role=types.Role.ROLE_AGENT,
                                    parts=[types.Part(text="which one?")],
                                ),
                            ),
                        ),
                    ),
                ],
                [
                    types.StreamResponse(
                        status_update=types.TaskStatusUpdateEvent(
                            task_id="task-1",
                            context_id="context-1",
                            status=types.TaskStatus(
                                state=types.TaskState.TASK_STATE_COMPLETED,
                            ),
                        ),
                    ),
                ],
            ],
            get_tasks=[
                types.Task(
                    id="task-1",
                    context_id="context-1",
                    status=types.TaskStatus(
                        state=types.TaskState.TASK_STATE_INPUT_REQUIRED,
                    ),
                ),
            ],
        )
        agent = A2AAgent(self.card, client=client)

        first = await agent.reply(UserMsg(name="user", content="hi"))
        await agent.reply(UserMsg(name="user", content="the second"))

        self.assertEqual(first.get_text_content(), "which one?")
        self.assertListEqual(
            [
                (_.message.context_id, _.message.task_id)
                for _ in client.requests
            ],
            [("", ""), ("context-1", "task-1")],
        )
        self.assertIsNone(agent.state.task_id)
        await agent.aclose()

    async def test_running_task_is_rejected(self) -> None:
        """A second message would execute the running Task all over again."""
        client = _FakeClient(
            get_tasks=[
                types.Task(
                    id="task-1",
                    context_id="context-1",
                    status=types.TaskStatus(
                        state=types.TaskState.TASK_STATE_WORKING,
                    ),
                ),
            ],
        )
        agent = A2AAgent(
            self.card,
            client=client,
            state=A2AAgentState(context_id="context-1", task_id="task-1"),
        )

        with self.assertRaises(RuntimeError) as error:
            await agent.reply(UserMsg(name="user", content="hi"))

        self.assertEqual(
            str(error.exception),
            "A2A task task-1 is still running on the remote server; "
            "retry once it has finished.",
        )
        self.assertListEqual(client.requests, [])
        await agent.aclose()

    async def test_forgotten_task_starts_a_new_one(self) -> None:
        """A Task the server dropped degrades into a new one."""
        client = _FakeClient(
            [
                [
                    types.StreamResponse(
                        message=types.Message(
                            message_id="msg-1",
                            context_id="context-1",
                            role=types.Role.ROLE_AGENT,
                            parts=[types.Part(text="ok")],
                        ),
                    ),
                ],
            ],
            get_tasks=[TaskNotFoundError()],
        )
        agent = A2AAgent(
            self.card,
            client=client,
            state=A2AAgentState(context_id="context-1", task_id="stale-task"),
        )

        await agent.reply(UserMsg(name="user", content="hi"))

        self.assertEqual(client.requests[0].message.task_id, "")
        self.assertEqual(client.requests[0].message.context_id, "context-1")
        self.assertIsNone(agent.state.task_id)
        await agent.aclose()


class A2AAgentLifecycleTest(IsolatedAsyncioTestCase):
    """Test the adapter lifecycle and the interface no-ops."""

    def setUp(self) -> None:
        """Build a card shared by the lifecycle tests."""
        self.card = types.AgentCard(
            name="remote-agent",
            description="test agent",
            supported_interfaces=[
                types.AgentInterface(
                    url="http://example.test/1.0",
                    protocol_binding="JSONRPC",
                    protocol_version="1.0",
                ),
            ],
        )

    async def test_closed_agent_rejects_further_use(self) -> None:
        """The adapter owns its client, so closing it is final."""
        client = _FakeClient()
        async with A2AAgent(self.card, client=client) as agent:
            pass

        self.assertEqual(client.close_count, 1)
        with self.assertRaises(RuntimeError) as error:
            await agent.reply(UserMsg(name="user", content="hi"))
        self.assertEqual(str(error.exception), "A2AAgent is closed.")

        with self.assertRaises(RuntimeError):
            async with agent:
                pass

    async def test_compress_context_is_a_no_op(self) -> None:
        """The remote server owns its context, so there is nothing to do."""
        agent = A2AAgent(self.card, client=_FakeClient())

        with self.assertLogs("as", level="WARNING") as logs:
            await agent.compress_context("anything", keyword="ignored")

        self.assertIn("compress_context", logs.output[0])
        await agent.aclose()


if __name__ == "__main__":
    import unittest

    unittest.main()
