# -*- coding: utf-8 -*-
"""ChannelGateway — inbound-only orchestration (data plane).

``process(event, channel)`` is the single entry point for both inbound
messages and confirmation-card clicks. It is deliberately thin:

- a **message** is routed to an ``(agent_id, session_id)`` and delivered
  as run input (a user turn when the session is idle, or an inbox hint
  when a reply is already in flight) — then the gateway returns;
- a **card click** takes the parked request and resumes the run.

The gateway does **not** collect or send the reply. Output flows the
other way: a channel-bound run emits an outbound signal, and the
:class:`~agentscope.app.channel.ChannelLifecycleDispatcher` (on the node
hosting the channel) subscribes to the run's event stream and streams
the reply back — so scheduled / background runs reach the channel too,
not just inbound messages.
"""
import json

from ..._logging import logger
from ...message import DataBlock, HintBlock, TextBlock, UserMsg
from ...permission import PermissionContext, PermissionMode
from ...state import AgentState
from .._bus_ops import enqueue_run_trigger
from ..message_bus import MessageBus, MessageBusKeys
from ..storage import (
    ChannelRecord,
    ChatModelConfig,
    SessionConfig,
    SessionScope,
    SessionSource,
    StorageBase,
)
from ..workspace_manager import WorkspaceManagerBase
from ._base import ChannelEvent, ChannelConfirmationResultEvent
from ._decision import resume_after_decision
from ._routing import resolve

# How long a media-only message waits for its accompanying text message.
_MEDIA_BUFFER_TTL_SECS = 300
# Max buffered attachments carried into one text message.
_MEDIA_BUFFER_MAX = 9


class ChannelGateway:
    """Route inbound channel events into runs; resume on card clicks."""

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
        workspace_manager: WorkspaceManagerBase,
    ) -> None:
        """Bind storage, the message bus, and the workspace manager.

        Args:
            storage (`StorageBase`): Application storage.
            message_bus (`MessageBus`): Application message bus.
            workspace_manager (`WorkspaceManagerBase`): Assigns each
                derived session its workspace under the isolation policy.
        """
        self._storage = storage
        self._bus = message_bus
        self._workspace_manager = workspace_manager

    async def process(
        self,
        event: ChannelEvent | ChannelConfirmationResultEvent,
    ) -> None:
        """Handle one inbound event (message or confirmation decision).

        Args:
            event (`ChannelEvent | ChannelConfirmationResultEvent`): The
                inbound message or card-click decision.
        """
        try:
            if isinstance(event, ChannelConfirmationResultEvent):
                await self._handle_decision(event)
            else:
                await self._handle_message(event)
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "ChannelGateway.process failed for channel %s",
                event.channel_id,
            )

    async def _handle_decision(
        self,
        event: ChannelConfirmationResultEvent,
    ) -> None:
        """Resume the run for a card-click decision.

        Routes the click to its session and resumes; the authoritative
        tool call is read from session state, so a stale/forged click
        simply finds nothing to answer.

        Args:
            event (`ChannelConfirmationResultEvent`): The click decision.
        """
        record = await self._storage.get_channel(event.channel_id)
        if record is None or not record.enabled:
            return
        # Prefer the target pinned on the card at send time; re-resolving
        # via routing here would misroute clicks whose original message
        # matched on metadata, or in per-chat-user scope when a different
        # member clicks.
        if event.agent_id and event.session_id:
            guess = (event.agent_id, event.session_id)
        else:
            agent_id, session_id, _ = resolve(
                ChannelEvent(
                    channel_id=event.channel_id,
                    channel_user_id=event.channel_user_id,
                    chat_id=event.chat_id,
                ),
                record,
            )
            guess = (agent_id, session_id)

        if await self._resume(record.user_id, guess, event):
            return

        # A card that reports nothing but the click cannot name its run,
        # and routing only guesses at one: a platform that identifies the
        # clicker differently than the sender lands on another session
        # entirely. Ask the sessions serving the chat the card was
        # delivered into which of them is waiting; a click cannot answer
        # for a chat it did not come from.
        for session in await self._storage.list_sessions_by_channel(
            record.user_id,
            event.channel_id,
        ):
            target = (session.agent_id, session.id)
            if target == guess or session.source_chat_id != event.chat_id:
                continue
            if await self._resume(record.user_id, target, event):
                return

        logger.warning(
            "channel '%s': no session is waiting on tool call '%s' "
            "(clicked in chat '%s' by '%s')",
            event.channel_id,
            event.tool_call_id,
            event.chat_id,
            event.channel_user_id,
        )

    async def _resume(
        self,
        user_id: str,
        target: tuple[str, str],
        event: ChannelConfirmationResultEvent,
    ) -> bool:
        """Answer the decision in one session, if it is waiting for it.

        Args:
            user_id (`str`): Owner of the session.
            target (`tuple[str, str]`): The ``(agent_id, session_id)`` to
                try.
            event (`ChannelConfirmationResultEvent`): The click decision.

        Returns:
            `bool`: Whether the run was resumed.
        """
        agent_id, session_id = target
        return await resume_after_decision(
            self._bus,
            self._storage,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            tool_call_id=event.tool_call_id,
            approved=event.approved,
        )

    # -- Message path --

    async def _handle_message(self, event: ChannelEvent) -> None:
        """Aggregate buffered media, then inject a hint into a live run
        or start a fresh user turn on an idle session.

        Args:
            event (`ChannelEvent`): The normalised inbound message.
        """
        record = await self._storage.get_channel(event.channel_id)
        if record is None:
            logger.error("No channel record for %s", event.channel_id)
            return
        if not record.enabled:
            return  # stale event from a since-disabled channel

        agent_id, session_id, scope = resolve(event, record)
        if event.chat_id:
            await self._bus.registry_set(
                MessageBusKeys.channel_seen_chats(event.channel_id),
                event.chat_id,
                "1",
            )

        content = await self._aggregate_media(event)
        if content is None:
            return  # media buffered; nothing to run until a text message

        # A reply already in flight → inject the input as a hint so the
        # live run folds it in. Otherwise start a fresh user turn.
        if await self._bus.is_locked(MessageBusKeys.session_lock(session_id)):
            await self._bus.queue_push(
                MessageBusKeys.inbox(session_id),
                HintBlock(
                    hint=content,
                    source=json.dumps(
                        {
                            "label": "channel",
                            "sublabel": event.channel_user_name
                            or event.channel_user_id,
                        },
                        ensure_ascii=False,
                    ),
                ).model_dump(mode="json"),
            )
            return

        await self._ensure_session(record, agent_id, session_id, event, scope)
        # Deliver as a genuine user turn; the run's output is streamed
        # back by the dispatcher's forward loop, not collected here.
        await enqueue_run_trigger(
            self._bus,
            user_id=record.user_id,
            session_id=session_id,
            agent_id=agent_id,
            kind=MessageBusKeys.WAKEUP_KIND_MESSAGE,
            inputs=UserMsg(name=event.channel_user_id, content=content),
        )

    async def _aggregate_media(
        self,
        event: ChannelEvent,
    ) -> list[TextBlock | DataBlock] | None:
        """Merge buffered attachments with this message: media-only
        buffers and returns ``None``; the next text drains and combines.

        Args:
            event (`ChannelEvent`): The inbound message.
        """
        key = MessageBusKeys.channel_media_buffer(
            event.channel_id,
            event.chat_id,
            event.channel_user_id,
        )
        has_text = any(isinstance(b, TextBlock) for b in event.content)
        if not has_text:
            for block in event.content:
                if isinstance(block, DataBlock):
                    await self._bus.queue_push(
                        key,
                        block.model_dump(mode="json"),
                        ttl_secs=_MEDIA_BUFFER_TTL_SECS,
                    )
            return None
        entries = await self._bus.queue_drain(key, max_count=_MEDIA_BUFFER_MAX)
        buffered = [DataBlock.model_validate(p) for _id, p in entries]
        return [*buffered, *event.content]

    # -- Session creation (deterministic id, idempotent) --

    async def _ensure_session(
        self,
        record: ChannelRecord,
        agent_id: str,
        session_id: str,
        event: ChannelEvent,
        scope: SessionScope,
    ) -> None:
        """Create the derived session if absent (idempotent across nodes).

        Args:
            record (`ChannelRecord`): The owning channel record.
            agent_id (`str`): The resolved target agent.
            session_id (`str`): The derived session id.
            event (`ChannelEvent`): The originating message.
            scope (`SessionScope`): How the session is grouped.
        """
        existing = await self._storage.get_session(
            user_id=record.user_id,
            agent_id=agent_id,
            session_id=session_id,
        )
        if existing is not None:
            return

        fallback = record.session.fallback_chat_model_config
        session_config = SessionConfig(
            workspace_id=await self._workspace_manager.assign_workspace_id(
                user_id=record.user_id,
                agent_id=agent_id,
                session_id=session_id,
            ),
            chat_model_config=ChatModelConfig(
                **record.session.chat_model_config,
            ),
            fallback_chat_model_config=(
                ChatModelConfig(**fallback) if fallback else None
            ),
            name=self._session_name(record, event, scope),
        )
        initial_state = AgentState(
            permission_context=PermissionContext(
                mode=PermissionMode(record.session.permission_mode),
            ),
        )
        await self._storage.upsert_session(
            user_id=record.user_id,
            agent_id=agent_id,
            config=session_config,
            state=initial_state,
            session_id=session_id,
            source=SessionSource.CHANNEL,
            source_chat_id=event.chat_id,
            source_chat_name=event.chat_name or None,
            source_channel_id=record.id,
        )

    @staticmethod
    def _session_name(
        record: ChannelRecord,
        event: ChannelEvent,
        scope: SessionScope,
    ) -> str:
        """Compact, human-readable session name, e.g. ``Feishu/产品群/张三``.

        Args:
            record (`ChannelRecord`): The owning channel record.
            event (`ChannelEvent`): The originating message.
            scope (`SessionScope`): How the session is grouped.
        """
        platform = record.channel_type.capitalize()
        where = event.chat_name or event.channel_user_name or event.chat_id
        parts = [platform, where]
        if scope is SessionScope.PER_CHAT_USER:
            who = event.channel_user_name or event.channel_user_id
            if who and who != where:
                parts.append(who)
        return "/".join(p for p in parts if p)
