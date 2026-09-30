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
from .._bus_ops import deliver_to_inbox, enqueue_run_trigger
from ..message_bus import MessageBus, MessageBusKeys
from ..storage import (
    ChannelRecord,
    ChatModelConfig,
    SessionConfig,
    SessionScope,
    ChannelOrigin,
    StorageBase,
)
from ..workspace_manager import WorkspaceManagerBase
from ._approval import forget_approval, load_approval
from ._base import (
    ChannelDecisionStatus,
    ChannelEvent,
    ChannelConfirmationResultEvent,
)
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
    ) -> ChannelDecisionStatus | None:
        """Handle one inbound event (message or confirmation decision).

        Args:
            event (`ChannelEvent | ChannelConfirmationResultEvent`): The
                inbound message or card-click decision.

        Returns:
            `ChannelDecisionStatus | None`: A decision outcome for card clicks,
            consumed by the platform adapter to update the card or show an
            authorization/staleness error; ``None`` for normal messages.
        """
        try:
            if isinstance(event, ChannelConfirmationResultEvent):
                return await self._handle_decision(event)
            await self._handle_message(event)
            return None
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "ChannelGateway.process failed for channel %s",
                event.channel_id,
            )
            return (
                ChannelDecisionStatus.ERROR
                if isinstance(event, ChannelConfirmationResultEvent)
                else None
            )

    async def _handle_decision(
        self,
        event: ChannelConfirmationResultEvent,
    ) -> ChannelDecisionStatus:
        """Resume the run for a card-click decision.

        The platform only returns an opaque approval id. Routing and the
        requester are loaded from server-side state, then the tool call is
        checked against the current session state before it is resumed.

        Args:
            event (`ChannelConfirmationResultEvent`): The click decision.
        """
        record = await self._storage.get_channel(event.channel_id)
        if record is None or not record.enabled or not event.approval_id:
            return ChannelDecisionStatus.STALE
        approval = await load_approval(self._bus, event.approval_id)
        if approval is None:
            return ChannelDecisionStatus.STALE
        wrong_channel = approval.channel_id != event.channel_id
        wrong_chat = approval.chat_id != event.chat_id
        if wrong_channel or wrong_chat:
            return ChannelDecisionStatus.STALE
        actor = event.actor or event.channel_user_id
        if approval.requester_id and actor != approval.requester_id:
            logger.warning(
                "channel '%s': decision by '%s' ignored; requester is '%s'",
                event.channel_id,
                actor,
                approval.requester_id,
            )
            return ChannelDecisionStatus.UNAUTHORIZED
        accepted = await resume_after_decision(
            self._bus,
            self._storage,
            user_id=record.user_id,
            agent_id=approval.agent_id,
            session_id=approval.session_id,
            tool_call_id=approval.tool_call_id,
            approved=event.approved,
            expected_reply_id=approval.reply_id,
            approval_id=event.approval_id,
        )
        if not accepted:
            return ChannelDecisionStatus.STALE
        try:
            await forget_approval(self._bus, event.approval_id)
        except Exception:  # pylint: disable=broad-except
            logger.warning(
                "channel '%s': accepted approval '%s' could not be removed",
                event.channel_id,
                event.approval_id,
            )
        return ChannelDecisionStatus.ACCEPTED

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
            await deliver_to_inbox(
                self._bus,
                user_id=record.user_id,
                session_id=session_id,
                agent_id=agent_id,
                payload=HintBlock(
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
            origin=ChannelOrigin(
                channel_id=record.id,
                chat_id=event.chat_id,
                chat_name=event.chat_name or None,
                channel_user_id=event.channel_user_id or None,
            ),
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
