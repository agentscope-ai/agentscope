# -*- coding: utf-8 -*-
"""Channel base abstractions: ChannelBase, ChannelEvent, ChannelCapability."""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, TYPE_CHECKING

from pydantic import BaseModel, Field

from ...message import TextBlock, DataBlock

if TYPE_CHECKING:
    from ._gateway import ChannelGateway


class ChannelEvent(BaseModel):
    """Normalised inbound message event from an external platform.

    This is an **internal** data structure consumed by the event
    processing pipeline — it is never exposed to the agent layer.
    The ``content`` field uses the same ``TextBlock`` / ``DataBlock``
    types as ``Msg.content``, so the pipeline can pass it directly to
    the agent without an extra conversion step.
    """

    channel_id: str
    """Source channel instance identifier."""

    channel_user_id: str
    """Platform-side unique user identifier."""

    chat_id: str
    """Platform-side chat/group identifier.

    Used for session mapping (``DmScope``) and routing-rule
    evaluation.  Platform adapters must populate this when
    constructing the ``ChannelEvent``.
    """

    channel_message_id: str | None = None
    """Platform-side message id for reply referencing."""

    content: list[TextBlock | DataBlock] = Field(default_factory=list)
    """Unified content blocks.

    Platform adapters should convert raw payloads into ``TextBlock``
    (for text) and ``DataBlock`` (for images, audio, files, etc.)
    when constructing the event.  This is the **single source of
    truth** for event content — there is no separate ``message`` or
    ``attachments`` field.
    """

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Platform-specific metadata: chat_type, tenant_key, etc."""

    received_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
    )

    @property
    def message(self) -> str:
        """Convenience accessor: concatenate all TextBlock texts."""
        return "".join(
            block.text
            for block in self.content
            if isinstance(block, TextBlock)
        )


class ChannelCapability(BaseModel):
    """Platform capability declaration for Gateway degradation decisions.

    Each ``ChannelBase`` subclass sets its ``capabilities`` to describe
    what the underlying platform supports.  ``ChannelGateway`` uses these
    flags to decide how to format agent output (e.g. whether to render
    markdown or strip it, whether to split long messages, etc.).

    All boolean flags describe the **send** direction (agent → platform)
    unless otherwise noted.
    """

    text: bool = True
    """Whether the platform can receive plain-text messages."""

    markdown: bool = False
    """Whether the platform can render Markdown in sent messages.

    When ``False``, the gateway should strip or convert Markdown
    formatting before sending.
    """

    image: bool = False
    """Whether the platform can receive image attachments."""

    file: bool = False
    """Whether the platform can receive file attachments."""

    streaming: bool = False
    """Whether the platform supports streaming / incremental delivery."""

    interactive_card: bool = False
    """Whether the platform supports interactive card messages.

    When ``False``, the HITL approval flow treats tool calls requiring
    user confirmation as timed-out (tool execution will not proceed),
    because there is no UI surface to present the approval card.
    Platforms that support cards (e.g. Feishu) should set this to ``True``
    and override ``build_approval_card``, ``send_interactive_card``,
    ``register_approval``, and ``resolve_approval``.
    """

    max_message_length: int = 4000
    """Maximum length of a single message in **characters** (Unicode).

    Messages exceeding this limit are split by
    ``ChannelBase._split_long_message`` before sending.
    """


class ChannelBase(ABC):
    """Abstract base class for platform channel adapters.

    A Channel handles three concerns:

    1. Establish and maintain a long-lived connection (WebSocket / stream /
       long-polling).
    2. Normalise platform payloads into ``ChannelEvent``.
    3. Send agent replies back to the platform.

    **Lifecycle:** ``ChannelManager`` creates channel instances via the
    ``ChannelTypeRegistry`` factory, calls ``set_gateway`` to inject the
    event dispatcher, then starts the channel via ``start_listening``.
    """

    _gateway: ChannelGateway | None = None
    """Event dispatcher injected by ``ChannelManager`` at startup.

    Channel adapters should only use this reference to dispatch normalised
    events via ``_gateway.handle_event(event)``.  They must **not** access
    or mutate internal gateway state.
    """

    capabilities: ChannelCapability = ChannelCapability()

    @property
    @abstractmethod
    def channel_id(self) -> str:
        """The unique channel instance identifier."""

    @abstractmethod
    async def start_listening(self) -> None:
        """Establish long-lived connection and loop receiving events.

        Implementations should include automatic reconnection.
        """

    @abstractmethod
    async def send_response(
        self,
        event: ChannelEvent,
        response: str,
    ) -> None:
        """Send agent reply text back to the platform.

        Direction: **agent service → channel platform**.

        Args:
            event: The original inbound ``ChannelEvent`` that triggered the
                agent run.  Used to extract routing information (e.g.
                ``chat_id``, ``channel_message_id``) so the reply is
                delivered to the correct chat / thread.
            response: The agent's text output to send.
        """

    # ── Reactions (agent service → channel platform) ──

    async def add_reaction(  # pylint: disable=unused-argument
        self,
        event: ChannelEvent,
        emoji_type: str,
    ) -> str | None:
        """Add an emoji reaction to the original platform message.

        Direction: **agent service → channel platform**.

        Called by ``ChannelGateway`` immediately after receiving an event
        to signal "processing" (e.g. ``"OnIt"``).

        Args:
            event: The inbound event whose ``channel_message_id`` identifies
                the message to react to.
            emoji_type: A platform-agnostic logical emoji name.

        Returns:
            An opaque reaction identifier for later removal via
            ``remove_reaction``, or ``None`` if reactions are not
            supported.
        """
        return None

    async def remove_reaction(
        self,
        event: ChannelEvent,
        reaction_id: str,
    ) -> None:
        """Remove a reaction previously added by ``add_reaction``.

        Direction: **agent service → channel platform**.

        Args:
            event: The original inbound event (for routing context).
            reaction_id: The identifier returned by ``add_reaction``.
        """

    # ── HITL / Approval card methods ──
    #
    # These methods support the Human-In-The-Loop approval flow.
    # When an agent requests tool confirmation (REQUIRE_USER_CONFIRM),
    # the gateway calls build_approval_card → send_interactive_card →
    # register_approval → (user clicks) → resolve_approval.
    #
    # Default behaviour for platforms WITHOUT interactive card support:
    #   - build_approval_card: returns plain-text fallback
    #   - send_interactive_card: returns None (no card sent)
    #   - register_approval: returns an immediately-resolved Future(None)
    #   - Result: _handle_approval returns None → treated as timeout/abort,
    #     tool execution will not proceed
    #
    # Platforms that support interactive cards (e.g. Feishu) should set
    # capabilities.interactive_card = True and override all four methods.

    def build_approval_card(  # pylint: disable=unused-argument
        self,
        *,
        request_id: str,
        tool_name: str,
        tool_input_summary: str = "",
        session_id: str = "",
        agent_id: str = "",
        user_id: str = "",
    ) -> str:
        """Build an interactive approval card for tool-guard.

        Subclasses should override to provide platform-specific card
        formatting (e.g. Feishu interactive card JSON).
        Default returns a plain-text fallback.

        Args:
            request_id: Unique identifier for this approval request.
            tool_name: Name of the tool requesting approval.
            tool_input_summary: A text summary of the tool call's input
                parameters (may be truncated from the original JSON input;
                gateway passes up to 500 characters).
            session_id: The AgentScope session this tool call belongs to.
            agent_id: The agent requesting the tool execution.
            user_id: The AgentScope user who owns the channel.

        Returns:
            Platform-specific card content string (e.g. JSON for Feishu).
        """
        return f"[Tool Approval] {tool_name}: {tool_input_summary[:200]}"

    def build_resolved_card(
        self,
        *,
        tool_name: str,
        action: str,
    ) -> str:
        """Build a resolved card (after user decision or timeout).

        Subclasses should override for platform-specific formatting.

        Args:
            tool_name: Name of the tool that was approved/denied.
            action: Resolution action, e.g. ``"approved"``, ``"denied"``,
                ``"timeout"``.
        """
        return f"[{action}] {tool_name}"

    async def send_interactive_card(  # pylint: disable=unused-argument
        self,
        event: ChannelEvent,
        card_content: str,
    ) -> str | None:
        """Send an interactive card message to the platform.

        Direction: **agent service → channel platform**.

        Returns the platform-side message_id of the sent card, or ``None``
        if the platform does not support interactive cards.  When ``None``
        is returned, the gateway treats the approval as timed-out (auto-deny).

        Args:
            event: The original inbound event (for routing context).
            card_content: Card content from ``build_approval_card``.
        """
        return None

    async def update_card(
        self,
        message_id: str,
        card_content: str,
    ) -> None:
        """Update an existing interactive card by message_id.

        Called after approval resolution to show the final status.

        Args:
            message_id: The platform message id returned by
                ``send_interactive_card``.
            card_content: Updated card content from ``build_resolved_card``.
        """

    def register_approval(  # pylint: disable=unused-argument
        self,
        request_id: str,
    ) -> asyncio.Future:
        """Register a pending approval and return a Future.

        The returned Future is resolved when the user clicks approve/deny
        on the interactive card.  Platform adapters that support cards
        should maintain a ``dict[str, Future]`` of pending approvals and
        resolve the corresponding Future in their card callback handler.

        Default implementation returns an immediately-resolved
        ``Future(None)``, which causes the gateway to treat the approval
        as timed-out (auto-deny).  This is the safe default for platforms
        that do not support interactive cards.

        Args:
            request_id: Unique identifier matching ``build_approval_card``.

        Returns:
            A ``Future[bool | None]`` — ``True`` for approved, ``False``
            for denied, ``None`` for timeout/unsupported.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        fut.set_result(None)
        return fut

    def resolve_approval(  # pylint: disable=unused-argument
        self,
        request_id: str,
        approved: bool,
    ) -> bool:
        """Resolve a pending approval by setting the Future result.

        Called by the platform callback handler when the user clicks
        approve/deny on the interactive card.

        Args:
            request_id: The approval request identifier.
            approved: ``True`` if user approved, ``False`` if denied.

        Returns:
            ``True`` if a pending approval was found and resolved,
            ``False`` otherwise.
        """
        return False

    async def on_start(self) -> None:
        """Initialise resources (HTTP clients, tokens, etc.)."""

    async def on_stop(self) -> None:
        """Release connection resources."""

    async def list_bot_chats(self) -> list[dict]:
        """Fetch the list of chats/groups the bot is in from the platform.

        Returns a list of dicts with at least 'chat_id' and 'name' keys.
        Default: empty (platform doesn't support or not implemented).
        """
        return []

    def set_gateway(self, gateway: ChannelGateway) -> None:
        """Inject the gateway reference for dispatching events.

        Called by ``ChannelManager`` during channel startup.  The channel
        adapter should use ``_gateway.handle_event(event)`` to dispatch
        normalised events and must not access gateway internals.
        """
        self._gateway = gateway

    def _split_long_message(
        self,
        text: str,
        max_length: int | None = None,
    ) -> list[str]:
        """Split text into chunks respecting platform length limits."""
        limit = max_length or self.capabilities.max_message_length
        if len(text) <= limit:
            return [text]
        parts: list[str] = []
        while text:
            parts.append(text[:limit])
            text = text[limit:]
        return parts
