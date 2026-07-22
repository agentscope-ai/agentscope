# -*- coding: utf-8 -*-
"""A stateful client-side adapter for remote A2A agents."""
from __future__ import annotations

import asyncio

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, TYPE_CHECKING

from .._utils._common import _generate_id
from ..event import (
    AgentEvent,
    CustomEvent,
    ReplyEndEvent,
    ReplyFinishedReason,
    ReplyStartEvent,
)
from ..message import AssistantMsg, HintBlock, Msg
from ._a2a_content import (
    _ArtifactReducer,
    _block_to_part,
    _emit_block,
    _part_to_block,
)
from ._config import ContextConfig

if TYPE_CHECKING:
    from a2a.client import Client
    from a2a.types import AgentCard, Message, StreamResponse, Task


def _import_a2a() -> tuple[Any, Any, Any]:
    """Import the optional A2A SDK with an actionable error message."""
    try:
        from a2a import types
        from a2a.client import ClientConfig, ClientFactory
    except ImportError as error:
        raise ImportError(
            "A2AAgent requires the A2A extra. Install it with "
            "`pip install 'agentscope[a2a]'`.",
        ) from error
    return types, ClientConfig, ClientFactory


def _protocol_1_0_card(agent_card: AgentCard) -> AgentCard:
    """Copy an Agent Card while retaining only A2A 1.0 interfaces."""
    types, _, _ = _import_a2a()
    if not isinstance(agent_card, types.AgentCard):
        raise TypeError("agent_card must be an a2a.types.AgentCard instance.")
    interfaces = [
        interface
        for interface in agent_card.supported_interfaces
        if interface.protocol_version == "1.0"
    ]
    if not interfaces:
        raise ValueError(
            "A2AAgent requires an Agent Card with at least one A2A 1.0 "
            "interface.",
        )
    filtered_card = types.AgentCard()
    filtered_card.CopyFrom(agent_card)
    del filtered_card.supported_interfaces[:]
    filtered_card.supported_interfaces.extend(interfaces)
    return filtered_card


def _validate_default_transport(agent_card: AgentCard) -> None:
    """Ensure the SDK factory can select a supported A2A 1.0 transport."""
    supported_bindings = {"JSONRPC", "HTTP+JSON"}
    advertised_bindings = [
        interface.protocol_binding
        for interface in agent_card.supported_interfaces
    ]
    if not any(
        binding in supported_bindings for binding in advertised_bindings
    ):
        raise ValueError(
            "A2AAgent's default client requires an A2A 1.0 JSONRPC or "
            "HTTP+JSON interface; advertised bindings: "
            f"{advertised_bindings!r}. Inject a compatible SDK Client to "
            "use another transport.",
        )


class A2ATaskStateError(RuntimeError):
    """A valid A2A Task ended without producing a completed result.

    The error retains the remote identifiers and state so callers can handle
    `INPUT_REQUIRED`, update authorization out of band for `AUTH_REQUIRED`, or
    inspect terminal failures without parsing an error string.
    """

    def __init__(
        self,
        *,
        context_id: str | None,
        task_id: str | None,
        task_state: str | None,
        status_message: Msg | None = None,
    ) -> None:
        """Initialize the typed remote Task state error."""
        self.context_id = context_id
        self.task_id = task_id
        self.task_state = task_state
        self.status_message = status_message
        super().__init__(
            "A2A task did not complete "
            f"(context_id={context_id!r}, task_id={task_id!r}, "
            f"state={task_state!r}).",
        )


class A2AAgent:
    """A stateful client-side adapter for an A2A 1.0 agent.

    This class intentionally provides Agent-like interaction methods without
    inheriting :class:`agentscope.agent.Agent`. A local ``Agent`` owns a model,
    toolkit, state, and reasoning loop; this adapter delegates those concerns
    to the remote A2A server and owns only the client-side conversation and
    Task lifecycle.

    The adapter owns the remote context and active Task lifecycle. It supports
    text, raw bytes, and URL Parts, including streamed text/raw artifacts.
    Injected clients are owned by the adapter and closed by :meth:`aclose`.

    Args:
        agent_card (`a2a.types.AgentCard`):
            The remote Agent Card. At least one A2A 1.0 interface is required.
        client (`a2a.client.Client | None`, optional):
            An official SDK client. If omitted, a streaming JSON-RPC and
            HTTP+JSON client is created from the filtered Agent Card.
    """

    def __init__(
        self,
        agent_card: AgentCard,
        *,
        client: Client | None = None,
    ) -> None:
        """Initialize the A2A agent adapter."""
        types, ClientConfig, ClientFactory = _import_a2a()
        self._agent_card = _protocol_1_0_card(agent_card)
        self.name = self._agent_card.name
        if client is None:
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
        self._context_id: str | None = None
        self._task_id: str | None = None
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

    async def get_task(self) -> Task:
        """Fetch and return the latest remote Task snapshot."""
        async with self._reply_lock:
            self._ensure_open()
            task_id = self._require_task_id()
            task = await self._client.get_task(
                self._types.GetTaskRequest(id=task_id),
            )
            self._update_task(task)
            return task

    async def cancel_task(self) -> Task:
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
                if self._task_state == self._state("TASK_STATE_INPUT_REQUIRED")
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
        direct_blocks = []
        completed = False
        saw_response = False
        status_message: Msg | None = None
        final_status_message: Msg | None = None
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

            if payload == "status_update":
                update = response.status_update
                stream_task_id = self._validate_stream_task_id(
                    stream_task_id,
                    update.task_id,
                )
                stream_context_id = self._validate_stream_context_id(
                    stream_context_id,
                    update.context_id,
                )
                self._update_ids(update.context_id, update.task_id)
                self._task_state = int(update.status.state)
                current_status_message = None
                if update.status.HasField("message"):
                    current_status_message = self._message_to_msg(
                        update.status.message,
                        reply_id,
                    )
                    status_message = current_status_message
                yield self._status_event(reply_id, current_status_message)
                if self._task_state == self._state(
                    "TASK_STATE_COMPLETED",
                ):
                    completed = True
                    final_status_message = current_status_message
                continue

            if payload != "task":
                raise RuntimeError("A2A response contained no payload.")

            task = response.task
            stream_task_id = self._validate_stream_task_id(
                stream_task_id,
                task.id,
            )
            stream_context_id = self._validate_stream_context_id(
                stream_context_id,
                task.context_id,
            )
            self._update_task(task)
            current_status_message = None
            if task.status.HasField("message"):
                current_status_message = self._message_to_msg(
                    task.status.message,
                    reply_id,
                )
                status_message = current_status_message
            yield self._status_event(reply_id, current_status_message)
            task_completed = self._task_state == self._state(
                "TASK_STATE_COMPLETED",
            )
            if task_completed:
                final_status_message = current_status_message
            for artifact in task.artifacts:
                async for event in reducer.apply(
                    artifact,
                    reply_id,
                    self._event_metadata(reply_id),
                    append=False,
                    last_chunk=task_completed,
                    snapshot=True,
                ):
                    yield event
            if task_completed:
                async for event in reducer.reconcile_completed(
                    {artifact.artifact_id for artifact in task.artifacts},
                    reply_id,
                    self._event_metadata(reply_id),
                ):
                    yield event
            completed = task_completed

        if not saw_response:
            raise RuntimeError("A2A response stream ended without a response.")
        if clear_observations:
            self._observed_msgs.clear()

        if not completed:
            raise A2ATaskStateError(
                context_id=self._context_id,
                task_id=self._task_id,
                task_state=self.task_state,
                status_message=status_message,
            )

        metadata = self._final_metadata(reducer.artifact_ids)
        async for event in reducer.close(reply_id, metadata):
            yield event
        content = [*direct_blocks, *reducer.blocks]
        if not content and final_status_message is not None:
            content = final_status_message.content
        final_msg = AssistantMsg(
            id=reply_id,
            name=self.name,
            content=content,
            metadata=metadata,
        )
        yield final_msg
        yield ReplyEndEvent(
            session_id=self._session_id,
            reply_id=reply_id,
            finished_reason=ReplyFinishedReason.COMPLETED,
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
        if self._task_state == self._state("TASK_STATE_INPUT_REQUIRED"):
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
            self._state("TASK_STATE_AUTH_REQUIRED"),
        }:
            raise RuntimeError(
                f"A2A task {self._task_id!r} is {self.task_state}; call "
                "resume() after any required out-of-band action.",
            )

    def _message_to_msg(self, message: Message, reply_id: str) -> Msg:
        """Map an A2A status Message without emitting it as final content."""
        return AssistantMsg(
            name=self.name,
            content=[_part_to_block(part) for part in message.parts],
            id=reply_id,
            metadata=self._event_metadata(reply_id),
        )

    def _status_event(
        self,
        reply_id: str,
        status_message: Msg | None,
    ) -> CustomEvent:
        """Expose generic A2A progress without changing core event types."""
        value: dict[str, Any] = {
            "context_id": self._context_id,
            "task_id": self._task_id,
            "task_state": self.task_state,
        }
        if status_message is not None:
            value["message"] = {
                "text": status_message.get_text_content(),
                "content_types": [
                    block.type for block in status_message.content
                ],
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

    def _update_task(self, task: Task) -> None:
        """Update remote identifiers and state from a Task snapshot."""
        self._update_ids(task.context_id, task.id)
        self._task_state = int(task.status.state)

    def _update_ids(self, context_id: str, task_id: str) -> None:
        """Remember non-empty server-authoritative identifiers."""
        if context_id:
            self._context_id = context_id
        if task_id:
            self._task_id = task_id

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


__all__ = ["A2AAgent", "A2ATaskStateError"]
