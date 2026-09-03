# -*- coding: utf-8 -*-
"""A stateful client-side adapter for remote A2A agents."""
from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from typing import Any, TYPE_CHECKING

from ..state import A2AAgentState
from .._logging import logger
from .._utils._common import _generate_id
from ..event import (
    ReplyEndEvent,
    ReplyFinishedReason,
    ReplyStartEvent,
    AgentEvent,
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
)
from ..message import (
    AssistantMsg,
    Msg,
    TextBlock,
    Base64Source,
    DataBlock,
    URLSource,
)

if TYPE_CHECKING:
    from a2a.client import Client
    from a2a.types import AgentCard
    from a2a.types import Part


def _part_to_events(
    part: Part,
    reply_id: str,
    metadata: dict[str, Any],
) -> list[AgentEvent]:
    """Build the events for one A2A Part. Each Part is its own block.

    A text Part becomes a text block, and a raw or URL Part becomes a data
    block. The A2A ids the Part came from are carried on the block end event,
    so a consumer can tell which Task and Artifact each block belongs to.
    """
    block_id = _generate_id()
    kind = part.WhichOneof("content")
    if kind == "text":
        events: list[AgentEvent] = [
            TextBlockStartEvent(reply_id=reply_id, block_id=block_id),
        ]
        if part.text:
            events.append(
                TextBlockDeltaEvent(
                    reply_id=reply_id,
                    block_id=block_id,
                    delta=part.text,
                ),
            )
        events.append(
            TextBlockEndEvent(
                reply_id=reply_id,
                block_id=block_id,
                metadata=metadata,
            ),
        )
        return events

    if kind in ("raw", "url"):
        media_type = part.media_type or "application/octet-stream"
        events = [
            DataBlockStartEvent(
                reply_id=reply_id,
                block_id=block_id,
                media_type=media_type,
                name=part.filename or None,
            ),
        ]
        if kind == "url":
            events.append(
                DataBlockDeltaEvent(
                    reply_id=reply_id,
                    block_id=block_id,
                    url=part.url,
                    media_type=media_type,
                ),
            )
        elif part.raw:
            events.append(
                DataBlockDeltaEvent(
                    reply_id=reply_id,
                    block_id=block_id,
                    data=base64.b64encode(part.raw).decode("ascii"),
                    media_type=media_type,
                ),
            )
        events.append(
            DataBlockEndEvent(
                reply_id=reply_id,
                block_id=block_id,
                metadata=metadata,
            ),
        )
        return events

    raise ValueError(
        "A2AAgent supports text, raw, and URL parts; got unsupported "
        f"{kind or 'empty'} content.",
    )


def _get_finished_reason(state: int) -> ReplyFinishedReason:
    """Map the A2A Task state a response stream ended on to a reply outcome.

    A Task waiting for input ends the reply normally: it is suspended
    server-side, but nothing is suspended locally, so its status message is
    an ordinary turn that the caller answers with the next reply.
    """
    from a2a.types import TaskState

    if state in (
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
    ):
        return ReplyFinishedReason.COMPLETED
    if state == TaskState.TASK_STATE_CANCELED:
        return ReplyFinishedReason.INTERRUPTED
    # FAILED and REJECTED, plus a stream that died on SUBMITTED or WORKING
    # without ever resolving.
    return ReplyFinishedReason.ERROR


def _awaiting_input(state: int) -> bool:
    """Whether a Task in this state is suspended waiting for the caller, so
    the next message continues it instead of starting a new Task."""
    from a2a.types import TaskState

    return state in (
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
    )


class A2AAgent:
    """A stateful client-side adapter for an A2A 1.0 agent.

    This class intentionally provides Agent-like interaction methods without
    inheriting :class:`agentscope.agent.Agent`. A local ``Agent`` owns a model,
    toolkit, state, and reasoning loop; this adapter delegates those concerns
    to the remote A2A server and owns only the remote conversation
    (``context_id``) and the Task the next message continues (``task_id``),
    both held in :class:`agentscope.state.A2AAgentState`.

    Each A2A Part of a response becomes one content block: text Parts become
    :class:`~agentscope.message.TextBlock`, raw byte and URL Parts become
    :class:`~agentscope.message.DataBlock`.

    The adapter owns its A2A client and closes it in :meth:`aclose`, so it is
    single-use: once closed it cannot be reopened.
    """

    def __init__(
        self,
        agent_card: AgentCard,
        *,
        client: Client | None = None,
        state: A2AAgentState | None = None,
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
            state (`agentscope.state.A2AAgentState | None`, optional):
                An existing state to resume, e.g. one saved from an earlier
                adapter, so its ``context_id`` continues the same remote
                conversation. A fresh state is created when omitted.
        """
        try:
            import a2a  # noqa: F401  pylint: disable=unused-import
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
        self.state = state or A2AAgentState()
        self._closed = False

    async def __aenter__(self) -> A2AAgent:
        """Enter the asynchronous context manager.

        Leaving the block closes the owned client, and nothing reopens it,
        so an adapter can only be entered before it is closed.
        """
        if self._closed:
            raise RuntimeError("A2AAgent is closed.")
        return self

    async def __aexit__(self, *_: Any) -> None:
        """Close the owned client when leaving the context manager."""
        await self.aclose()

    async def aclose(self) -> None:
        """Close the owned A2A client. Repeated calls are safe."""
        if not self._closed:
            await self._client.close()
            self._closed = True

    async def reply_stream(
        self,
        inputs: Msg | list[Msg] | None = None,
        yield_final_msg: bool = False,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Send input and stream the remote reply as AgentScope events.

        Args:
            inputs (`Msg | list[Msg] | None`, optional):
                The messages to send, preceded by the observed ones.
            yield_final_msg (`bool`, defaults to `False`):
                Whether to yield the final reply message after the events.

        Yields:
            `AgentEvent | Msg`:
                Streamed events produced during the reply.
        """
        async for event_or_msg in self._reply(inputs):
            if isinstance(event_or_msg, Msg) and not yield_final_msg:
                continue
            yield event_or_msg

    async def reply(self, inputs: Msg | list[Msg] | None = None) -> Msg:
        """Send input and return the final assistant message.

        Args:
            inputs (`Msg | list[Msg] | None`, optional):
                The messages to send, preceded by the observed ones.

        Returns:
            `Msg`:
                The reply message, assembled from the streamed events.
        """
        final_msg: Msg | None = None
        async for event_or_msg in self._reply(inputs):
            if isinstance(event_or_msg, Msg):
                final_msg = event_or_msg
        if final_msg is None:
            raise RuntimeError("A2AAgent did not produce a final message.")
        return final_msg

    async def _reply(
        self,
        inputs: Msg | list[Msg] | None = None,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Send one A2A Message and stream its response back.

        The observed messages lead the input, and the whole turn is flattened
        into a single A2A Message. It continues the remote Task the previous
        reply left waiting for input, if any, and otherwise starts a new one
        within the same ``context_id``.

        Args:
            inputs (`Msg | list[Msg] | None`):
                The input message(s) to send, preceded by the observed ones.

        Yields:
            `AgentEvent | Msg`:
                The streamed events, then the final reply message.

        Raises:
            `RuntimeError`:
                If the remote Task from an earlier reply is still running,
                since a second message would execute it again.
        """
        from a2a.types import GetTaskRequest, TaskState
        from a2a.utils.errors import TaskNotFoundError

        # Ensure the client is open
        self._ensure_open()

        # Normalize the inputs into a2a-acceptable objects
        if isinstance(inputs, Msg):
            input_msgs = [inputs]
        elif isinstance(inputs, list) and all(
            isinstance(_, Msg) for _ in inputs
        ):
            input_msgs = inputs
        else:
            input_msgs = []

        # Observed messages are earlier context, so they lead this input.
        input_msgs = [*self.state.observed_context, *input_msgs]
        self.state.observed_context.clear()

        if not input_msgs:
            raise ValueError(
                "A2AAgent reply requires at least one message.",
            )

        # A Task left over from an earlier reply decides what this input
        # does, and only the server knows how it ended.
        if self.state.task_id:
            try:
                task = await self._client.get_task(
                    GetTaskRequest(id=self.state.task_id),
                )
            except TaskNotFoundError:
                # The server has forgotten the Task; start a new one.
                self.state.task_id = None
            else:
                if task.status.state in (
                    TaskState.TASK_STATE_SUBMITTED,
                    TaskState.TASK_STATE_WORKING,
                ):
                    # The server taps the running Task's own queue, so
                    # sending now would execute it a second time.
                    raise RuntimeError(
                        f"A2A task {self.state.task_id} is still running "
                        "on the remote server; retry once it has finished.",
                    )
                if not _awaiting_input(task.status.state):
                    self.state.task_id = None

        reply_id = _generate_id()
        # The reply message is assembled from the very events streamed to
        # the caller, so the two can never disagree.
        msg = AssistantMsg(
            id=reply_id,
            name=self._agent_card.name,
            content=[],
        )

        yield ReplyStartEvent(
            session_id=self.state.session_id,
            reply_id=reply_id,
            name=self._agent_card.name,
        )

        finished_reason = ReplyFinishedReason.COMPLETED

        async for response in self._client.send_message(
            self._build_request(input_msgs),
        ):
            events: list[AgentEvent] = []
            match response.WhichOneof("payload"):
                case "message":
                    # A direct Message response carries no Task status, and
                    # answering it never continues a Task.
                    message = response.message
                    if message.context_id:
                        self.state.context_id = message.context_id
                    self.state.task_id = None

                    metadata = {
                        "a2a": {"message_id": message.message_id},
                    }
                    for part in message.parts:
                        events += _part_to_events(
                            part,
                            reply_id,
                            metadata,
                        )

                case "artifact_update":
                    # An artifact carries no Task status, so it never
                    # decides whether the Task is still to be continued.
                    update = response.artifact_update
                    if update.context_id:
                        self.state.context_id = update.context_id

                    metadata = {
                        "a2a": {
                            "task_id": update.task_id,
                            "artifact_id": update.artifact.artifact_id,
                        },
                    }
                    for part in update.artifact.parts:
                        events += _part_to_events(
                            part,
                            reply_id,
                            metadata,
                        )

                case "status_update":
                    update = response.status_update
                    if update.context_id:
                        self.state.context_id = update.context_id
                    self.state.task_id = (
                        update.task_id
                        if _awaiting_input(update.status.state)
                        else None
                    )

                    # Last write wins: the outcome is decided by the
                    # state the stream ends on.
                    finished_reason = _get_finished_reason(
                        update.status.state,
                    )

                    metadata = {"a2a": {"task_id": update.task_id}}
                    for part in update.status.message.parts:
                        events += _part_to_events(
                            part,
                            reply_id,
                            metadata,
                        )

                case "task":
                    # A full Task snapshot, the only payload the
                    # non-streaming path and GetTask return.
                    task = response.task
                    if task.context_id:
                        self.state.context_id = task.context_id
                    self.state.task_id = (
                        task.id if _awaiting_input(task.status.state) else None
                    )

                    finished_reason = _get_finished_reason(
                        task.status.state,
                    )

                    for artifact in task.artifacts:
                        metadata = {
                            "a2a": {
                                "task_id": task.id,
                                "artifact_id": artifact.artifact_id,
                            },
                        }
                        for part in artifact.parts:
                            events += _part_to_events(
                                part,
                                reply_id,
                                metadata,
                            )

                    metadata = {"a2a": {"task_id": task.id}}
                    for part in task.status.message.parts:
                        events += _part_to_events(
                            part,
                            reply_id,
                            metadata,
                        )

                case _:
                    raise RuntimeError(
                        "A2A response contained no payload.",
                    )

            for event in events:
                msg.append_event(event)
                yield event

        metadata = {"a2a": {"context_id": self.state.context_id}}

        # `append_event` stamps finished_reason and finished_at from the
        # end event, so the message must see it before it is yielded.
        end_event = ReplyEndEvent(
            session_id=self.state.session_id,
            reply_id=reply_id,
            finished_reason=finished_reason,
            metadata=metadata,
        )
        msg.append_event(end_event)
        yield end_event

        msg.metadata = metadata
        yield msg

    def _build_request(self, messages: list[Msg]) -> Any:
        """Flatten AgentScope messages into one A2A user Message.

        Args:
            messages (`list[Msg]`):
                The messages of this turn, whose blocks become the Parts of
                a single user Message.

        Returns:
            `a2a.types.SendMessageRequest`:
                The request, carrying the remote ``context_id`` and, when the
                Task is waiting for input, its ``task_id``.
        """
        from a2a.types import Part, Message, Role, SendMessageRequest

        # Convert Msg objects into A2A message parts.
        parts = []
        for msg in messages:
            for block in msg.content:
                if isinstance(block, TextBlock):
                    parts.append(Part(text=block.text))

                elif isinstance(block, DataBlock):
                    if isinstance(block.source, Base64Source):
                        try:
                            raw = base64.b64decode(
                                block.source.data,
                                validate=True,
                            )
                        except ValueError as error:
                            raise ValueError(
                                "A2AAgent received invalid base64 input data.",
                            ) from error
                        parts.append(
                            Part(
                                raw=raw,
                                media_type=block.source.media_type,
                                filename=block.name or "",
                            ),
                        )

                    elif isinstance(block.source, URLSource):
                        parts.append(
                            Part(
                                url=str(block.source.url),
                                media_type=block.source.media_type,
                                filename=block.name or "",
                            ),
                        )

                else:
                    raise TypeError(
                        f"A2AAgent cannot send block of type {type(block)}.",
                    )

        message = Message(
            message_id=_generate_id(),
            role=Role.ROLE_USER,
            parts=parts,
        )

        # Use the current session id as the a2a context id if already set
        if self.state.context_id:
            message.context_id = self.state.context_id

        if self.state.task_id:
            message.task_id = self.state.task_id

        return SendMessageRequest(message=message)

    def _ensure_open(self) -> None:
        """Reject operations after client closure."""
        if self._closed:
            raise RuntimeError("A2AAgent is closed.")

    # =================================================================
    # The functions that not implemented by the A2A protocol, leaving for
    # alignment with the Agent interface.
    # =================================================================

    async def compress_context(  # pylint: disable=unused-argument
        self,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Do nothing because the remote A2A server owns its context.

        The arguments are accepted for interface compatibility with
        :class:`agentscope.agent.Agent`.
        """
        logger.warning(
            "Ignoring compress_context() on A2AAgent %s: the remote A2A "
            "server owns its own conversation context.",
            self.name,
        )

    async def observe(self, msgs: Msg | list[Msg] | None = None) -> None:
        """Cache messages to send ahead of the next reply's own input.

        Args:
            msgs (`Msg | list[Msg] | None`, optional):
                The messages to remember. ``None`` is ignored.
        """
        if msgs is None:
            return
        messages = [msgs] if isinstance(msgs, Msg) else msgs
        if not isinstance(messages, list) or not all(
            isinstance(msg, Msg) for msg in messages
        ):
            raise TypeError("msgs must be a Msg, a list of Msg, or None.")
        self._ensure_open()
        self.state.observed_context.extend(messages)
