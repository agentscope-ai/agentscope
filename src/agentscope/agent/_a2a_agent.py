# -*- coding: utf-8 -*-
"""A stateful client-side adapter for remote A2A agents."""
from __future__ import annotations

import asyncio

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, TYPE_CHECKING

from .._logging import logger
from .._utils._common import _generate_id
from ..event import (
    AgentEvent,
    CustomEvent,
    ReplyEndEvent,
    ReplyFinishedReason,
    ReplyStartEvent,
)
from ..message import AssistantMsg, HintBlock, Msg, TextBlock
from ..types import ErrorInfo, ErrorType
from ._a2a_content import (
    _ArtifactReducer,
    _block_to_part,
    _emit_block,
    _part_to_block,
)
from ._config import ContextConfig

if TYPE_CHECKING:
    from a2a.client import Client
    from a2a.types import AgentCard, Message, StreamResponse
    from a2a.types import Task as A2ATask

    from ..message import ContentBlock


def _validate_default_transport(agent_card: AgentCard) -> None:
    """Ensure the SDK factory can select a supported transport."""
    supported_bindings = {"JSONRPC", "HTTP+JSON"}
    advertised_bindings = [
        interface.protocol_binding
        for interface in agent_card.supported_interfaces
    ]
    if not any(
        binding in supported_bindings for binding in advertised_bindings
    ):
        raise ValueError(
            "A2AAgent's default client requires a JSONRPC or HTTP+JSON "
            "interface; advertised bindings: "
            f"{advertised_bindings!r}. Inject a compatible SDK Client to "
            "use another transport.",
        )


# The reply outcome for each A2A Task state a response stream can end on.
# The interrupted states end the reply normally: the remote Task is suspended
# server-side, but nothing is suspended locally, so the status message is an
# ordinary turn that the caller answers with the next `reply()`. A stream that
# ends on any other state (`SUBMITTED`, `WORKING`) died without a resolution.
_FINISHED_REASONS = {
    "TASK_STATE_COMPLETED": ReplyFinishedReason.COMPLETED,
    "TASK_STATE_INPUT_REQUIRED": ReplyFinishedReason.COMPLETED,
    "TASK_STATE_AUTH_REQUIRED": ReplyFinishedReason.COMPLETED,
    "TASK_STATE_CANCELED": ReplyFinishedReason.INTERRUPTED,
    "TASK_STATE_FAILED": ReplyFinishedReason.ERROR,
    "TASK_STATE_REJECTED": ReplyFinishedReason.ERROR,
}


class A2AAgent:
    """A stateful client-side adapter for an A2A 1.0 agent.

    This class intentionally provides Agent-like interaction methods without
    inheriting :class:`agentscope.agent.Agent`. A local ``Agent`` owns a model,
    toolkit, state, and reasoning loop; this adapter delegates those concerns
    to the remote A2A server and owns only the client-side conversation and
    Task lifecycle.

    The adapter owns the remote context and active Task lifecycle. It supports
    text, raw bytes, and URL Parts, including streamed text/raw artifacts.
    """

    def __init__(
        self,
        agent_card: AgentCard,
        *,
        client: Client | None = None,
        context_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        """Initialize the A2A agent adapter.

        Args:
            agent_card (`a2a.types.AgentCard`):
                The remote Agent Card, used both to identify the peer (its
                ``name`` becomes this adapter's ``name``) and to select a
                transport. The SDK picks the newest protocol version the card
                advertises for the chosen binding, falling back to its A2A 0.3
                compatibility transport when that is all the peer offers.
            client (`a2a.client.Client | None`, optional):
                An official SDK client, e.g. one configured for gRPC or with
                custom auth. If omitted, a streaming client is built from
                the card, which then requires a ``JSONRPC`` or ``HTTP+JSON``
                interface. The adapter owns the client either way and closes
                it in :meth:`aclose`.
            context_id (`str | None`, optional):
                An existing remote context to continue, as returned by the
                :attr:`context_id` property of an earlier adapter. Subsequent
                messages join that conversation instead of starting a new one.
            task_id (`str | None`, optional):
                A remote Task to reattach to, for :meth:`get_task`,
                :meth:`resume` and :meth:`cancel_task`. :meth:`reply` ignores
                it until one of those learns the Task's state, so a Task the
                server has since dropped degrades into a new Task within
                ``context_id`` rather than a failure.
        """
        try:
            from a2a import types
        except ImportError as error:
            raise ImportError(
                "A2AAgent requires the A2A extra. Install it with "
                "`pip install 'agentscope[a2a]'`.",
            ) from error

        self._agent_card = agent_card
        self.name = self._agent_card.name
        if client is None:
            from a2a.client import ClientConfig, ClientFactory
            from a2a.utils.constants import TransportProtocol

            _validate_default_transport(self._agent_card)
            client = ClientFactory(
                ClientConfig(
                    streaming=True,
                    polling=False,
                    supported_protocol_bindings=[
                        TransportProtocol.JSONRPC,
                        TransportProtocol.HTTP_JSON,
                    ],
                ),
            ).create(self._agent_card)
        self._client = client
        self._types = types
        self._observed_msgs: list[Msg] = []
        self._context_id = context_id
        self._task_id = task_id
        self._task_state: int | None = None
        self._session_id = _generate_id()
        self._reply_lock = asyncio.Lock()
        self._closed = False

    @property
    def context_id(self) -> str | None:
        """The latest remote context ID."""
        return self._context_id

    @property
    def task_id(self) -> str | None:
        """The latest remote Task ID, if the interaction used a Task."""
        return self._task_id

    @property
    def task_state(self) -> str | None:
        """The latest remote Task state name."""
        return self._state_name(self._task_state)

    async def __aenter__(self) -> A2AAgent:
        """Enter the asynchronous context manager."""
        if self._closed:
            raise RuntimeError("A2AAgent is closed.")
        return self

    async def __aexit__(self, *_: Any) -> None:
        """Close the owned client when leaving the context manager."""
        await self.aclose()

    async def aclose(self) -> None:
        """Close the owned A2A client. Repeated calls are safe."""
        async with self._reply_lock:
            if not self._closed:
                await self._client.close()
                self._closed = True

    async def observe(self, msgs: Msg | list[Msg] | None = None) -> None:
        """Cache messages to include in the next request."""
        if msgs is None:
            return
        messages = [msgs] if isinstance(msgs, Msg) else msgs
        if not isinstance(messages, list) or not all(
            isinstance(msg, Msg) for msg in messages
        ):
            raise TypeError("msgs must be a Msg, a list of Msg, or None.")
        async with self._reply_lock:
            self._ensure_open()
            self._observed_msgs.extend(messages)

    async def compress_context(
        self,
        context_config: ContextConfig | None = None,
        instructions: HintBlock | None = None,
    ) -> None:
        """Do nothing because the remote A2A server owns its context.

        The arguments are accepted for interface compatibility with
        :class:`agentscope.agent.Agent`.
        """
        del context_config, instructions
        logger.warning(
            "Ignoring compress_context() on A2AAgent %s: the remote A2A "
            "server owns its own conversation context.",
            self.name,
        )

    async def reply_stream(
        self,
        inputs: Msg | list[Msg] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Send input and stream the remote reply as AgentScope events."""
        async for event_or_msg in self._reply(inputs):
            if not isinstance(event_or_msg, Msg):
                yield event_or_msg

    async def reply(self, inputs: Msg | list[Msg] | None = None) -> Msg:
        """Send input and return the canonical final assistant message."""
        return await self._consume_final(self._reply(inputs))

    async def resume_stream(self) -> AsyncGenerator[AgentEvent, None]:
        """Resume updates for the latest non-completed remote Task.

        Subscription is attempted first. If the server reports that
        subscription is unsupported, the canonical Task is fetched to close
        the subscribe-versus-completion race.
        """
        async for event_or_msg in self._resume():
            if not isinstance(event_or_msg, Msg):
                yield event_or_msg

    async def resume(self) -> Msg:
        """Resume the latest remote Task and return its completed result."""
        return await self._consume_final(self._resume())

    async def get_task(self) -> A2ATask:
        """Fetch and return the latest remote Task snapshot."""
        async with self._reply_lock:
            self._ensure_open()
            task_id = self._require_task_id()
            task = await self._client.get_task(
                self._types.GetTaskRequest(id=task_id),
            )
            self._update_task(task)
            return task

    async def list_tasks(self, state: str | None = None) -> list[A2ATask]:
        """List every remote Task in the current context.

        Args:
            state (`str | None`, optional):
                An A2A Task state name, e.g. ``"TASK_STATE_INPUT_REQUIRED"``,
                to return only the Tasks in that state.

        Returns:
            `list[a2a.types.Task]`:
                The matching Tasks, across all result pages. An empty list
                means the server no longer knows this context.
        """
        async with self._reply_lock:
            self._ensure_open()
            if self._context_id is None:
                raise RuntimeError("A2AAgent has no remote context yet.")
            tasks: list[A2ATask] = []
            page_token = ""
            while True:
                request = self._types.ListTasksRequest(
                    context_id=self._context_id,
                    page_token=page_token,
                )
                if state is not None:
                    request.status = self._state(state)
                response = await self._client.list_tasks(request)
                tasks.extend(response.tasks)
                page_token = response.next_page_token
                if not page_token:
                    return tasks

    async def cancel_task(self) -> A2ATask:
        """Request cancellation and return the server's Task snapshot."""
        async with self._reply_lock:
            self._ensure_open()
            task_id = self._require_resumable_task_id()
            task = await self._client.cancel_task(
                self._types.CancelTaskRequest(id=task_id),
            )
            self._update_task(task)
            return task

    async def _consume_final(
        self,
        stream: AsyncIterator[AgentEvent | Msg],
    ) -> Msg:
        """Consume a shared internal stream and return its final message."""
        final_msg: Msg | None = None
        async for event_or_msg in stream:
            if isinstance(event_or_msg, Msg):
                final_msg = event_or_msg
        if final_msg is None:
            raise RuntimeError("A2AAgent did not produce a final message.")
        return final_msg

    async def _reply(
        self,
        inputs: Msg | list[Msg] | None,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Build one A2A Message and reduce its response stream."""
        async with self._reply_lock:
            self._ensure_open()
            direct_inputs = self._normalize_inputs(inputs)
            messages = [*self._observed_msgs, *direct_inputs]
            if not messages:
                raise ValueError(
                    "A2AAgent reply requires at least one message.",
                )
            self._validate_send_state()
            request = self._build_request(messages)
            expected_task_id = (
                self._task_id
                if self._task_state in self._interrupted
                else None
            )
            async for item in self._reduce_stream(
                self._client.send_message(request),
                clear_observations=True,
                expected_task_id=expected_task_id,
            ):
                yield item

    async def _resume(self) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Subscribe to an active Task with a canonical GetTask fallback."""
        async with self._reply_lock:
            self._ensure_open()
            task_id = self._require_resumable_task_id()

            async def responses() -> AsyncGenerator[StreamResponse, None]:
                from a2a.utils.errors import UnsupportedOperationError

                try:
                    async for response in self._client.subscribe(
                        self._types.SubscribeToTaskRequest(id=task_id),
                    ):
                        yield response
                except UnsupportedOperationError:
                    task = await self._client.get_task(
                        self._types.GetTaskRequest(id=task_id),
                    )
                    yield self._types.StreamResponse(task=task)

            async for item in self._reduce_stream(
                responses(),
                clear_observations=False,
                expected_task_id=task_id,
            ):
                yield item

    async def _reduce_stream(
        self,
        responses: AsyncIterator[StreamResponse],
        *,
        clear_observations: bool,
        expected_task_id: str | None,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Reduce Message/Task/status/artifact responses through one path."""
        reply_id = _generate_id()
        yield ReplyStartEvent(
            session_id=self._session_id,
            reply_id=reply_id,
            name=self.name,
        )
        reducer = _ArtifactReducer()
        direct_blocks: list[ContentBlock] = []
        status_blocks: list[ContentBlock] = []
        completed_status_blocks: list[ContentBlock] = []
        completed = False
        saw_response = False
        stream_task_id = expected_task_id
        stream_context_id: str | None = None

        async for response in responses:
            saw_response = True
            payload = response.WhichOneof("payload")
            if payload == "message":
                if completed or direct_blocks or reducer.artifact_ids:
                    raise RuntimeError(
                        "A2A response contained more than one final result.",
                    )
                message = response.message
                stream_task_id = self._validate_stream_task_id(
                    stream_task_id,
                    message.task_id,
                )
                stream_context_id = self._validate_stream_context_id(
                    stream_context_id,
                    message.context_id,
                )
                self._update_message(message)
                for part in message.parts:
                    block = _part_to_block(part)
                    direct_blocks.append(block)
                    async for event in _emit_block(
                        block,
                        reply_id,
                        self._event_metadata(reply_id),
                        close=True,
                    ):
                        yield event
                completed = True
                self._task_state = None
                continue

            if payload == "artifact_update":
                update = response.artifact_update
                stream_task_id = self._validate_stream_task_id(
                    stream_task_id,
                    update.task_id,
                )
                stream_context_id = self._validate_stream_context_id(
                    stream_context_id,
                    update.context_id,
                )
                self._update_ids(update.context_id, update.task_id)
                async for event in reducer.apply(
                    update.artifact,
                    reply_id,
                    self._event_metadata(reply_id),
                    append=update.append,
                    last_chunk=update.last_chunk,
                    snapshot=False,
                ):
                    yield event
                continue

            # Both remaining payloads carry a TaskStatus; only a `task`
            # payload also carries the canonical artifact snapshot.
            if payload == "status_update":
                update = response.status_update
                context_id, task_id = update.context_id, update.task_id
                status, artifacts, snapshot = update.status, (), False
            elif payload == "task":
                task = response.task
                context_id, task_id = task.context_id, task.id
                status, artifacts, snapshot = task.status, task.artifacts, True
            else:
                raise RuntimeError("A2A response contained no payload.")

            stream_task_id = self._validate_stream_task_id(
                stream_task_id,
                task_id,
            )
            stream_context_id = self._validate_stream_context_id(
                stream_context_id,
                context_id,
            )
            self._update_ids(context_id, task_id)
            self._task_state = int(status.state)
            completed = self._task_state == self._state(
                "TASK_STATE_COMPLETED",
            )

            # A status message is content only on the state the stream ends
            # on: the remote agent's question, its authorization instructions,
            # or why it failed. A completed Task keeps the artifacts as its
            # output, and an in-flight message is progress, not the answer.
            if status.HasField("message"):
                blocks = [
                    _part_to_block(part) for part in status.message.parts
                ]
                if completed:
                    completed_status_blocks = blocks
                elif self.task_state in _FINISHED_REASONS:
                    for block in blocks:
                        async for event in _emit_block(
                            block,
                            reply_id,
                            self._event_metadata(reply_id),
                            close=True,
                        ):
                            yield event
                    status_blocks.extend(blocks)

            yield self._status_event(reply_id)

            for artifact in artifacts:
                async for event in reducer.apply(
                    artifact,
                    reply_id,
                    self._event_metadata(reply_id),
                    append=False,
                    last_chunk=completed,
                    snapshot=True,
                ):
                    yield event
            if snapshot and completed:
                async for event in reducer.reconcile_completed(
                    {artifact.artifact_id for artifact in artifacts},
                    reply_id,
                    self._event_metadata(reply_id),
                ):
                    yield event

        if not saw_response:
            raise RuntimeError("A2A response stream ended without a response.")
        if clear_observations:
            self._observed_msgs.clear()

        metadata = self._final_metadata(reducer.artifact_ids)
        async for event in reducer.close(reply_id, metadata):
            yield event
        content = [*direct_blocks, *reducer.blocks, *status_blocks]
        if not content:
            content = completed_status_blocks

        # A direct Message response never creates a Task, so it always
        # completes; otherwise the state the stream ended on decides.
        state_name = self.task_state
        finished_reason = (
            ReplyFinishedReason.COMPLETED
            if state_name is None
            else _FINISHED_REASONS.get(state_name, ReplyFinishedReason.ERROR)
        )
        error = None
        if finished_reason == ReplyFinishedReason.ERROR:
            error = ErrorInfo(
                type=ErrorType.UPSTREAM,
                message="\n".join(
                    block.text
                    for block in content
                    if isinstance(block, TextBlock)
                )
                or f"The remote A2A task ended in {self.task_state}.",
            )

        final_msg = AssistantMsg(
            id=reply_id,
            name=self.name,
            content=content,
            metadata=metadata,
            finished_reason=finished_reason,
        )
        final_msg.error = error
        yield final_msg
        yield ReplyEndEvent(
            session_id=self._session_id,
            reply_id=reply_id,
            finished_reason=finished_reason,
            error=error,
            metadata=metadata,
        )

    def _build_request(self, messages: list[Msg]) -> Any:
        """Flatten AgentScope messages into one A2A user Message."""
        parts = [
            _block_to_part(block, self._types)
            for message in messages
            for block in message.content
        ]
        message = self._types.Message(
            message_id=_generate_id(),
            role=self._types.Role.Value("ROLE_USER"),
            parts=parts,
        )
        if self._context_id:
            message.context_id = self._context_id
        if self._task_state in self._interrupted:
            assert self._task_id is not None
            message.task_id = self._task_id
        return self._types.SendMessageRequest(message=message)

    def _normalize_inputs(self, inputs: Msg | list[Msg] | None) -> list[Msg]:
        """Normalize and validate direct reply inputs."""
        if inputs is None:
            return []
        messages = [inputs] if isinstance(inputs, Msg) else inputs
        if not isinstance(messages, list) or not all(
            isinstance(msg, Msg) for msg in messages
        ):
            raise TypeError("inputs must be a Msg, a list of Msg, or None.")
        return messages

    def _validate_send_state(self) -> None:
        """Ensure a new Message is valid for the current remote Task state."""
        if self._task_state in {
            self._state("TASK_STATE_SUBMITTED"),
            self._state("TASK_STATE_WORKING"),
        }:
            raise RuntimeError(
                f"A2A task {self._task_id!r} is {self.task_state}; call "
                "resume() to follow it instead of sending a new message.",
            )

    def _status_event(self, reply_id: str) -> CustomEvent:
        """Expose the remote Task state without changing core event types.

        The status message itself is emitted as ordinary content blocks, so
        this event carries identifiers and state only.
        """
        value: dict[str, Any] = {
            "context_id": self._context_id,
            "task_id": self._task_id,
            "task_state": self.task_state,
        }
        return CustomEvent(
            name="a2a_status_update",
            value=value,
            metadata=self._event_metadata(reply_id),
        )

    def _event_metadata(self, reply_id: str) -> dict[str, Any]:
        """Build concise metadata for streamed A2A events."""
        return {
            "a2a": {
                "context_id": self._context_id,
                "task_id": self._task_id,
                "task_state": self.task_state,
            },
            "reply_id": reply_id,
        }

    def _final_metadata(self, artifact_ids: list[str]) -> dict[str, Any]:
        """Build the canonical final A2A metadata view."""
        return {
            "a2a": {
                "context_id": self._context_id,
                "task_id": self._task_id,
                "task_state": self.task_state,
                "artifact_ids": artifact_ids,
            },
        }

    def _update_message(self, message: Message) -> None:
        """Update remote identifiers from a direct Message."""
        self._update_ids(message.context_id, "")
        self._task_id = message.task_id or None

    def _update_task(self, task: A2ATask) -> None:
        """Update remote identifiers and state from a Task snapshot."""
        self._update_ids(task.context_id, task.id)
        self._task_state = int(task.status.state)

    def _update_ids(self, context_id: str, task_id: str) -> None:
        """Remember non-empty server-authoritative identifiers."""
        if context_id:
            self._context_id = context_id
        if task_id:
            self._task_id = task_id

    @property
    def _interrupted(self) -> set[int]:
        """The Task states that a new Message continues rather than starts.

        ``AUTH_REQUIRED`` is included because the A2A specification lets a
        client message negotiate, correct, or reject the request.
        """
        return {
            self._state("TASK_STATE_INPUT_REQUIRED"),
            self._state("TASK_STATE_AUTH_REQUIRED"),
        }

    def _state(self, name: str) -> int:
        """Resolve a protobuf Task state by name."""
        return int(self._types.TaskState.Value(name))

    def _state_name(self, state: int | None) -> str | None:
        """Return a protobuf Task state name."""
        return self._types.TaskState.Name(state) if state is not None else None

    def _require_task_id(self) -> str:
        """Return the current Task ID or raise an actionable error."""
        if self._task_id is None:
            raise RuntimeError("A2AAgent has no remote Task to operate on.")
        return self._task_id

    def _require_resumable_task_id(self) -> str:
        """Return a non-terminal Task ID suitable for subscription."""
        task_id = self._require_task_id()
        terminal_states = {
            self._state("TASK_STATE_COMPLETED"),
            self._state("TASK_STATE_FAILED"),
            self._state("TASK_STATE_CANCELED"),
            self._state("TASK_STATE_REJECTED"),
        }
        if self._task_state in terminal_states:
            raise RuntimeError(
                f"A2A task {task_id!r} is already terminal "
                f"({self.task_state}).",
            )
        return task_id

    @staticmethod
    def _validate_stream_task_id(
        expected: str | None,
        incoming: str,
    ) -> str | None:
        """Ensure one response stream cannot silently switch Tasks."""
        if not incoming:
            return expected
        if expected is not None and incoming != expected:
            raise RuntimeError(
                "A2A response stream changed task ID from "
                f"{expected!r} to {incoming!r}.",
            )
        return incoming

    @staticmethod
    def _validate_stream_context_id(
        expected: str | None,
        incoming: str,
    ) -> str | None:
        """Ensure one response stream cannot silently switch contexts."""
        if not incoming:
            return expected
        if expected is not None and incoming != expected:
            raise RuntimeError(
                "A2A response stream changed context ID from "
                f"{expected!r} to {incoming!r}.",
            )
        return incoming

    def _ensure_open(self) -> None:
        """Reject operations after client closure."""
        if self._closed:
            raise RuntimeError("A2AAgent is closed.")


__all__ = ["A2AAgent"]
