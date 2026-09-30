# -*- coding: utf-8 -*-
"""The channel runtime of a process that holds no connection.

Owns two things: the channel instances such a process builds, and the
reply deliveries it runs through them.

A channel has exactly one piece of connection-bound state: the inbound
long connection opened by ``start_listening``. Everything else — sending
a reply, adding a reaction, listing chats, the platform tools handed to
the agent — is plain REST against the platform, needing only the stored
credentials.

So a process that never connects can still do all of it: build the same
channel class from its record and simply never call ``start_listening``.
That is what this factory does, and it is what lets the long connection
live in one worker while runs execute on any node.

Instances are cached per channel and rebuilt when the record's
``updated_at`` moves, so a credential rotation takes effect without a
restart. A cached instance is shared by concurrent runs — channels must
therefore keep no state across calls.

A replaced instance is retired rather than closed: a run that borrowed
it still holds it — its platform tools stay callable for the whole turn
— and closing the HTTP client underneath would break them. Retired
instances are released when this shuts down, which bounds what a
rotation costs to one idle client per rotation.

Deliveries are owned here for the same reason the connections are owned
by the lifecycle dispatcher: a background task needs a component whose
lifecycle can cancel it. The node running the agent is the one that
delivers, and that node may well have no dispatcher — this is what it
has instead.
"""
import asyncio
from contextlib import aclosing
from types import TracebackType
from typing import AsyncIterator

from ..._logging import logger
from ...event import ReplyStartEvent, RequireUserConfirmEvent
from ...message import Msg
from ..message_bus import MessageBus
from ..storage import StorageBase
from ._approval import (
    ChannelApprovalRecord,
    load_reply_requester,
    new_approval_id,
    remember_reply_requester,
    store_approval,
)
from ._base import ChannelBase, ChannelEvent, _EVENT_ADAPTER
from ._registry import ChannelTypeRegistry
from ._stream import open_reply_stream


class ChannelClients:
    """Builds and caches unconnected channel instances by channel id."""

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
        type_registry: ChannelTypeRegistry,
    ) -> None:
        """Bind storage, the bus, and the registry of channel classes.

        Args:
            storage (`StorageBase`): Source of channel records.
            message_bus (`MessageBus`): Where a delivery reads the run's
                events from.
            type_registry (`ChannelTypeRegistry`): Builds instances from
                a record's type, credentials and config.
        """
        self._storage = storage
        self._bus = message_bus
        self._types = type_registry
        self._cache: dict[str, tuple[str, ChannelBase]] = {}
        self._retired: list[ChannelBase] = []
        self._deliveries: set[asyncio.Task] = set()

    async def __aenter__(self) -> "ChannelClients":
        """Enter the factory's lifecycle; nothing is built up front."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Stop delivering, then release every instance built here.

        Deliveries go first: they are what borrows the instances, so
        nothing is using one by the time it is closed.
        """
        for task in list(self._deliveries):
            task.cancel()
        await asyncio.gather(*self._deliveries, return_exceptions=True)
        for channel_id in list(self._cache):
            self._retire(channel_id)
        for channel in self._retired:
            try:
                await channel.aclose()
            except Exception:  # pylint: disable=broad-except
                logger.warning("a channel client did not close cleanly")
        self._retired.clear()

    def _retire(self, channel_id: str) -> None:
        """Drop a cached instance without closing it.

        A concurrent run may still hold this instance through the
        platform tools attached to its toolkit, so closing here would
        pull the connection out from under an in-flight call. It is
        released at shutdown instead.

        Args:
            channel_id (`str`): The channel whose instance to retire.
        """
        cached = self._cache.pop(channel_id, None)
        if cached is not None:
            self._retired.append(cached[1])

    async def get(self, channel_id: str) -> ChannelBase | None:
        """Return an instance for ``channel_id``, or ``None``.

        Never calls ``start_listening``, so this opens no connection and
        starts no background task — only the platform's REST surface is
        usable on the returned instance.

        Args:
            channel_id (`str`): The channel to build a client for.

        Returns:
            `ChannelBase | None`: The instance, or ``None`` when the
            record is gone, disabled, or its type is not registered.
        """
        record = await self._storage.get_channel(channel_id)
        if record is None or not record.enabled:
            self._retire(channel_id)
            return None

        version = str(record.updated_at)
        cached = self._cache.get(channel_id)
        if cached is not None and cached[0] == version:
            return cached[1]

        try:
            channel = self._types.create_channel(
                channel_type=record.channel_type,
                channel_id=record.id,
                credentials=record.credentials,
                config=record.platform_config,
            )
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "channel client '%s' could not be built",
                channel_id,
            )
            return None

        # Retire the rotated instance; borrowers keep working.
        self._retire(channel_id)
        self._cache[channel_id] = (version, channel)
        return channel

    async def deliver(
        self,
        *,
        session_id: str,
        channel_id: str,
        chat_id: str,
        agent_id: str,
        input_msg: object = None,
    ) -> None:
        """Start streaming a run's reply back to its platform chat.

        Returns as soon as the delivery is under way — the caller is
        mid-run and must not wait on the platform. The task is held here
        so it is neither garbage-collected in flight nor left behind at
        shutdown.

        Args:
            session_id (`str`): The run whose reply is delivered.
            channel_id (`str`): The channel the session came from.
            chat_id (`str`): The platform chat to deliver into.
            agent_id (`str`): The agent that owns the session; pinned on
                a confirmation card so a click resumes this exact run.
            input_msg (`object`): Input that triggered the run. Used to obtain
                the original platform requester for approval authorization.
        """
        channel = await self.get(channel_id)
        if channel is None:
            logger.error(
                "channel '%s' has no client; the reply for session '%s' "
                "cannot be delivered",
                channel_id,
                session_id,
            )
            return

        requester_id = await self._requester_for_input(
            session_id,
            input_msg,
        )

        # Synthetic send target — a background run has no inbound
        # message. Carries the run's identity so a confirmation card can
        # pin its exact target and skip re-resolving routing on click.
        target = ChannelEvent(
            channel_id=channel_id,
            channel_user_id="",
            chat_id=chat_id,
            metadata={"session_id": session_id, "agent_id": agent_id},
        )

        # Subscribe before returning: the caller is about to run the
        # agent, and the run drops its event log when it persists, so a
        # subscription opened any later could miss the whole reply.
        try:
            events = await open_reply_stream(self._bus, session_id)
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "channel '%s' could not read the reply for session '%s'",
                channel_id,
                session_id,
            )
            return

        async def _run() -> None:
            """Feed the run's event stream to the channel."""
            try:
                async with aclosing(events):
                    await channel.send_response(
                        target,
                        self._approval_events(
                            events,
                            requester_id=requester_id,
                            channel_id=channel_id,
                            chat_id=chat_id,
                            agent_id=agent_id,
                            session_id=session_id,
                        ),
                    )
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "channel '%s' delivery failed for session '%s'",
                    channel_id,
                    session_id,
                )

        task = asyncio.create_task(
            _run(),
            name=f"channel-deliver:{session_id}",
        )
        self._deliveries.add(task)
        task.add_done_callback(self._deliveries.discard)

    async def _requester_for_input(
        self,
        session_id: str,
        input_msg: object,
    ) -> str:
        """Resolve the original platform requester from one run input.

        A fresh channel turn carries the user id in ``Msg.name``. Resume
        events instead carry the parked reply id, so their requester is
        recovered from the mapping stored when that reply first started.

        Args:
            session_id (`str`): Session being delivered.
            input_msg (`object`): Message or event that triggered the run.

        Returns:
            `str`: Platform requester id, or an empty string when the run has
            no requester.
        """
        if isinstance(input_msg, Msg):
            return input_msg.name
        if isinstance(input_msg, list) and input_msg:
            first = input_msg[0]
            return first.name if isinstance(first, Msg) else ""
        reply_id = getattr(input_msg, "reply_id", "")
        if not isinstance(reply_id, str) or not reply_id:
            return ""
        return await load_reply_requester(
            self._bus,
            session_id=session_id,
            reply_id=reply_id,
        )

    async def _approval_events(
        self,
        events: AsyncIterator[dict],
        *,
        requester_id: str,
        channel_id: str,
        chat_id: str,
        agent_id: str,
        session_id: str,
    ) -> AsyncIterator[dict]:
        """Persist approval context and attach opaque ids to the stream.

        The requester is retained at reply start because a reply may be
        continued by a background resume that no longer carries the original
        channel event. For every confirmation request, this method stores the
        requester together with the exact agent, session, reply, and tool call
        behind a new opaque approval id. Platform adapters receive only those
        ids through event metadata; the gateway later treats the stored record
        as authoritative.

        Args:
            events (`AsyncIterator[dict]`): Raw events from the running reply.
            requester_id (`str`): Platform id of the user who started it.
            channel_id (`str`): Channel instance receiving the reply.
            chat_id (`str`): Platform chat receiving the reply.
            agent_id (`str`): Agent that owns the reply.
            session_id (`str`): Session that owns the reply.

        Yields:
            `dict`: The original event, with approval ids added to confirmation
            event metadata.
        """
        current_requester = requester_id
        async for raw in events:
            event = _EVENT_ADAPTER.validate_python(raw)
            if isinstance(event, ReplyStartEvent):
                if current_requester:
                    await remember_reply_requester(
                        self._bus,
                        session_id=session_id,
                        reply_id=event.reply_id,
                        requester_id=current_requester,
                    )
                else:
                    current_requester = await load_reply_requester(
                        self._bus,
                        session_id=session_id,
                        reply_id=event.reply_id,
                    )
            if isinstance(event, RequireUserConfirmEvent):
                if not current_requester:
                    current_requester = await load_reply_requester(
                        self._bus,
                        session_id=session_id,
                        reply_id=event.reply_id,
                    )
                approval_ids: dict[str, str] = {}
                for tool_call in event.tool_calls:
                    approval_id = new_approval_id()
                    await store_approval(
                        self._bus,
                        approval_id,
                        ChannelApprovalRecord(
                            channel_id=channel_id,
                            chat_id=chat_id,
                            agent_id=agent_id,
                            session_id=session_id,
                            reply_id=event.reply_id,
                            tool_call_id=tool_call.id,
                            requester_id=current_requester,
                        ),
                    )
                    approval_ids[tool_call.id] = approval_id
                raw = dict(raw)
                raw["metadata"] = {
                    **dict(raw.get("metadata") or {}),
                    "channel_approval_ids": approval_ids,
                }
            yield raw
