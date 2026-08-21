# -*- coding: utf-8 -*-
"""Feishu (Lark) channel — new ChannelBase interface.

Translates the Feishu platform to/from normalised events and emits them
via the injected gateway callback. On a card click the channel freezes
its own card and emits a ``ChannelConfirmationResultEvent`` (same entry
as messages) carrying the tool call's id. No in-process approval futures
or attachment buffers — the awaiting confirmation lives in session state.

The WebSocket runs in a background thread (the lark SDK owns its own
event loop); inbound events are bridged to the app loop with
``run_coroutine_threadsafe``. Connection/reconnect is driven via the
SDK's public ``start()`` — the one place to adapt if the SDK changes.
"""
import asyncio
import base64
import json
import threading
import time
from typing import Any, AsyncIterator, Awaitable, Callable, TYPE_CHECKING

from pydantic import BaseModel, Field

from ...._logging import logger
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
)
from ._card_templates import (
    _build_action_response,
    _build_approval_card,
    _build_toast,
    _parse_action,
)

if TYPE_CHECKING:
    import httpx
    from lark_oapi.api.im.v1 import EventMessage, P2ImMessageReceiveV1
    from lark_oapi.event.callback.model.p2_card_action_trigger import (
        P2CardActionTrigger,
        P2CardActionTriggerResponse,
    )
    from .....tool import ToolBase
    from .....workspace import WorkspaceBase

_API = "https://open.feishu.cn/open-apis"
_TOKEN_EXPIRED_CODES = frozenset({99991663, 99991664})
_MEDIA_TYPES = frozenset({"image", "audio", "media", "file"})
_STREAM_ELEMENT_ID = "md"
# Minimum seconds between live streaming-card updates (throttle).
_STREAM_MIN_INTERVAL = 0.7


class _ThreadLoopProxy:
    """Forward attribute access to the *current thread's* event loop.

    ``lark_oapi.ws.client`` drives ``client.start()`` off one module-
    global ``loop``. Replacing that global with this proxy makes every
    ``loop.<attr>`` resolve to the loop of whichever thread is calling —
    so multiple Feishu WS threads (one per bot) each use their own loop
    instead of sharing one global.
    """

    def __getattr__(self, name: str) -> Any:
        """Resolve any attribute on the current thread's event loop.

        Args:
            name (`str`): The loop attribute the SDK is reaching for.

        Returns:
            `Any`: The attribute from ``asyncio.get_event_loop()``.
        """
        return getattr(asyncio.get_event_loop(), name)


_THREAD_LOOP_PROXY = _ThreadLoopProxy()

# Give up (and let the dispatcher disable the channel) after this many
# consecutive connects that never came up — the credentials are bad.
_MAX_CONNECT_ATTEMPTS = 2


class FeishuChannel(ChannelBase):
    """Feishu platform channel (SDK long-connection mode)."""

    channel_type = "feishu"
    display_name = "Feishu (Lark)"
    description = "Group and direct-message bot with card interactions."
    icon_url = "https://www.google.com/s2/favicons?domain=feishu.cn&sz=128"
    platform_bot_id_field = "app_id"

    class Credentials(BaseModel):
        """Feishu bot application credentials."""

        app_id: str = Field(title="App ID", description="Feishu App ID")
        app_secret: str = Field(
            title="App Secret",
            description="Feishu App Secret",
            json_schema_extra={"format": "password"},
        )

    class Config(BaseModel):
        """Feishu platform options."""

        only_at_reply: bool = Field(
            default=True,
            title="Reply only when mentioned",
            description="In group chats, reply only when the bot is "
            "@mentioned",
        )
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
        credentials: "FeishuChannel.Credentials",
        config: "FeishuChannel.Config",
    ) -> None:
        """Read the credentials and options from the validated models.

        Args:
            channel_id (`str`):
                This channel instance's unique id.
            credentials (`FeishuChannel.Credentials`):
                Validated app id + secret.
            config (`FeishuChannel.Config`):
                Validated platform options.
        """
        self._channel_id = channel_id
        self._app_id = credentials.app_id
        self._app_secret = credentials.app_secret
        self._config = config
        self.status = ChannelStatus()
        self._http: "httpx.AsyncClient | None" = None
        self._token: str | None = None
        self._bot_open_id: str | None = None
        self._ws_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        # The WS thread's own loop, captured so teardown can stop it and
        # unblock lark's otherwise-forever ``client.start()``.
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._stream_seq: dict[str, int] = {}
        self._chat_name_cache: dict[str, str] = {}
        self._user_name_cache: dict[str, str] = {}
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
        """Open the HTTP client, run the WS client (reconnecting with
        backoff), and close everything on exit.

        Args:
            emit (`Callable`): Gateway callback for inbound events.
        """
        self._emit = emit
        self.status.state = "connecting"
        self._loop = asyncio.get_running_loop()
        backoff = 1.0
        attempts = 0
        ever_connected = False
        try:
            while not self._stop.is_set():
                uptime = 0.0
                try:
                    await self._refresh_token()
                    if self._bot_open_id is None:
                        info = await self._api("GET", f"{_API}/bot/v3/info")
                        self._bot_open_id = (
                            (info or {}).get("bot", {}).get("open_id")
                        )
                    self._ws_thread = self._launch_ws_thread()
                    while (
                        not self._stop.is_set() and self._ws_thread.is_alive()
                    ):
                        await asyncio.sleep(5.0)
                        uptime += 5.0
                        self.status.state = "connected"
                        self.status.last_error = ""
                        ever_connected = True
                        attempts = 0
                except Exception as e:  # pylint: disable=broad-except
                    self.status.state = "retrying"
                    self.status.last_error = str(e)
                    logger.exception(
                        "Feishu WS '%s' connect failed",
                        self._channel_id,
                    )
                if self._stop.is_set():
                    break
                # Give up only when we have NEVER connected — bad credentials
                # never will. Park in 'failed' (keep the task alive so the
                # dispatcher won't restart it); editing the channel re-tries.
                # Transient drops after a good connect keep retrying.
                if not ever_connected:
                    attempts += 1
                    if attempts >= _MAX_CONNECT_ATTEMPTS:
                        self.status.state = "failed"
                        if not self.status.last_error:
                            self.status.last_error = "connect failed"
                        logger.error(
                            "Feishu WS '%s' giving up after %d attempts: %s",
                            self._channel_id,
                            attempts,
                            self.status.last_error,
                        )
                        while not self._stop.is_set():
                            await asyncio.sleep(30.0)
                        break
                backoff = 1.0 if uptime >= 60.0 else min(backoff * 2, 30.0)
                logger.warning(
                    "Feishu WS '%s' exited, reconnecting in %.1fs",
                    self._channel_id,
                    backoff,
                )
                await asyncio.sleep(backoff)
        finally:
            self._stop.set()
            self.status.state = "stopped"
            # Stop the WS thread's loop so lark's ``client.start()`` (parked
            # forever on ``run_until_complete(_select())``) returns; without
            # this the daemon thread lingers and keeps delivering events
            # after the channel is disabled/updated.
            ws_loop = self._ws_loop
            if ws_loop is not None:
                try:
                    ws_loop.call_soon_threadsafe(ws_loop.stop)
                except RuntimeError:
                    pass  # loop already closed
            if self._ws_thread and self._ws_thread.is_alive():
                self._ws_thread.join(timeout=5.0)
            if self._http:
                await self._http.aclose()
                self._http = None

    def _launch_ws_thread(self) -> threading.Thread:
        """Start the lark WS client on a daemon thread with its own loop.

        Returns:
            `threading.Thread`: The started WS thread (daemon).
        """
        try:
            import lark_oapi as lark
        except ImportError as e:
            raise ImportError(
                "Feishu channel requires 'lark-oapi' "
                "(pip install lark-oapi).",
            ) from e

        loop = self._loop
        assert loop is not None  # set in start_listening before this runs

        def on_message(data: "P2ImMessageReceiveV1") -> None:
            """Bridge an inbound message onto the app loop.

            Args:
                data (`P2ImMessageReceiveV1`): The SDK message event.
            """
            asyncio.run_coroutine_threadsafe(self._on_message(data), loop)

        def on_card_action(
            data: "P2CardActionTrigger",
        ) -> "P2CardActionTriggerResponse":
            """Handle a card click; return the toast ack.

            Args:
                data (`P2CardActionTrigger`): The SDK card-action event.
            """
            return self._on_card_action(data, loop)

        def ignore(_data: object) -> None:
            """No-op for subscribed events we don't act on, so the SDK
            doesn't log 'processor not found'."""

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message)
            .register_p2_card_action_trigger(on_card_action)
            .register_p2_im_message_reaction_created_v1(ignore)
            .register_p2_im_message_reaction_deleted_v1(ignore)
            .build()
        )
        client = lark.ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
        )

        def run() -> None:
            """Run the blocking WS client on this thread's own loop."""
            try:
                # lark's ws client drives one import-time global loop; give
                # this thread its own loop + a proxy so bots don't share it.
                import lark_oapi.ws.client as _ws_client

                thread_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(thread_loop)
                self._ws_loop = thread_loop
                _ws_client.loop = _THREAD_LOOP_PROXY
                client.start()  # blocks on this thread's loop until closed
            except Exception as e:  # pylint: disable=broad-except
                if not self._stop.is_set():
                    self.status.state = "retrying"
                    self.status.last_error = str(e)
                    logger.exception(
                        "Feishu WS '%s' crashed",
                        self._channel_id,
                    )

        thread = threading.Thread(
            target=run,
            name=f"feishu-ws:{self._channel_id}",
            daemon=True,
        )
        thread.start()
        return thread

    # -- Inbound (WS thread → app loop) --

    async def _on_message(self, data: "P2ImMessageReceiveV1") -> None:
        """Normalise an inbound message and emit it (media or text).

        Args:
            data (`P2ImMessageReceiveV1`):
                The inbound message-receive event from the SDK.
        """
        try:
            event = await self._normalize(data)
            if event and self._emit:
                await self._emit(event)
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "Feishu '%s' message handling failed",
                self._channel_id,
            )

    async def _normalize(
        self,
        data: "P2ImMessageReceiveV1",
    ) -> ChannelEvent | None:
        """Convert an inbound Feishu message into a ``ChannelEvent``,
        downloading media and honouring ``only_at_reply`` in groups.

        Args:
            data (`P2ImMessageReceiveV1`):
                The inbound message-receive event.

        Returns:
            `ChannelEvent | None`:
                The normalised event, or ``None`` when there is nothing
                to act on.
        """
        message = getattr(data.event, "message", None)
        sender = getattr(data.event, "sender", None)
        if message is None or sender is None:
            return None

        user_id = ""
        if sender.sender_id:
            user_id = sender.sender_id.open_id or ""
        chat_id = message.chat_id or ""
        chat_type = message.chat_type or ""
        message_id = message.message_id or ""
        meta = {
            "chat_type": chat_type,
            "tenant_key": data.header.tenant_key if data.header else "",
        }
        if chat_id and chat_type in ("group", "p2p"):
            self._chat_kind_cache[chat_id] = (
                ChatKind.GROUP if chat_type == "group" else ChatKind.PRIVATE
            )

        # @-gate up front so unmentioned group media is dropped too — it
        # used to skip the check, get downloaded, and buffer into the
        # sender's next @-mention.
        if self._gated_out(message, chat_type):
            return None

        msg_type = message.message_type
        content: list[TextBlock | DataBlock] = []
        if msg_type in _MEDIA_TYPES:
            block = await self._download_media(message, msg_type)
            content = [block] if block else []
        elif msg_type == "post":
            content = await self._parse_post(
                json.loads(message.content or "{}"),
                message_id,
            )
        elif msg_type == "text":
            text = (
                json.loads(message.content or "{}").get("text") or ""
            ).strip()
            if chat_type == "group" and self._config.only_at_reply:
                for mention in message.mentions or []:
                    text = text.replace(mention.key or "", "").strip()
            content = [TextBlock(text=text)] if text else []
        elif message_id or chat_id:
            await self._send(
                message_id,
                chat_id,
                "text",
                json.dumps({"text": f"Unsupported message type: {msg_type}."}),
            )

        if not content:
            return None
        return ChannelEvent(
            channel_id=self._channel_id,
            channel_user_id=user_id,
            channel_user_name=await self._user_name(user_id),
            chat_id=chat_id,
            chat_name=(
                await self.chat_name(chat_id) if chat_type == "group" else ""
            ),
            channel_message_id=message_id,
            content=content,
            metadata=meta,
        )

    async def chat_name(self, chat_id: str) -> str:
        """The chat's title (cached per chat); ``""`` when unavailable —
        a 1:1 chat, a not-yet-named group, or a missing ``im:chat`` scope.

        Args:
            chat_id (`str`): The chat to look up.
        """
        if not chat_id:
            return ""
        cached = self._chat_name_cache.get(chat_id)
        if cached:
            return cached
        # Only cache a non-empty result, so a missing scope / not-yet-named
        # group is retried on the next message rather than stuck empty.
        data = await self._api("GET", f"{_API}/im/v1/chats/{chat_id}")
        name = (data or {}).get("data", {}).get("name", "") or ""
        if name:
            self._chat_name_cache[chat_id] = name
        return name

    async def chat_kind(self, chat_id: str) -> ChatKind | None:
        """Group vs 1:1 for a chat — from the inbound cache, else the
        get-chat API (``chat_mode``). ``None`` when unknown.

        Args:
            chat_id (`str`): The chat to classify.
        """
        if not chat_id:
            return None
        cached = self._chat_kind_cache.get(chat_id)
        if cached is not None:
            return cached
        data = await self._api("GET", f"{_API}/im/v1/chats/{chat_id}")
        mode = (data or {}).get("data", {}).get("chat_mode", "")
        kind = {"group": ChatKind.GROUP, "p2p": ChatKind.PRIVATE}.get(mode)
        if kind is not None:
            self._chat_kind_cache[chat_id] = kind
        return kind

    async def _user_name(self, open_id: str) -> str:
        """The sender's display name (cached per user); empty on failure.

        Args:
            open_id (`str`): The sender's open_id.
        """
        if not open_id:
            return ""
        cached = self._user_name_cache.get(open_id)
        if cached:
            return cached
        data = await self._api(
            "GET",
            f"{_API}/contact/v3/users/{open_id}?user_id_type=open_id",
        )
        name = (data or {}).get("data", {}).get("user", {}).get("name", "")
        name = name or ""
        if name:
            self._user_name_cache[open_id] = name
        else:
            logger.warning(
                "Feishu user '%s' returned no name (check "
                "contact:user.base:readonly scope)",
                open_id,
            )
        return name

    def _gated_out(self, message: "EventMessage", chat_type: str) -> bool:
        """Whether a group message is dropped by ``only_at_reply`` — kept
        only when the bot itself is @mentioned (mentions of other members
        do not count).

        Args:
            message (`EventMessage`): The inbound message.
            chat_type (`str`): ``"group"`` / ``"p2p"`` / etc.

        Returns:
            `bool`: ``True`` to ignore the message.
        """
        if chat_type != "group" or not self._config.only_at_reply:
            return False
        mentions = message.mentions or []
        if self._bot_open_id:
            return not any(
                getattr(getattr(m, "id", None), "open_id", "")
                == self._bot_open_id
                for m in mentions
            )
        # Bot identity unknown (info fetch failed / lacked open_id): we
        # cannot verify the @ was for us, so fail closed rather than let
        # every group message through.
        logger.warning(
            "Feishu '%s' bot id unknown; dropping unverified group message",
            self._channel_id,
        )
        return True

    async def _parse_post(
        self,
        content: dict,
        message_id: str,
    ) -> list[TextBlock | DataBlock]:
        """Flatten a Feishu ``post`` (nested rich-text rows) into ordered
        text and image blocks, downloading each embedded resource.

        Args:
            content (`dict`): The parsed ``post`` content (title + rows).
            message_id (`str`): The message id, for the resource endpoint.

        Returns:
            `list[TextBlock | DataBlock]`: Text and data blocks in order.
        """
        blocks: list[TextBlock | DataBlock] = []
        parts: list[str] = []

        def _flush() -> None:
            text = "".join(parts).strip()
            parts.clear()
            if text:
                blocks.append(TextBlock(text=text))

        if content.get("title"):
            parts.append(content["title"] + "\n")
        for row in content.get("content") or []:
            for element in row or []:
                tag = element.get("tag")
                if tag == "text":
                    parts.append(element.get("text", ""))
                elif tag == "a":
                    parts.append(
                        element.get("text") or element.get("href") or "",
                    )
                elif tag in ("img", "media"):
                    key = element.get("image_key") or element.get("file_key")
                    if not key:
                        continue
                    _flush()
                    block = await self._download_resource(
                        message_id,
                        key,
                        "image" if tag == "img" else "file",
                        "image/png" if tag == "img" else "video/mp4",
                        tag,
                    )
                    if block is not None:
                        blocks.append(block)
            parts.append("\n")
        _flush()
        return blocks

    def _on_card_action(
        self,
        data: "P2CardActionTrigger",
        loop: asyncio.AbstractEventLoop,
    ) -> "P2CardActionTriggerResponse":
        """Emit the click as a decision event; ack with a toast.

        Args:
            data (`P2CardActionTrigger`):
                The card-action event carrying the button value.
            loop (`asyncio.AbstractEventLoop`):
                The app loop to bridge the emitted event onto.

        Returns:
            `P2CardActionTriggerResponse`: The synchronous toast ack.
        """
        action = getattr(getattr(data.event, "action", None), "value", None)
        parsed = _parse_action(action)
        if parsed is None:
            return _build_toast(False)
        tool_call_id, chat_id, approved, agent_id, session_id = parsed
        operator = getattr(data.event, "operator", None)
        user_id = getattr(operator, "open_id", "") or ""
        if self._emit:
            asyncio.run_coroutine_threadsafe(
                self._emit(
                    ChannelConfirmationResultEvent(
                        channel_id=self._channel_id,
                        chat_id=chat_id,
                        channel_user_id=user_id,
                        agent_id=agent_id,
                        session_id=session_id,
                        tool_call_id=tool_call_id,
                        approved=approved,
                    ),
                ),
                loop,
            )
        # Update the clicked card in place via the callback response —
        # reliable even while the approved run floods the card API.
        return _build_action_response(approved)

    # -- Outbound (gateway → platform) --

    async def send_response(
        self,
        event: ChannelEvent,
        events: AsyncIterator[dict],
    ) -> None:
        """Stream the reply into a live CardKit card, presenting an
        approval card when the run parks; fall back to one-shot text if
        the streaming card cannot be created.

        Args:
            event (`ChannelEvent`): The send target (chat id).
            events (`AsyncIterator[dict]`): The run's session events.
        """
        reply: Msg | None = None
        confirm: RequireUserConfirmEvent | None = None
        ref: str | None = None
        failed = False
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
            if failed or reply is None:
                continue
            rendered = self._render(
                reply,
                show_thinking=self._config.show_thinking,
                show_tool_process=self._config.show_tool_process,
            )
            text = "".join(
                b.text for b in rendered if isinstance(b, TextBlock)
            )
            if not text:
                continue
            if ref is None:
                ref = await self._card_open(event)
                if ref is None:
                    failed = True
                    continue
            now = time.monotonic()
            if now - last >= _STREAM_MIN_INTERVAL:
                last = now
                await self._card_push(ref, text)
        blocks = self._render(
            reply,
            show_thinking=self._config.show_thinking,
            show_tool_process=self._config.show_tool_process,
        )
        text = "".join(b.text for b in blocks if isinstance(b, TextBlock))
        await self._finish_card(event, ref, text)
        for block in blocks:
            if isinstance(block, DataBlock) and isinstance(
                block.source,
                Base64Source,
            ):
                data_bytes = base64.b64decode(block.source.data)
                media_type = block.source.media_type or ""
                if media_type.startswith("image/") and self.capabilities.image:
                    await self.send_image_to(
                        event.chat_id,
                        "chat_id",
                        data_bytes,
                    )
                elif self.capabilities.file:
                    await self.send_file_to(
                        event.chat_id,
                        "chat_id",
                        data_bytes,
                        block.name or "file",
                    )
        if confirm is not None:
            await self._present_confirm(event, confirm)

    async def _finish_card(
        self,
        event: ChannelEvent,
        ref: str | None,
        text: str,
    ) -> None:
        """Finalise the live card, or send ``text`` once if none was open.

        Args:
            event (`ChannelEvent`): The send target (chat id).
            ref (`str | None`): The open card id, or ``None``.
            text (`str`): The complete reply text.
        """
        if ref is not None:
            await self._card_push(ref, text)
            await self._close_stream(ref)
            self._stream_seq.pop(ref, None)
        elif text:
            for part in self._split_long_message(text):
                await self._send(
                    event.channel_message_id,
                    event.chat_id,
                    "text",
                    json.dumps({"text": part}),
                )

    # -- Streaming card (Feishu CardKit) --
    # NOTE: needs a real bot to verify end-to-end; on any API failure
    # ``_card_open`` returns None and ``send_response`` sends once instead.

    async def _card_open(self, event: ChannelEvent) -> str | None:
        """Create a streaming card and send it, returning its card id.

        Args:
            event (`ChannelEvent`): The send target (chat id).

        Returns:
            `str | None`: The card id, or ``None`` if streaming could not
            be started (the caller then sends once).
        """
        card_json = json.dumps(
            {
                "schema": "2.0",
                "config": {"streaming_mode": True},
                "body": {
                    "elements": [
                        {
                            "tag": "markdown",
                            "element_id": _STREAM_ELEMENT_ID,
                            "content": "",
                        },
                    ],
                },
            },
        )
        created = await self._api(
            "POST",
            f"{_API}/cardkit/v1/cards",
            {"type": "card_json", "data": card_json},
        )
        if not created or created.get("code") != 0:
            return None
        card_id = created.get("data", {}).get("card_id")
        if not card_id:
            return None
        sent = await self._send(
            event.channel_message_id,
            event.chat_id,
            "interactive",
            json.dumps({"type": "card", "data": {"card_id": card_id}}),
        )
        if not sent or sent.get("code") != 0:
            return None
        self._stream_seq[card_id] = 0
        return card_id

    async def _card_push(self, card_id: str, text: str) -> None:
        """Write the current text to the card with a rising sequence.

        Args:
            card_id (`str`): The streaming card id.
            text (`str`): Text to render.
        """
        seq = self._stream_seq.get(card_id, 0) + 1
        self._stream_seq[card_id] = seq
        await self._api(
            "PUT",
            f"{_API}/cardkit/v1/cards/{card_id}/elements/"
            f"{_STREAM_ELEMENT_ID}/content",
            {"content": text, "sequence": seq},
        )

    async def _close_stream(self, card_id: str) -> None:
        """End streaming mode so the card stops showing "generating…".

        Args:
            card_id (`str`): The streaming card to finalise.
        """
        seq = self._stream_seq.get(card_id, 0) + 1
        self._stream_seq[card_id] = seq
        await self._api(
            "PATCH",
            f"{_API}/cardkit/v1/cards/{card_id}/settings",
            {
                "settings": json.dumps({"config": {"streaming_mode": False}}),
                "sequence": seq,
            },
        )

    async def _present_confirm(
        self,
        event: ChannelEvent,
        req: RequireUserConfirmEvent,
    ) -> None:
        """Post one approval card per tool call; each button carries its
        ``tool_call_id`` and the ``chat_id`` for click routing.

        Args:
            event (`ChannelEvent`): The send target (chat id).
            req (`RequireUserConfirmEvent`): The approval request to show.
        """
        for tool in req.tool_calls:
            await self._send(
                event.channel_message_id,
                event.chat_id,
                "interactive",
                _build_approval_card(
                    tool.id,
                    event.chat_id,
                    tool.name,
                    str(tool.input)[:800],
                    event.metadata.get("agent_id", ""),
                    event.metadata.get("session_id", ""),
                ),
            )

    async def send_reaction(
        self,
        event: ChannelEvent,
        emoji_type: str,
    ) -> str | None:
        """Add an emoji reaction to the inbound message.

        Args:
            event (`ChannelEvent`): The message to react to.
            emoji_type (`str`): The Feishu emoji type (e.g. ``"OnIt"``).

        Returns:
            `str | None`: The reaction id for removal, or ``None``.
        """
        if not event.channel_message_id:
            return None
        data = await self._api(
            "POST",
            f"{_API}/im/v1/messages/{event.channel_message_id}/reactions",
            {"reaction_type": {"emoji_type": emoji_type}},
        )
        if data and data.get("code") == 0:
            return data.get("data", {}).get("reaction_id")
        return None

    async def remove_reaction(
        self,
        event: ChannelEvent,
        reaction_id: str,
    ) -> None:
        """Remove a reaction previously added by :meth:`send_reaction`.

        Args:
            event (`ChannelEvent`): The reacted-to message.
            reaction_id (`str`): The reaction id to remove.
        """
        if not event.channel_message_id:
            return
        await self._api(
            "DELETE",
            f"{_API}/im/v1/messages/{event.channel_message_id}"
            f"/reactions/{reaction_id}",
        )

    async def list_bot_chats(self) -> list[dict]:
        """List the chats the bot is in as ``{chat_id, name, chat_type}``."""
        results: list[dict] = []
        page_token = ""
        while True:
            url = f"{_API}/im/v1/chats?page_size=50"
            if page_token:
                url += f"&page_token={page_token}"
            data = await self._api("GET", url)
            if not data or data.get("code") != 0:
                break
            payload = data.get("data", {})
            for item in payload.get("items", []):
                results.append(
                    {
                        "chat_id": item.get("chat_id", ""),
                        "name": item.get("name", ""),
                        "chat_type": item.get("chat_type", ""),
                    },
                )
            if not payload.get("has_more"):
                break
            page_token = payload.get("page_token", "")
        return results

    async def list_tools(
        self,
        workspace: "WorkspaceBase",
    ) -> list["ToolBase"]:
        """Expose the Feishu send/discovery tools to the agent.

        Args:
            workspace (`WorkspaceBase`):
                The calling session's workspace; the send-file tools read
                their payload from its backend by absolute path.

        Returns:
            `list[ToolBase]`: The Feishu agent tools.
        """
        from ._tools import (
            ListChatMembers,
            ListChats,
            SendFile,
            SendImage,
            SendMessage,
        )

        backend = workspace.get_backend()
        return [
            ListChats(self, backend),
            ListChatMembers(self, backend),
            SendMessage(self, backend),
            SendFile(self, backend),
            SendImage(self, backend),
        ]

    # -- Agent-tool operations (act on chats/users other than the current) --

    async def send_message_to(
        self,
        receive_id: str,
        receive_id_type: str,
        text: str,
    ) -> dict | None:
        """Send a plain-text message to an arbitrary receive_id.

        Args:
            receive_id (`str`): The recipient id.
            receive_id_type (`str`): ``"chat_id"`` / ``"open_id"`` / etc.
            text (`str`): The message text.

        Returns:
            `dict | None`: The Feishu API response, or ``None`` on error.
        """
        return await self._api(
            "POST",
            f"{_API}/im/v1/messages?receive_id_type={receive_id_type}",
            {
                "receive_id": receive_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}),
            },
        )

    async def send_file_to(
        self,
        receive_id: str,
        receive_id_type: str,
        data: bytes,
        file_name: str,
    ) -> dict | None:
        """Upload a file (→ file_key) then send it to a receive_id.

        Args:
            receive_id (`str`): The recipient id.
            receive_id_type (`str`): ``"chat_id"`` / ``"open_id"`` / etc.
            data (`bytes`): The file bytes.
            file_name (`str`): The file's display name.

        Returns:
            `dict | None`: The send response, or ``None`` on error.
        """
        file_key = await self._upload(
            f"{_API}/im/v1/files",
            {"file_type": "stream", "file_name": file_name},
            {"file": (file_name, data)},
            "file_key",
        )
        if not file_key:
            return None
        return await self._api(
            "POST",
            f"{_API}/im/v1/messages?receive_id_type={receive_id_type}",
            {
                "receive_id": receive_id,
                "msg_type": "file",
                "content": json.dumps({"file_key": file_key}),
            },
        )

    async def send_image_to(
        self,
        receive_id: str,
        receive_id_type: str,
        data: bytes,
    ) -> dict | None:
        """Upload an image (→ image_key) then send it to a receive_id.

        Args:
            receive_id (`str`): The recipient id.
            receive_id_type (`str`): ``"chat_id"`` / ``"open_id"`` / etc.
            data (`bytes`): The image bytes.

        Returns:
            `dict | None`: The send response, or ``None`` on error.
        """
        image_key = await self._upload(
            f"{_API}/im/v1/images",
            {"image_type": "message"},
            {"image": ("image", data)},
            "image_key",
        )
        if not image_key:
            return None
        return await self._api(
            "POST",
            f"{_API}/im/v1/messages?receive_id_type={receive_id_type}",
            {
                "receive_id": receive_id,
                "msg_type": "image",
                "content": json.dumps({"image_key": image_key}),
            },
        )

    async def list_chat_members(self, chat_id: str) -> list[dict]:
        """List a group's members as ``{open_id, name}`` dicts.

        Args:
            chat_id (`str`): The group whose members to list.

        Returns:
            `list[dict]`: One ``{open_id, name}`` per member.
        """
        results: list[dict] = []
        page_token = ""
        while True:
            url = (
                f"{_API}/im/v1/chats/{chat_id}/members"
                f"?member_id_type=open_id&page_size=100"
            )
            if page_token:
                url += f"&page_token={page_token}"
            data = await self._api("GET", url)
            if not data or data.get("code") != 0:
                break
            payload = data.get("data", {})
            for item in payload.get("items", []):
                results.append(
                    {
                        "open_id": item.get("member_id", ""),
                        "name": item.get("name", ""),
                    },
                )
            if not payload.get("has_more"):
                break
            page_token = payload.get("page_token", "")
        return results

    # -- Feishu API helpers --

    async def _upload(
        self,
        url: str,
        data: dict,
        files: dict,
        key: str,
        *,
        _retried: bool = False,
    ) -> str | None:
        """Multipart upload (file/image), returning the resource key; the
        JSON ``_api`` helper can't do multipart, so this posts directly.

        Args:
            url (`str`): The upload endpoint.
            data (`dict`): The multipart form fields.
            files (`dict`): The multipart file part(s).
            key (`str`): The response data key to return (``file_key`` /
                ``image_key``).
            _retried (`bool`): Internal — set on the post-refresh retry.

        Returns:
            `str | None`: The resource key, or ``None`` on error.
        """
        if not await self._ensure_ready():
            return None
        try:
            resp = await self._http.post(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                data=data,
                files=files,
            )
            body = resp.json()
            if body.get("code") == 0:
                return body.get("data", {}).get(key)
            if not _retried and body.get("code") in _TOKEN_EXPIRED_CODES:
                await self._refresh_token()
                return await self._upload(
                    url,
                    data,
                    files,
                    key,
                    _retried=True,
                )
            logger.warning("Feishu upload failed: %s", body.get("msg"))
            return None
        except Exception:  # pylint: disable=broad-except
            logger.debug("Feishu upload request failed")
            return None

    async def aclose(self) -> None:
        """Close the HTTP client this instance opened lazily.

        The connection loop closes its own in ``start_listening``; this
        covers the client-only instances, which never run it.
        """
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self._token = None

    async def _ensure_ready(self) -> bool:
        """Make the instance able to call the platform, connected or not.

        Outbound is plain REST, so an instance built by
        :class:`~agentscope.app.channel.ChannelClients` — one that never
        ran ``start_listening`` — must reach the platform too. Both the
        HTTP client and the tenant token are therefore created on first
        use rather than during connection setup.

        Returns:
            `bool`: Whether an authenticated client is available.
        """
        if self._token is None:
            # Callers treat this as a readiness check and promise their
            # own ``None`` on failure, so a network or auth blip must
            # not escape and turn a read endpoint into a 500.
            try:
                await self._refresh_token()
            except Exception:  # pylint: disable=broad-except
                logger.warning(
                    "Feishu '%s' could not obtain a token",
                    self._channel_id,
                )
                return False
        return self._token is not None

    async def _refresh_token(self) -> None:
        """Fetch a fresh tenant access token and cache it."""
        if self._http is None:
            import httpx

            self._http = httpx.AsyncClient(timeout=30.0)
        resp = await self._http.post(
            f"{_API}/auth/v3/tenant_access_token/internal",
            json={"app_id": self._app_id, "app_secret": self._app_secret},
        )
        data = resp.json()
        if data.get("code") == 0:
            self._token = data.get("tenant_access_token")
        else:
            logger.error("Feishu token refresh failed: %s", data)

    async def _api(
        self,
        method: str,
        url: str,
        body: dict | None = None,
        *,
        _retried: bool = False,
    ) -> dict | None:
        """Authenticated JSON Feishu request; refreshes token once on expiry.

        Args:
            method (`str`): HTTP method.
            url (`str`): The full endpoint URL.
            body (`dict | None`): The JSON body, if any.
            _retried (`bool`): Internal — set on the post-refresh retry.

        Returns:
            `dict | None`: The parsed response, or ``None`` on error.
        """
        if not await self._ensure_ready():
            return None
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        try:
            resp = await self._http.request(
                method,
                url,
                headers=headers,
                json=body,
            )
            data = resp.json()
            if data.get("code") == 0:
                return data
            if not _retried and data.get("code") in _TOKEN_EXPIRED_CODES:
                await self._refresh_token()
                return await self._api(method, url, body, _retried=True)
            logger.warning("Feishu API %s failed: %s", method, data.get("msg"))
            return data
        except Exception:  # pylint: disable=broad-except
            logger.debug("Feishu API %s request failed", method)
            return None

    async def _send(
        self,
        reply_to: str | None,
        chat_id: str,
        msg_type: str,
        content: str,
    ) -> dict | None:
        """Send a message — as a reply when possible, else to the chat.

        Args:
            reply_to (`str | None`):
                Inbound message id to reply to; when falsy the message is
                sent to ``chat_id`` instead.
            chat_id (`str`):
                Target chat id (used when not replying).
            msg_type (`str`):
                Feishu message type (``"text"`` / ``"interactive"`` / …).
            content (`str`):
                The already-serialised message content JSON.

        Returns:
            `dict | None`: The Feishu API response, or ``None`` on error.
        """
        if reply_to:
            return await self._api(
                "POST",
                f"{_API}/im/v1/messages/{reply_to}/reply",
                {"msg_type": msg_type, "content": content},
            )
        return await self._api(
            "POST",
            f"{_API}/im/v1/messages?receive_id_type=chat_id",
            {"receive_id": chat_id, "msg_type": msg_type, "content": content},
        )

    async def _download_media(
        self,
        message: "EventMessage",
        msg_type: str,
    ) -> DataBlock | None:
        """Download a media resource into a base64 ``DataBlock``.

        Args:
            message (`EventMessage`):
                The inbound message carrying the resource key.
            msg_type (`str`):
                The Feishu message type (``image`` / ``file`` / ``audio``
                / ``media``), selecting the resource endpoint + mime.

        Returns:
            `DataBlock | None`: The downloaded block, or ``None`` on error.
        """
        content = json.loads(getattr(message, "content", None) or "{}")
        key = content.get("image_key") or content.get("file_key") or ""
        if not key:
            return None
        default_mime = {
            "image": "image/png",
            "audio": "audio/ogg",
            "media": "video/mp4",
        }.get(msg_type, "application/octet-stream")
        return await self._download_resource(
            message.message_id,
            key,
            "image" if msg_type == "image" else "file",
            default_mime,
            content.get("file_name") or msg_type,
        )

    async def _download_resource(
        self,
        message_id: str,
        key: str,
        resource_type: str,
        default_mime: str,
        name: str,
    ) -> DataBlock | None:
        """Download one message resource (image/file) into a base64 block.

        Args:
            message_id (`str`): The message the resource belongs to.
            key (`str`): The resource key (``image_key`` / ``file_key``).
            resource_type (`str`): ``"image"`` or ``"file"`` endpoint type.
            default_mime (`str`): Fallback media type.
            name (`str`): Display name for the block.

        Returns:
            `DataBlock | None`: The block, or ``None`` on error.
        """
        if not await self._ensure_ready():
            return None
        url = (
            f"{_API}/im/v1/messages/{message_id}"
            f"/resources/{key}?type={resource_type}"
        )
        try:
            resp = await self._http.get(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
            )
            if resp.status_code != 200:
                return None
            return DataBlock(
                source=Base64Source(
                    data=base64.b64encode(resp.content).decode("ascii"),
                    media_type=resp.headers.get("content-type", default_mime),
                ),
                name=name,
            )
        except Exception:  # pylint: disable=broad-except
            logger.debug("Feishu resource download failed")
            return None
