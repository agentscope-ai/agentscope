# -*- coding: utf-8 -*-
"""WeCom (企业微信) channel — AI bot long-connection mode.

The bot holds a WebSocket to WeCom rather than exposing a callback URL,
so this channel needs no public endpoint and runs on the app event loop
like the Discord one, with no thread bridging.

Two platform rules shape the design. A message callback must be answered
within five seconds, far less than a run takes, so the channel opens a
streaming reply the moment a message arrives and hands the stream id to
``send_response``, which then refreshes it in place (ten-minute budget).
And WeCom keeps exactly one live connection per bot — a new connection
evicts the old — so every event for a bot lands in one process, which is
what lets the approval table below live in memory.
"""
import asyncio
import base64
import hashlib
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, TYPE_CHECKING

from pydantic import BaseModel, Field

from ...._logging import logger
from ...._utils._common import _generate_id
from ....event import ReplyEndEvent, RequireUserConfirmEvent
from ....message import Base64Source, DataBlock, Msg, TextBlock
from .._base import (
    ChannelBase,
    ChannelCapability,
    ChannelEvent,
    ChannelConfirmationResultEvent,
    ChannelStatus,
    ChatKind,
    _EVENT_ADAPTER,
    _NO_TEXT_REPLY,
)
from ._card_templates import (
    _build_approval_card,
    _parse_button_key,
    _resolved_card,
)

if TYPE_CHECKING:
    from .....tool import ToolBase
    from .....workspace import WorkspaceBase

_CMD_UPLOAD_INIT = "aibot_upload_media_init"
_CMD_UPLOAD_CHUNK = "aibot_upload_media_chunk"
_CMD_UPLOAD_FINISH = "aibot_upload_media_finish"

# Chunk ceilings from the upload API: 512KB per chunk, 100 chunks.
_UPLOAD_CHUNK_SIZE = 512 * 1024
_UPLOAD_MAX_CHUNKS = 100

# Shown while the agent is still working, so the five-second reply
# window is met before the run has produced anything.
_PENDING_REPLY = "Thinking…"
# Minimum seconds between live stream refreshes (throttle).
_STREAM_MIN_INTERVAL = 0.7
# Finish a stream nothing has written to for this long, so a message the
# gateway ended up not running leaves no reply stuck "thinking".
_STREAM_IDLE_SECS = 90.0
_STREAM_SWEEP_INTERVAL = 15.0
# Give up (and park in 'failed') after this many connects that never came
# up — the credentials are bad.
_MAX_CONNECT_ATTEMPTS = 2
# Hand a long-running reconnect streak back to the dispatcher rather than
# letting the SDK retry forever: its retry recurses through connect(), so
# an endless streak grows the stack. Exiting here starts us over clean.
_MAX_RECONNECTS = 200

_CHAT_TYPES = {"single": 1, "group": 2}
_MEDIA_TYPES = ("image", "file", "voice", "video")


@dataclass
class _Stream:
    """One open streaming reply, keyed by chat.

    ``frame`` is the inbound callback frame the stream belongs to; every
    refresh passes its ``req_id`` back to WeCom.
    """

    frame: dict
    stream_id: str
    touched_at: float


@dataclass
class _Pending:
    """The lookup keys a sent approval card answers on click."""

    tool_call_id: str
    chat_id: str
    agent_id: str = ""
    session_id: str = ""
    task_id: str = ""


@dataclass
class _Conn:
    """Connection bookkeeping shared between the SDK's callbacks and the
    supervising :meth:`WeComChannel.start_listening` loop."""

    authenticated: bool = False
    attempts: int = 0
    parked: bool = False
    error: str = ""


class WeComChannel(ChannelBase):
    """WeCom platform channel (AI bot long-connection mode)."""

    channel_type = "wecom"
    display_name = "WeCom (企业微信)"
    description = "Enterprise IM bot with streaming replies and cards."
    icon_url = (
        "https://www.google.com/s2/favicons?domain=work.weixin.qq.com&sz=128"
    )
    platform_bot_id_field = "bot_id"

    class Credentials(BaseModel):
        """WeCom AI bot credentials, from the bot's admin console page."""

        bot_id: str = Field(title="Bot ID", description="WeCom AI bot id")
        secret: str = Field(
            title="Bot Secret",
            description="WeCom AI bot secret",
            json_schema_extra={"format": "password"},
        )

    class Config(BaseModel):
        """WeCom platform options."""

        show_tool_process: bool = Field(
            default=False,
            title="Show tool process",
            description="Show tool calls and results inline in the reply",
        )
        show_thinking: bool = Field(
            default=False,
            title="Show thinking",
            description="Show the model's reasoning inline in the reply",
        )

    capabilities = ChannelCapability(
        text=True,
        markdown=True,
        image=True,
        file=True,
        interactive=True,
        streaming=True,
        max_message_length=4000,
    )

    def __init__(
        self,
        channel_id: str,
        credentials: "WeComChannel.Credentials",
        config: "WeComChannel.Config",
    ) -> None:
        """Read the credentials and options from the validated models.

        Args:
            channel_id (`str`):
                This channel instance's unique id.
            credentials (`WeComChannel.Credentials`):
                Validated bot id + secret.
            config (`WeComChannel.Config`):
                Validated platform options.
        """
        self._channel_id = channel_id
        self._bot_id = credentials.bot_id
        self._secret = credentials.secret
        self._config = config
        self.status = ChannelStatus()
        self._client: Any = None
        self._stopped = False
        self._conn = _Conn()
        self._streams: dict[str, _Stream] = {}
        self._pending: dict[str, _Pending] = {}
        self._chat_kind_cache: dict[str, ChatKind] = {}

    @property
    def channel_id(self) -> str:
        """The unique channel instance identifier."""
        return self._channel_id

    # -- Lifecycle --

    async def start_listening(
        self,
        emit: Callable[
            [ChannelEvent | ChannelConfirmationResultEvent],
            Awaitable[None],
        ],
    ) -> None:
        """Connect the bot, supervise the SDK's own reconnect loop, and
        close everything on exit.

        Args:
            emit (`Callable`): Gateway callback for inbound events.
        """
        try:
            from aibot import WSClient, WSClientOptions
        except ImportError as e:
            raise ImportError(
                "WeCom channel requires 'wecom-aibot-python-sdk' "
                "(pip install wecom-aibot-python-sdk).",
            ) from e

        self._emit = emit
        self.status.state = "connecting"
        client = WSClient(
            WSClientOptions(
                bot_id=self._bot_id,
                secret=self._secret,
                # Keep retrying a bot that has connected before; the
                # never-connected case is parked below instead, so a
                # transient drop is not mistaken for bad credentials.
                max_reconnect_attempts=_MAX_RECONNECTS,
            ),
        )
        self._client = client
        client.on("authenticated", self._on_authenticated)
        client.on("disconnected", self._on_disconnected)
        client.on("reconnecting", self._on_reconnecting)
        client.on("error", self._on_error)
        client.on("message", self._on_message)
        client.on("event.template_card_event", self._on_card_event)

        # The SDK retries inside connect(), which would block here for as
        # long as the bot stays unreachable — run it alongside the loop.
        connect_task = asyncio.create_task(
            client.connect(),
            name=f"wecom-connect:{self._channel_id}",
        )
        sweeper = asyncio.create_task(
            self._sweep_streams(),
            name=f"wecom-streams:{self._channel_id}",
        )
        try:
            while not self._stopped:
                await asyncio.sleep(5.0)
                # Retries exhausted without parking: end the listener so
                # the dispatcher's reconcile starts a fresh one.
                if connect_task.done() and not self._conn.parked:
                    self.status.last_error = (
                        self._conn.error or "connection lost"
                    )
                    logger.warning(
                        "WeCom '%s' exhausted reconnects, restarting: %s",
                        self._channel_id,
                        self.status.last_error,
                    )
                    break
                if self._conn.parked and self.status.state != "failed":
                    self.status.state = "failed"
                    self.status.last_error = (
                        self._conn.error or "connect failed"
                    )
                    logger.error(
                        "WeCom '%s' giving up after %d attempts: %s",
                        self._channel_id,
                        self._conn.attempts,
                        self.status.last_error,
                    )
        finally:
            self._stopped = True
            self.status.state = "stopped"
            for task in (connect_task, sweeper):
                task.cancel()
            await asyncio.gather(
                connect_task,
                sweeper,
                return_exceptions=True,
            )
            try:
                client.disconnect()
            except Exception:  # pylint: disable=broad-except
                logger.debug("WeCom '%s' disconnect failed", self._channel_id)
            self._client = None
            self._streams.clear()
            self._pending.clear()

    def _on_authenticated(self) -> None:
        """Mark the connection live once the subscribe frame is accepted."""
        self._conn.authenticated = True
        self._conn.attempts = 0
        self._conn.error = ""
        self.status.state = "connected"
        self.status.last_error = ""

    def _on_disconnected(self, reason: str) -> None:
        """Record a dropped connection; the SDK reconnects.

        Args:
            reason (`str`): The SDK's close reason.
        """
        if self._stopped or self._conn.parked:
            return
        self.status.state = "retrying"
        self.status.last_error = reason

    def _on_reconnecting(self, attempt: int) -> None:
        """Park the channel if it has never come up at all.

        A bot that has connected once is worth retrying; one that has
        never authenticated almost certainly has bad credentials, so stop
        and let the operator fix them.

        Args:
            attempt (`int`): The SDK's reconnect attempt counter.
        """
        self._conn.attempts = attempt
        if self._conn.authenticated or self._conn.parked:
            self.status.state = "retrying"
            return
        if attempt >= _MAX_CONNECT_ATTEMPTS:
            self._conn.parked = True
            client = self._client
            if client is not None:
                try:
                    client.disconnect()
                except Exception:  # pylint: disable=broad-except
                    logger.debug("WeCom '%s' park failed", self._channel_id)

    def _on_error(self, error: Exception) -> None:
        """Record an SDK-reported error.

        Args:
            error (`Exception`): The error the SDK surfaced.
        """
        self._conn.error = str(error)
        if not self._stopped and self.status.state != "failed":
            self.status.last_error = str(error)

    # -- Inbound --

    async def _on_message(self, frame: dict) -> None:
        """Normalise an inbound message, open its reply stream, emit it.

        Args:
            frame (`dict`): The raw ``aibot_msg_callback`` frame.
        """
        try:
            event = await self._normalize(frame)
            if event is None:
                return
            # The platform wants a reply within five seconds; a run takes
            # longer, so claim the stream now and let send_response fill
            # it. Media-only messages are buffered by the gateway rather
            # than run, so they get no stream.
            if any(isinstance(b, TextBlock) for b in event.content):
                await self._open_stream(frame, event.chat_id)
            if self._emit:
                await self._emit(event)
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "WeCom '%s' message handling failed",
                self._channel_id,
            )

    async def _normalize(self, frame: dict) -> ChannelEvent | None:
        """Convert an inbound WeCom message into a ``ChannelEvent``.

        Args:
            frame (`dict`): The raw ``aibot_msg_callback`` frame.

        Returns:
            `ChannelEvent | None`: The normalised event, or ``None`` when
            there is nothing to act on.
        """
        body = frame.get("body") or {}
        chat_id = body.get("chatid") or ""
        chat_type = body.get("chattype") or ""
        user_id = (body.get("from") or {}).get("userid") or ""
        msg_type = body.get("msgtype") or ""
        if chat_type in ("group", "single"):
            self._chat_kind_cache[chat_id] = (
                ChatKind.GROUP if chat_type == "group" else ChatKind.PRIVATE
            )

        content: list[TextBlock | DataBlock] = []
        if msg_type == "text":
            text = ((body.get("text") or {}).get("content") or "").strip()
            content = [TextBlock(text=text)] if text else []
        elif msg_type == "mixed":
            content = await self._parse_mixed(body)
        elif msg_type in _MEDIA_TYPES:
            block = await self._download(body.get(msg_type) or {}, msg_type)
            content = [block] if block else []
        elif chat_id:
            await self._send_msg(
                chat_id,
                chat_type,
                {
                    "msgtype": "markdown",
                    "markdown": {
                        "content": f"Unsupported message type: {msg_type}.",
                    },
                },
            )

        if not content:
            return None
        return ChannelEvent(
            channel_id=self._channel_id,
            channel_user_id=user_id,
            chat_id=chat_id,
            channel_message_id=body.get("msgid") or "",
            content=content,
            metadata={"chat_type": chat_type},
        )

    async def _parse_mixed(self, body: dict) -> list[TextBlock | DataBlock]:
        """Flatten a ``mixed`` message's items into ordered blocks.

        Args:
            body (`dict`): The inbound message body.

        Returns:
            `list[TextBlock | DataBlock]`: Text and data blocks in order.
        """
        blocks: list[TextBlock | DataBlock] = []
        items = (body.get("mixed") or {}).get("msg_item") or []
        for item in items:
            item_type = item.get("msgtype") or ""
            if item_type == "text":
                text = ((item.get("text") or {}).get("content") or "").strip()
                if text:
                    blocks.append(TextBlock(text=text))
            elif item_type in _MEDIA_TYPES:
                block = await self._download(
                    item.get(item_type) or {},
                    item_type,
                )
                if block is not None:
                    blocks.append(block)
        return blocks

    async def _download(self, media: dict, kind: str) -> DataBlock | None:
        """Download and decrypt one media resource into a base64 block.

        Args:
            media (`dict`): The message's media object (``url`` + ``aeskey``).
            kind (`str`): ``image`` / ``file`` / ``voice`` / ``video``.

        Returns:
            `DataBlock | None`: The block, or ``None`` on error.
        """
        url = media.get("url") or ""
        client = self._client
        if not url or client is None:
            return None
        try:
            data, filename = await client.download_file(
                url,
                media.get("aeskey") or "",
            )
        except Exception:  # pylint: disable=broad-except
            logger.debug("WeCom media download failed")
            return None
        media_type = {
            "image": "image/png",
            "voice": "audio/amr",
            "video": "video/mp4",
        }.get(kind, "application/octet-stream")
        return DataBlock(
            source=Base64Source(
                data=base64.b64encode(data).decode("ascii"),
                media_type=media_type,
            ),
            name=filename or kind,
        )

    async def _on_card_event(self, frame: dict) -> None:
        """Emit an approval-card click as a decision and freeze the card.

        Args:
            frame (`dict`): The raw ``template_card_event`` frame.
        """
        try:
            body = frame.get("body") or {}
            event = body.get("event") or {}
            parsed = _parse_button_key(event.get("event_key") or "")
            if parsed is None:
                return
            token, approved = parsed
            pending = self._pending.pop(token, None)
            if pending is None:
                return
            if self._emit:
                await self._emit(
                    ChannelConfirmationResultEvent(
                        channel_id=self._channel_id,
                        chat_id=pending.chat_id,
                        channel_user_id=(body.get("from") or {}).get(
                            "userid",
                        )
                        or "",
                        agent_id=pending.agent_id,
                        session_id=pending.session_id,
                        tool_call_id=pending.tool_call_id,
                        approved=approved,
                    ),
                )
            client = self._client
            if client is None:
                return
            task_id = event.get("task_id") or pending.task_id
            await client.update_template_card(
                frame,
                _resolved_card(task_id, approved),
            )
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "WeCom '%s' card click handling failed",
                self._channel_id,
            )

    # -- Outbound --

    async def send_response(
        self,
        event: ChannelEvent,
        events: AsyncIterator[dict],
    ) -> None:
        """Refresh the reply stream opened for this chat as the run
        produces output, then finish it; fall back to a pushed message
        when there is no stream (a scheduled or background run).

        Args:
            event (`ChannelEvent`): The send target (chat id).
            events (`AsyncIterator[dict]`): The run's session events.
        """
        stream = self._streams.pop(event.chat_id, None)
        reply: Msg | None = None
        confirm: RequireUserConfirmEvent | None = None
        last = 0.0
        async for raw in events:
            evt = _EVENT_ADAPTER.validate_python(raw)
            if isinstance(evt, RequireUserConfirmEvent):
                confirm = evt
                break
            reply_id = getattr(evt, "reply_id", None)
            if reply_id is not None:
                if reply is None:
                    reply = Msg(name="assistant", role="assistant", content=[])
                    reply.id = reply_id
                reply.append_event(evt)
            if isinstance(evt, ReplyEndEvent):
                break
            if stream is None or reply is None:
                continue
            text = self._text_of(reply)
            if not text:
                continue
            now = time.monotonic()
            if now - last >= _STREAM_MIN_INTERVAL:
                last = now
                await self._push(stream, text, finish=False)

        blocks = self._render(
            reply,
            show_thinking=self._config.show_thinking,
            show_tool_process=self._config.show_tool_process,
        )
        text = "".join(b.text for b in blocks if isinstance(b, TextBlock))
        data = [b for b in blocks if isinstance(b, DataBlock)]
        if not text and data:
            text = "\n".join(f"📎 {b.name or 'file'}" for b in data)
        await self._finish(event, stream, text)
        for block in data:
            await self._send_data_block(event.chat_id, block)
        if confirm is not None:
            await self._present_confirm(event, confirm)

    def _text_of(self, reply: Msg | None) -> str:
        """Render ``reply`` under this channel's config and join its text.

        Args:
            reply (`Msg | None`): The accumulated reply so far.
        """
        return "".join(
            b.text
            for b in self._render(
                reply,
                show_thinking=self._config.show_thinking,
                show_tool_process=self._config.show_tool_process,
            )
            if isinstance(b, TextBlock)
        )

    async def _open_stream(self, frame: dict, chat_id: str) -> None:
        """Start a streaming reply for ``chat_id``, replacing any open one.

        Args:
            frame (`dict`): The inbound frame the stream answers.
            chat_id (`str`): The chat the reply belongs to.
        """
        client = self._client
        if client is None or not chat_id:
            return
        previous = self._streams.pop(chat_id, None)
        if previous is not None:
            await self._push(previous, _PENDING_REPLY, finish=True)
        stream_id = f"stream-{_generate_id()}"
        try:
            await client.reply_stream(frame, stream_id, _PENDING_REPLY, False)
        except Exception:  # pylint: disable=broad-except
            logger.debug(
                "WeCom '%s' could not open a reply stream",
                self._channel_id,
            )
            return
        self._streams[chat_id] = _Stream(
            frame=frame,
            stream_id=stream_id,
            touched_at=time.monotonic(),
        )

    async def _push(
        self,
        stream: _Stream,
        text: str,
        *,
        finish: bool,
    ) -> None:
        """Write the reply so far into the stream.

        Args:
            stream (`_Stream`): The open stream to refresh.
            text (`str`): The full reply text so far (WeCom replaces the
                message content on each refresh).
            finish (`bool`): Whether this is the final refresh.
        """
        client = self._client
        if client is None:
            return
        stream.touched_at = time.monotonic()
        try:
            await client.reply_stream(
                stream.frame,
                stream.stream_id,
                text,
                finish,
            )
        except Exception:  # pylint: disable=broad-except
            logger.debug("WeCom '%s' stream refresh failed", self._channel_id)

    async def _finish(
        self,
        event: ChannelEvent,
        stream: _Stream | None,
        text: str,
    ) -> None:
        """Close the stream with the complete reply, or push it if the run
        had no inbound message to stream into.

        Args:
            event (`ChannelEvent`): The send target (chat id).
            stream (`_Stream | None`): The open stream, if any.
            text (`str`): The complete reply text.
        """
        if stream is not None:
            # Always close the stream, and never leave the placeholder as
            # the final word — a run that produced nothing should say so.
            await self._push(stream, text or _NO_TEXT_REPLY, finish=True)
            return
        for part in self._split_long_message(text):
            if part:
                await self._send_msg(
                    event.chat_id,
                    self._chat_type_of(event.chat_id),
                    {"msgtype": "markdown", "markdown": {"content": part}},
                )

    async def _send_data_block(self, chat_id: str, block: DataBlock) -> None:
        """Upload a reply attachment and send it to ``chat_id``.

        Args:
            chat_id (`str`): The chat to send to.
            block (`DataBlock`): The attachment to deliver.
        """
        if not isinstance(block.source, Base64Source):
            return
        raw = base64.b64decode(block.source.data)
        media_type = block.source.media_type or ""
        if media_type.startswith("image/") and self.capabilities.image:
            await self.send_image_to(chat_id, self._chat_type_of(chat_id), raw)
        elif self.capabilities.file:
            await self.send_file_to(
                chat_id,
                self._chat_type_of(chat_id),
                raw,
                block.name or "file",
            )

    async def _present_confirm(
        self,
        event: ChannelEvent,
        req: RequireUserConfirmEvent,
    ) -> None:
        """Push one approval card per tool call, each carrying the token
        that resolves back to its tool call on click.

        Cards are pushed rather than sent as a reply: WeCom allows only
        one template card per answered message, and a run can park on
        several tool calls at once.

        Args:
            event (`ChannelEvent`): The send target (chat id).
            req (`RequireUserConfirmEvent`): The approval request to show.
        """
        for tool in req.tool_calls:
            token = f"tg-{_generate_id()}"
            task_id = f"task-{_generate_id()}"
            self._pending[token] = _Pending(
                tool_call_id=tool.id,
                chat_id=event.chat_id,
                agent_id=event.metadata.get("agent_id", ""),
                session_id=event.metadata.get("session_id", ""),
                task_id=task_id,
            )
            await self._send_msg(
                event.chat_id,
                self._chat_type_of(event.chat_id),
                {
                    "msgtype": "template_card",
                    "template_card": _build_approval_card(
                        task_id,
                        token,
                        tool.name,
                        tool.input,
                    ),
                },
            )

    async def _sweep_streams(self) -> None:
        """Finish streams nothing has written to for a while, so a message
        that never started a run leaves no reply stuck "thinking"."""
        while not self._stopped:
            await asyncio.sleep(_STREAM_SWEEP_INTERVAL)
            now = time.monotonic()
            stale = [
                chat_id
                for chat_id, stream in self._streams.items()
                if now - stream.touched_at >= _STREAM_IDLE_SECS
            ]
            for chat_id in stale:
                stream = self._streams.pop(chat_id, None)
                if stream is not None:
                    await self._push(stream, _PENDING_REPLY, finish=True)

    # -- Chat metadata --

    async def chat_kind(self, chat_id: str) -> ChatKind | None:
        """Group vs 1:1 for a chat, from inbound messages. The AI bot API
        exposes no chat lookup, so a chat never seen is ``None``.

        Args:
            chat_id (`str`): The chat to classify.
        """
        return self._chat_kind_cache.get(chat_id)

    def _chat_type_of(self, chat_id: str) -> str:
        """The platform ``chattype`` for a chat, defaulting to ``single``.

        Args:
            chat_id (`str`): The chat to classify.
        """
        kind = self._chat_kind_cache.get(chat_id)
        return "group" if kind is ChatKind.GROUP else "single"

    # -- Agent-callable tools --

    async def list_tools(
        self,
        workspace: "WorkspaceBase",
    ) -> list["ToolBase"]:
        """Expose the WeCom send tools to the agent.

        Args:
            workspace (`WorkspaceBase`):
                The calling session's workspace; the send-file tools read
                their payload from its backend by absolute path.

        Returns:
            `list[ToolBase]`: The WeCom agent tools.
        """
        from ._tools import SendFile, SendImage, SendMessage

        backend = workspace.get_backend()
        return [
            SendMessage(self, backend),
            SendFile(self, backend),
            SendImage(self, backend),
        ]

    # -- Agent-tool operations (act on chats/users other than the current) --

    async def send_message_to(
        self,
        chat_id: str,
        chat_type: str,
        text: str,
    ) -> dict | None:
        """Push a markdown message to a user or group.

        Args:
            chat_id (`str`): A group's chat id, or a person's user id.
            chat_type (`str`): ``"single"`` or ``"group"``.
            text (`str`): The message text.

        Returns:
            `dict | None`: The platform ack, or an error frame.
        """
        return await self._send_msg(
            chat_id,
            chat_type,
            {"msgtype": "markdown", "markdown": {"content": text}},
        )

    async def send_file_to(
        self,
        chat_id: str,
        chat_type: str,
        data: bytes,
        file_name: str,
    ) -> dict | None:
        """Upload a file (→ media_id) then send it to a user or group.

        Args:
            chat_id (`str`): A group's chat id, or a person's user id.
            chat_type (`str`): ``"single"`` or ``"group"``.
            data (`bytes`): The file bytes.
            file_name (`str`): The file's display name.

        Returns:
            `dict | None`: The platform ack, or an error frame.
        """
        media_id = await self._upload_media("file", file_name, data)
        if not media_id:
            return {"errcode": -1, "errmsg": "media upload failed"}
        return await self._send_msg(
            chat_id,
            chat_type,
            {"msgtype": "file", "file": {"media_id": media_id}},
        )

    async def send_image_to(
        self,
        chat_id: str,
        chat_type: str,
        data: bytes,
    ) -> dict | None:
        """Upload an image (→ media_id) then send it to a user or group.

        Args:
            chat_id (`str`): A group's chat id, or a person's user id.
            chat_type (`str`): ``"single"`` or ``"group"``.
            data (`bytes`): The image bytes.

        Returns:
            `dict | None`: The platform ack, or an error frame.
        """
        media_id = await self._upload_media("image", "image.png", data)
        if not media_id:
            return {"errcode": -1, "errmsg": "media upload failed"}
        return await self._send_msg(
            chat_id,
            chat_type,
            {"msgtype": "image", "image": {"media_id": media_id}},
        )

    # -- WeCom frame helpers --

    async def _send_msg(
        self,
        chat_id: str,
        chat_type: str,
        payload: dict,
    ) -> dict | None:
        """Push a message frame to a chat, outside any reply window.

        Args:
            chat_id (`str`): A group's chat id, or a person's user id.
            chat_type (`str`): ``"single"`` or ``"group"``.
            payload (`dict`): The ``msgtype`` and its message object.

        Returns:
            `dict | None`: The platform ack, or an error frame.
        """
        client = self._client
        if client is None:
            return {"errcode": -1, "errmsg": "channel is not connected"}
        body = {"chat_type": _CHAT_TYPES.get(chat_type, 1), **payload}
        try:
            return await client.send_message(chat_id, body)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("WeCom send to '%s' failed: %s", chat_id, e)
            return {"errcode": -1, "errmsg": str(e)}

    async def _upload_media(
        self,
        kind: str,
        file_name: str,
        data: bytes,
    ) -> str | None:
        """Upload bytes in chunks and return the resulting ``media_id``.

        Args:
            kind (`str`): ``file`` / ``image`` / ``voice`` / ``video``.
            file_name (`str`): The upload's display name.
            data (`bytes`): The payload.

        Returns:
            `str | None`: The media id, or ``None`` on error.
        """
        chunks = [
            data[i : i + _UPLOAD_CHUNK_SIZE]
            for i in range(0, len(data), _UPLOAD_CHUNK_SIZE)
        ] or [b""]
        if len(chunks) > _UPLOAD_MAX_CHUNKS:
            logger.warning(
                "WeCom upload of '%s' is %d chunks, over the %d limit",
                file_name,
                len(chunks),
                _UPLOAD_MAX_CHUNKS,
            )
            return None
        init = await self._cmd(
            _CMD_UPLOAD_INIT,
            {
                "type": kind,
                "filename": file_name,
                "total_size": len(data),
                "total_chunks": len(chunks),
                "md5": hashlib.md5(data, usedforsecurity=False).hexdigest(),
            },
        )
        upload_id = ((init or {}).get("body") or {}).get("upload_id")
        if not upload_id:
            return None
        for index, chunk in enumerate(chunks):
            sent = await self._cmd(
                _CMD_UPLOAD_CHUNK,
                {
                    "upload_id": upload_id,
                    "chunk_index": index,
                    "base64_data": base64.b64encode(chunk).decode("ascii"),
                },
            )
            if sent is None:
                return None
        done = await self._cmd(_CMD_UPLOAD_FINISH, {"upload_id": upload_id})
        return ((done or {}).get("body") or {}).get("media_id")

    async def _cmd(self, cmd: str, body: dict) -> dict | None:
        """Send one self-initiated command frame and await its ack.

        Args:
            cmd (`str`): The WeCom command name.
            body (`dict`): The frame body.

        Returns:
            `dict | None`: The ack frame, or ``None`` on error.
        """
        client = self._client
        if client is None:
            return None
        try:
            return await client.reply(
                {"headers": {"req_id": f"{cmd}-{_generate_id()}"}},
                body,
                cmd,
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("WeCom %s failed: %s", cmd, e)
            return None
