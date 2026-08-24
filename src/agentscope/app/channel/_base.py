# -*- coding: utf-8 -*-
"""Channel base abstractions: events, capability, and the channel base.

A channel has exactly three concerns: keep a long-lived connection,
normalise platform payloads into :class:`ChannelEvent` /
:class:`ChannelConfirmationResultEvent` and emit them, and send the
gateway's outbound instructions back to the platform.

The channel never imports or holds the gateway; it receives an ``emit``
callback via :meth:`start_listening` and calls it. The dispatcher, in
turn, only feeds the channel a run's event stream via ``send_response``
(the channel folds and renders it) plus ``send_reaction`` — never its
connection loop.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Callable, TYPE_CHECKING

from pydantic import BaseModel, Field, TypeAdapter

from ...event import AgentEvent
from ...message import (
    DataBlock,
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from ...types import ReplyFinishedReason

if TYPE_CHECKING:
    from ...tool import ToolBase
    from ...workspace import WorkspaceBase

_NO_TEXT_REPLY = "(Agent returned no text content)"
_AGENT_ERROR_REPLY = (
    "❌ Agent encountered an error. Please check the agent configuration."
)
# Deserialize a bus event dict back into its typed AgentEvent.
_EVENT_ADAPTER: TypeAdapter = TypeAdapter(AgentEvent)


class ChannelEvent(BaseModel):
    """A normalised inbound message from an external platform.

    ``content`` reuses the same ``TextBlock`` / ``DataBlock`` types as
    ``Msg.content`` so the pipeline can hand it to the agent without a
    conversion step.
    """

    channel_id: str
    """Source channel instance identifier."""

    channel_user_id: str
    """Platform-side unique user identifier."""

    channel_user_name: str = ""
    """Platform-side user display name, when the channel can provide it
    cheaply; the gateway falls back to ``channel_user_id`` otherwise."""

    chat_id: str
    """Platform-side chat/group identifier. Drives session grouping and
    routing-rule matching."""

    chat_name: str = ""
    """Human-readable chat/group title, when the channel can provide it
    cheaply; used to name the derived session."""

    channel_message_id: str | None = None
    """Platform-side message id, for reply referencing."""

    content: list[TextBlock | DataBlock] = Field(default_factory=list)
    """Unified content blocks — the single source of truth for content."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Platform-specific metadata: chat_type, tenant_key, etc. Available
    to routing rules via ``match_key``."""

    received_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
    )

    @property
    def message(self) -> str:
        """Concatenate all ``TextBlock`` texts (convenience accessor)."""
        return "".join(
            block.text
            for block in self.content
            if isinstance(block, TextBlock)
        )


class ChannelConfirmationResultEvent(BaseModel):
    """A user's decision on a pending tool-approval, delivered inbound.

    Enters through the *same* gateway entry point as messages. Carries
    only lookup keys — the authoritative pending tool call is read from
    the session state, never trusted from this round-tripped payload.
    """

    channel_id: str
    """Source channel instance identifier."""

    chat_id: str
    """Platform chat the decision came from; routes to the session."""

    channel_user_id: str
    """Platform user who decided; routes to the session."""

    agent_id: str = ""
    """Target agent resolved when the card was sent; used directly on
    click so a re-route or metadata-based rule can't misroute the
    decision. Empty on older cards → fall back to routing."""

    session_id: str = ""
    """Target session resolved when the card was sent; paired with
    ``agent_id`` to resume the exact run that is asking."""

    tool_call_id: str
    """Id of the tool call being answered — the correlation key, matched
    against the session's awaiting confirmations."""

    approved: bool
    """The user's decision."""

    actor: str = ""
    """Platform-side id of whoever made the decision (for audit)."""


class ChannelStatus(BaseModel):
    """Live connection status of a running channel adapter — owned and
    updated by the adapter itself; read by the dispatcher's status API.

    ``state`` transitions (all set by the adapter):
    ``stopped`` (default / on teardown) → ``connecting`` → ``connected``;
    ``retrying`` while reconnecting after a drop; ``failed`` when the
    adapter gave up (parked, no more retries until the channel is edited).
    """

    state: str = "stopped"
    last_error: str = ""


# How long a node's status report stays trustworthy.
LIVENESS_TTL_SECS = 30


class ChannelHeartbeat(BaseModel):
    """One node's report of a channel's status, stamped with the time it
    was written.

    The bus expires a registry namespace as a whole rather than per
    field, so a node that stops reporting — a worker that restarted
    under a fresh id, say — leaves its last entry behind while its
    successor keeps the namespace alive. Readers therefore drop reports
    older than :data:`LIVENESS_TTL_SECS` instead of trusting expiry.
    """

    status: ChannelStatus
    reported_at: float
    """Wall-clock seconds since the epoch. Compared across nodes, so a
    little clock skew is expected and harmless at this TTL."""

    def is_fresh(self, now: float) -> bool:
        """Whether this report is recent enough to believe.

        Args:
            now (`float`): The reader's current epoch time.
        """
        return now - self.reported_at <= LIVENESS_TTL_SECS


class ChatKind(str, Enum):
    """A chat's audience shape, used to tailor the agent's context.

    ``GROUP`` — many participants; ``PRIVATE`` — a 1:1 conversation.
    Unknown / not-applicable is represented by ``None`` at call sites,
    not an enum member.
    """

    GROUP = "group"
    PRIVATE = "private"


class ChannelCapability(BaseModel):
    """Platform capability declaration for gateway degradation decisions.

    All flags describe the send direction (agent → platform).
    """

    text: bool = True
    markdown: bool = False
    image: bool = False
    file: bool = False
    interactive: bool = False
    """Whether the platform can present an interactive confirmation UI.
    Declarative metadata; each channel renders approvals in its own
    ``send_response`` (a card, a text prompt, ...)."""

    streaming: bool = False
    """Whether the platform can update one reply message in place. A
    channel decides how to render inside ``send_response``; this is
    declarative metadata for the management UI."""

    max_message_length: int = 4000
    """Max characters per message; longer replies are split before send."""


class ChannelBase(ABC):
    """Abstract base for platform channels.

    A subclass fully describes its platform type on the class itself, so
    the service can register it from :func:`~agentscope.app.create_app`
    (``channels=[FeishuChannel, ...]``) without a separate registry
    table:

    - :attr:`channel_type` / :attr:`display_name` /
      :attr:`platform_bot_id_field` — type metadata;
    - nested :class:`Credentials` / :class:`Config` — the credential and
      option models, overridden by each subclass; the service renders
      their JSON Schema as frontend forms and validates against them;
    - ``__init__(channel_id, credentials, config)`` — the uniform
      construction contract; instances are built per stored channel with
      that channel's validated ``Credentials`` / ``Config``.
    """

    channel_type: str
    """Unique platform type id (e.g. ``"feishu"``). Subclasses set this."""

    display_name: str
    """Human-readable platform name for the management UI."""

    platform_bot_id_field: str
    """Credential field that uniquely identifies the bot, used to reject
    binding the same bot to two channels."""

    description: str = ""
    """One-line platform description for the management UI."""

    icon_url: str = ""
    """Brand icon URL for the management UI; empty falls back to a
    generated avatar."""

    class Credentials(BaseModel):
        """Secret connection fields (app id, tokens, ...). Subclasses
        override with their own fields; mark a field secret with
        ``json_schema_extra={"format": "password"}``."""

    class Config(BaseModel):
        """Non-secret per-channel behaviour switches. Subclasses override;
        left empty when the platform exposes no options."""

    def __init__(
        self,
        channel_id: str,
        credentials: "ChannelBase.Credentials",
        config: "ChannelBase.Config",
    ) -> None:
        """Build a channel from its validated credentials and config.

        Args:
            channel_id (`str`): This channel instance's unique id.
            credentials (`ChannelBase.Credentials`): Validated secrets.
            config (`ChannelBase.Config`): Validated platform options.
        """

    capabilities: ChannelCapability = ChannelCapability()

    status: ChannelStatus
    """Live connection status; each channel creates its own in ``__init__``
    (``self.status = ChannelStatus()``) and updates it. Read via the
    dispatcher's status API."""

    _emit: (
        Callable[
            ["ChannelEvent | ChannelConfirmationResultEvent"],
            Awaitable[None],
        ]
        | None
    ) = None
    """Gateway entry point, stored by :meth:`start_listening`. Channels
    dispatch normalised events via ``await self._emit(event)`` and must
    not access any other gateway state."""

    # -- Identity & connection --

    @property
    @abstractmethod
    def channel_id(self) -> str:
        """The unique channel instance identifier."""

    @abstractmethod
    async def start_listening(
        self,
        emit: Callable[
            ["ChannelEvent | ChannelConfirmationResultEvent"],
            Awaitable[None],
        ],
    ) -> None:
        """Store ``emit``, set up resources, then connect and loop
        receiving events — normalising each into a ``ChannelEvent`` and
        ``await self._emit(event)`` (auto-reconnect) — releasing
        resources in a ``finally``. The sole connection-lifecycle method.

        Args:
            emit (`Callable`): Gateway callback for inbound events; store
                as ``self._emit`` and call ``await self._emit(event)``.
        """

    # -- Outbound (agent service → platform). Dispatcher-invoked. --

    @abstractmethod
    async def send_response(
        self,
        event: ChannelEvent,
        events: AsyncIterator[dict],
    ) -> None:
        """Consume the run's agent-event stream and deliver the reply.

        Accumulate the events into a ``Msg`` (:meth:`Msg.append_event`,
        via :data:`_EVENT_ADAPTER`), render with :meth:`_render`, and send
        to the platform — streaming or one-shot as the channel chooses;
        present a confirmation when the run parks.

        Args:
            event (`ChannelEvent`): The send target (chat id).
            events (`AsyncIterator[dict]`): The run's session events.
        """

    def _render(
        self,
        reply: Msg | None,
        *,
        show_thinking: bool = False,
        show_tool_process: bool = False,
    ) -> list[TextBlock | DataBlock]:
        """Render an accumulated reply into deliverable blocks. The caller
        (each channel) passes its own ``Config`` flags — the base makes no
        assumptions about how a subclass stores them.

        Args:
            reply (`Msg | None`): The accumulated reply.
            show_thinking (`bool`): Include thinking blocks inline.
            show_tool_process (`bool`): Include tool call/result inline.

        Returns:
            `list[TextBlock | DataBlock]`: Text (+ data) blocks to send.
        """
        if reply is None:
            return []
        parts: list[str] = []
        data: list[DataBlock] = []
        for block in reply.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
            elif isinstance(block, DataBlock):
                data.append(block)
            elif isinstance(block, ThinkingBlock):
                if show_thinking:
                    parts.append(f"💭 {block.thinking}")
            elif isinstance(block, ToolCallBlock):
                if show_tool_process:
                    parts.append(f"🔧 Calling tool: {block.name}")
            elif isinstance(block, ToolResultBlock):
                if show_tool_process and isinstance(block.output, str):
                    parts.append(block.output)
        # Every block is already folded whole, and Markdown needs a blank
        # line between them or thinking runs into the text that follows.
        text = "\n\n".join(part for part in parts if part.strip()).strip()
        if reply.finished_reason == ReplyFinishedReason.ERROR:
            text = text or _AGENT_ERROR_REPLY
        elif not text and not data:
            text = _NO_TEXT_REPLY
        blocks: list[TextBlock | DataBlock] = (
            [TextBlock(text=text)] if text else []
        )
        blocks.extend(data)
        return blocks

    async def aclose(self) -> None:
        """Release resources acquired outside :meth:`start_listening`.

        An instance built by
        :class:`~agentscope.app.channel.ChannelClients` never runs the
        connection loop, so whatever its REST calls opened lazily has no
        ``finally`` to close it. Override to close those; the connection
        loop keeps releasing its own. Default: nothing to do.
        """

    async def send_reaction(  # pylint: disable=unused-argument
        self,
        event: ChannelEvent,
        emoji_type: str,
    ) -> str | None:
        """Add an emoji reaction to the inbound message (e.g. "OnIt").

        Args:
            event (`ChannelEvent`): The inbound event to react to.
            emoji_type (`str`): Platform emoji/reaction key.

        Returns:
            `str | None`: Reaction id for :meth:`remove_reaction`, or
            ``None`` if unsupported.
        """
        return None

    async def remove_reaction(
        self,
        event: ChannelEvent,
        reaction_id: str,
    ) -> None:
        """Remove a reaction added by :meth:`send_reaction`. Default: no-op.

        Args:
            event (`ChannelEvent`): The inbound event that was reacted to.
            reaction_id (`str`): The id returned by :meth:`send_reaction`.
        """

    # -- Optional management-UI helpers --

    async def list_bot_chats(self) -> list[dict]:
        """Fetch the bot's chats/groups as dicts with at least ``chat_id``
        and ``name``. Default: empty (unsupported).
        """
        return []

    async def chat_kind(  # pylint: disable=unused-argument
        self,
        chat_id: str,
    ) -> "ChatKind | None":
        """Best-effort audience shape of ``chat_id`` (group vs 1:1), used
        to tailor the agent's session context. Default: ``None`` unknown.

        Args:
            chat_id (`str`): The platform chat to classify.
        """
        return None

    async def chat_name(  # pylint: disable=unused-argument
        self,
        chat_id: str,
    ) -> str:
        """Best-effort human display name of ``chat_id`` (e.g. a group
        title), used to tailor the agent's context. Default: ``""``.

        Args:
            chat_id (`str`): The platform chat to name.
        """
        return ""

    # -- Agent-callable tools --

    async def list_tools(  # pylint: disable=unused-argument
        self,
        workspace: "WorkspaceBase",
    ) -> list["ToolBase"]:
        """Platform tools exposed to the agent — e.g. send a file to a
        different user/group than the conversation. Default: none.

        Args:
            workspace (`WorkspaceBase`): The calling session's workspace,
                so file-sending tools read from it, not the host.
        """
        return []

    def _split_long_message(self, text: str) -> list[str]:
        """Split text into chunks within the platform length limit.

        Args:
            text (`str`): The text to split.
        """
        limit = self.capabilities.max_message_length
        if len(text) <= limit:
            return [text]
        return [text[i : i + limit] for i in range(0, len(text), limit)]
