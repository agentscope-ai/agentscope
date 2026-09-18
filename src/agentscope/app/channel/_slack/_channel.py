# -*- coding: utf-8 -*-
"""Slack channel — Socket Mode.

The app holds a WebSocket to Slack rather than exposing a request URL, so
this channel needs no public endpoint and runs on the app event loop like
the Discord one, with no thread bridging.

Socket Mode wants each envelope acknowledged within three seconds, but
that ack is separate from the user-visible reply, so a run is free to
take as long as it takes: the channel posts one message on the first
output and edits it in place with ``chat.update`` as more arrives.

Approval cards carry their lookup keys in the button ``value`` (Slack
allows 2000 characters there), so a click resolves without any in-process
state and this channel stays correct when several nodes each hold a
Socket Mode connection for the same app.
"""
import asyncio
import base64
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
    _build_approval_blocks,
    _parse_action,
    _resolved_blocks,
)

if TYPE_CHECKING:
    from slack_sdk.socket_mode.aiohttp import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from .....tool import ToolBase
    from .....workspace import WorkspaceBase

# Slack truncates well before this, and splitting keeps edits cheap.
_MAX_LEN = 4000
# chat.update is rate limited at roughly one call per second per channel.
_STREAM_MIN_INTERVAL = 1.0
# Park in 'failed' when the socket never comes up within this long: the
# SDK's connect() retries forever internally and reports no attempt count,
# so elapsed time is the only signal it gives us.
_MAX_CONNECT_SECS = 60.0
_POLL_INTERVAL = 5.0
# How often a given-up channel wakes to notice it has been stopped.
_PARK_INTERVAL = 30.0
# How many times to wait out a 429 before giving the call up.
_RATE_LIMIT_RETRIES = 2

# Outcomes of the start-up identity lookup.
_IDENTITY_OK = "ok"
_IDENTITY_REFUSED = "refused"
_IDENTITY_UNAVAILABLE = "unavailable"

# Slack error codes that no amount of retrying will fix: the stored
# credentials themselves have to change. Every other API error, from
# 'ratelimited' to 'service_unavailable', is transient by comparison.
_TERMINAL_AUTH_ERRORS = frozenset(
    {
        "account_inactive",
        "invalid_auth",
        "not_authed",
        "token_expired",
        "token_revoked",
    },
)

# Message subtypes that are still a person talking. Anything else (joins,
# edits, deletions, bot posts) is not input for the agent.
_USER_SUBTYPES = frozenset({"file_share", "thread_broadcast"})


class SlackChannel(ChannelBase):
    """Slack platform channel (Socket Mode)."""

    channel_type = "slack"
    display_name = "Slack"
    description = "Workspace bot for channels, groups and direct messages."
    icon_url = "https://www.google.com/s2/favicons?domain=slack.com&sz=128"
    platform_bot_id_field = "app_id"

    class Credentials(BaseModel):
        """Slack app credentials, from the app's admin pages."""

        app_id: str = Field(
            title="App ID",
            description="Slack App ID (Basic Information), e.g. A0123ABCDEF",
        )
        bot_token: str = Field(
            title="Bot Token",
            description="Bot User OAuth Token, starts with xoxb-",
            json_schema_extra={"format": "password"},
        )
        app_token: str = Field(
            title="App-Level Token",
            description="App-level token with connections:write, starts "
            "with xapp-",
            json_schema_extra={"format": "password"},
        )

    class Config(BaseModel):
        """Slack platform options."""

        only_at_reply: bool = Field(
            default=True,
            title="Reply only when mentioned",
            description="In channels, reply only when the bot is @mentioned",
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
        max_message_length=_MAX_LEN,
    )

    def __init__(
        self,
        channel_id: str,
        credentials: "SlackChannel.Credentials",
        config: "SlackChannel.Config",
    ) -> None:
        """Read the credentials and options from the validated models.

        Args:
            channel_id (`str`):
                This channel instance's unique id.
            credentials (`SlackChannel.Credentials`):
                Validated app id and tokens.
            config (`SlackChannel.Config`):
                Validated platform options.
        """
        self._channel_id = channel_id
        self._app_id = credentials.app_id
        self._bot_token = credentials.bot_token
        self._app_token = credentials.app_token
        self._config = config
        self.status = ChannelStatus()
        self._client: Any = None
        self._web: Any = None
        self._stopped = False
        self._bot_user_id: str = ""
        self._chat_name_cache: dict[str, str] = {}
        self._chat_kind_cache: dict[str, ChatKind] = {}
        self._user_name_cache: dict[str, str] = {}

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
        """Open the Socket Mode connection, supervise it, and close
        everything on exit.

        Args:
            emit (`Callable`): Gateway callback for inbound events.
        """
        try:
            from slack_sdk.socket_mode.aiohttp import SocketModeClient
        except ImportError as e:
            raise ImportError(
                "Slack channel requires 'slack_sdk' "
                "(pip install slack_sdk).",
            ) from e

        self._emit = emit
        self.status.state = "connecting"
        # Everything below is optional until it is built, so the finalizer
        # can clean up whatever a partial start-up managed to create.
        client: Any = None
        connect_task: asyncio.Task | None = None
        try:
            self._web_client()
            # Identify ourselves up front: needed to drop our own messages
            # and to recognise an @mention. A bad bot token fails here,
            # which is a cleaner signal than the socket's silent retry loop.
            identity = await self._load_identity()
            if identity == _IDENTITY_REFUSED:
                self.status.state = "failed"
                await self._park()
                return
            if identity == _IDENTITY_UNAVAILABLE:
                # Slack was never reached, so this says nothing about the
                # token. Ending the listener lets the dispatcher's next
                # reconcile start a fresh one, rather than parking the
                # channel until somebody edits it.
                logger.warning(
                    "Slack '%s' start-up deferred, will retry: %s",
                    self._channel_id,
                    self.status.last_error,
                )
                return

            client = SocketModeClient(
                app_token=self._app_token,
                web_client=self._web,
            )
            client.socket_mode_request_listeners.append(self._on_request)
            self._client = client
            # connect() retries forever internally, so it would block here
            # for as long as Slack stays unreachable — run it alongside the
            # supervising loop rather than awaiting it.
            connect_task = asyncio.create_task(
                client.connect(),
                name=f"slack-connect:{self._channel_id}",
            )
            started = time.monotonic()
            ever_connected = False
            while not self._stopped:
                await asyncio.sleep(_POLL_INTERVAL)
                if await client.is_connected():
                    ever_connected = True
                    self.status.state = "connected"
                    self.status.last_error = ""
                    continue
                if ever_connected:
                    self.status.state = "retrying"
                    continue
                # Never came up: almost certainly a bad app-level token or
                # Socket Mode not enabled. Park so the dispatcher leaves us
                # alone until the channel is edited.
                if time.monotonic() - started >= _MAX_CONNECT_SECS:
                    # Tear the socket down before announcing the failure.
                    # connect() retries forever and _park() does not return
                    # until the channel is stopped, so leaving it running
                    # would keep dialling; doing this first also means the
                    # status is never observably 'failed' while the socket
                    # is still live and able to start handling events.
                    await self._shutdown_socket(connect_task, client)
                    connect_task, client = None, None
                    self._client = None
                    self.status.state = "failed"
                    self.status.last_error = (
                        "socket did not connect; check the app-level token "
                        "and that Socket Mode is enabled"
                    )
                    logger.error(
                        "Slack '%s' giving up: %s",
                        self._channel_id,
                        self.status.last_error,
                    )
                    await self._park()
                    break
                self.status.state = "connecting"
        finally:
            self._stopped = True
            self.status.state = "stopped"
            await self._shutdown_socket(connect_task, client)
            self._client = None
            self._web = None

    async def _shutdown_socket(
        self,
        connect_task: "asyncio.Task | None",
        client: Any,
    ) -> None:
        """Stop the connect retry loop and close the socket.

        Safe to call twice: the give-up path uses it before parking and
        then hands the finalizer ``None``.

        Args:
            connect_task (`asyncio.Task | None`): The running connect
                task, if one was started.
            client (`Any`): The Socket Mode client, if one was built.
        """
        if connect_task is not None:
            connect_task.cancel()
            await asyncio.gather(connect_task, return_exceptions=True)
        if client is not None:
            try:
                await client.close()
            except Exception:  # pylint: disable=broad-except
                logger.debug("Slack '%s' close failed", self._channel_id)

    async def _park(self) -> None:
        """Sleep until stopped, holding the listener task open.

        The dispatcher restarts any channel whose task has finished, so a
        channel that has given up stays parked in ``failed`` instead of
        returning; editing the channel is what retries it.
        """
        while not self._stopped:
            await asyncio.sleep(_PARK_INTERVAL)

    def _web_client(self) -> Any:
        """The Web API client, built on first use.

        Outbound is plain REST, so an instance built by
        :class:`~agentscope.app.channel.ChannelClients` — one that never
        runs ``start_listening`` — has to reach Slack too. The client is
        therefore created on demand rather than during connection setup.

        Returns:
            `Any`: The ``AsyncWebClient``, or ``None`` when slack_sdk is
            not installed, which every caller already treats as failure.
        """
        if self._web is not None:
            return self._web
        try:
            from slack_sdk.http_retry.builtin_async_handlers import (
                AsyncRateLimitErrorRetryHandler,
                async_default_handlers,
            )
            from slack_sdk.web.async_client import AsyncWebClient
        except ImportError:
            logger.error(
                "Slack '%s' needs slack_sdk (pip install slack_sdk)",
                self._channel_id,
            )
            return None
        self._web = AsyncWebClient(
            token=self._bot_token,
            # Passing this list replaces the client's defaults, so keep
            # them: they carry the connection-error retries that ride out
            # a transient blip. The added handler waits out a 429 per its
            # Retry-After rather than dropping the call.
            retry_handlers=[
                *async_default_handlers(),
                AsyncRateLimitErrorRetryHandler(
                    max_retry_count=_RATE_LIMIT_RETRIES,
                ),
            ],
        )
        return self._web

    async def aclose(self) -> None:
        """Drop the Web API client this instance built lazily.

        slack_sdk opens and closes a session per request when it was not
        handed one, so there is no connection to shut down here; letting
        the client go is all a retired instance needs.
        """
        self._web = None

    async def _load_identity(self) -> str:
        """Resolve and cache the bot's own user id via ``auth.test``.

        Distinguishes a refusal from an unreachable Slack: the first is a
        verdict on the credentials and worth parking on, the second is a
        transient failure that retrying can fix.

        Returns:
            `str`: ``_IDENTITY_OK`` when the identity is known,
            ``_IDENTITY_REFUSED`` when Slack answered and rejected the
            token, ``_IDENTITY_UNAVAILABLE`` when the call never got an
            answer.
        """
        from slack_sdk.errors import SlackApiError

        web = self._web_client()
        if web is None:
            self.status.last_error = "slack_sdk is not installed"
            return _IDENTITY_REFUSED
        try:
            auth = await web.auth_test()
        except SlackApiError as e:
            code = self._error_code(e)
            if code not in _TERMINAL_AUTH_ERRORS:
                # Slack answered, but with something a retry can clear:
                # rate limiting, an internal error, a timeout. Parking on
                # that would brick the channel over a passing outage.
                self.status.last_error = (
                    f"auth.test failed with '{code or 'an unknown error'}'"
                )
                return _IDENTITY_UNAVAILABLE
            self.status.last_error = f"auth.test rejected the token: {code}"
            logger.error(
                "Slack '%s' bot token rejected: %s",
                self._channel_id,
                code,
            )
            return _IDENTITY_REFUSED
        except Exception as e:  # pylint: disable=broad-except
            self.status.last_error = f"auth.test could not reach Slack: {e}"
            return _IDENTITY_UNAVAILABLE
        self._bot_user_id = auth.get("user_id") or ""
        if not self._bot_user_id:
            self.status.last_error = "auth.test returned no user id"
            logger.error(
                "Slack '%s' identity lookup returned no user id",
                self._channel_id,
            )
            return _IDENTITY_REFUSED
        return _IDENTITY_OK

    @staticmethod
    def _error_code(error: Exception) -> str:
        """Read Slack's ``error`` code off a failed Web API response.

        Args:
            error (`Exception`): The ``SlackApiError`` to read.

        Returns:
            `str`: The code, or ``""`` when the response carried none —
            which callers treat as transient, since parking a channel on
            an error we could not even identify is the costlier mistake.
        """
        response = getattr(error, "response", None)
        if response is None:
            return ""
        try:
            return str(response.get("error") or "")
        except Exception:  # pylint: disable=broad-except
            return ""

    # -- Inbound --

    async def _on_request(
        self,
        client: "SocketModeClient",
        req: "SocketModeRequest",
    ) -> None:
        """Acknowledge one Socket Mode envelope, then act on it.

        The ack must go out within three seconds and is unrelated to the
        agent's reply, so it happens first and unconditionally.

        Args:
            client (`SocketModeClient`): The connection the request came in
                on, used to send the acknowledgement.
            req (`SocketModeRequest`): The envelope.
        """
        from slack_sdk.socket_mode.response import SocketModeResponse

        try:
            await client.send_socket_mode_response(
                SocketModeResponse(envelope_id=req.envelope_id),
            )
        except Exception:  # pylint: disable=broad-except
            # Slack redelivers an unacknowledged envelope, so this is worth
            # seeing. Carry on regardless: dropping the event here would
            # turn a possible duplicate into a certain loss.
            logger.warning(
                "Slack '%s' failed to ack envelope %s",
                self._channel_id,
                req.envelope_id,
            )
        try:
            if req.type == "events_api":
                await self._on_event(req)
            elif req.type == "interactive":
                await self._on_interaction(req.payload or {})
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "Slack '%s' request handling failed",
                self._channel_id,
            )

    async def _on_event(self, req: "SocketModeRequest") -> None:
        """Normalise an Events API message and emit it.

        Args:
            req (`SocketModeRequest`): The envelope carrying the event.
        """
        payload = req.payload or {}
        event = payload.get("event") or {}
        if event.get("type") != "message":
            return
        channel_event = await self._normalize(
            event,
            delivery={
                # Slack repeats event_id across retries of the same event,
                # so this is what a dedup would key on. Kept on the event
                # rather than acted on here: doing it properly needs shared
                # state across nodes, which is a framework-level concern.
                "event_id": payload.get("event_id", ""),
                "envelope_id": req.envelope_id or "",
                "retry_attempt": req.retry_attempt or 0,
                "retry_reason": req.retry_reason or "",
            },
        )
        if channel_event is not None and self._emit:
            await self._emit(channel_event)

    async def _normalize(
        self,
        event: dict,
        delivery: dict | None = None,
    ) -> ChannelEvent | None:
        """Convert an inbound Slack message into a ``ChannelEvent``,
        downloading files and honouring ``only_at_reply`` in channels.

        Args:
            event (`dict`): The Slack ``message`` event.
            delivery (`dict | None`): Envelope identifiers (``event_id``,
                ``envelope_id``, retry counters) to keep on the event, so a
                redelivery stays identifiable downstream.

        Returns:
            `ChannelEvent | None`: The normalised event, or ``None`` when
            there is nothing to act on.
        """
        subtype = event.get("subtype")
        if subtype is not None and subtype not in _USER_SUBTYPES:
            return None
        # Our own posts come back over the socket; never answer ourselves.
        if event.get("bot_id") or event.get("user") == self._bot_user_id:
            return None

        chat_id = event.get("channel") or ""
        chat_type = event.get("channel_type") or ""
        user_id = event.get("user") or ""
        text = (event.get("text") or "").strip()

        if self._gated_out(text, chat_type):
            return None
        text = self._strip_mention(text)

        content: list[TextBlock | DataBlock] = []
        for spec in event.get("files") or []:
            block = await self._download(spec)
            if block is not None:
                content.append(block)
        if text:
            content.append(TextBlock(text=text))
        if not content:
            return None

        if chat_id:
            self._chat_kind_cache[chat_id] = (
                ChatKind.PRIVATE if chat_type == "im" else ChatKind.GROUP
            )
        return ChannelEvent(
            channel_id=self._channel_id,
            channel_user_id=user_id,
            channel_user_name=await self._user_name(user_id),
            chat_id=chat_id,
            chat_name=(
                await self.chat_name(chat_id) if chat_type != "im" else ""
            ),
            channel_message_id=event.get("ts") or "",
            content=content,
            metadata={"chat_type": chat_type, **(delivery or {})},
        )

    def _gated_out(self, text: str, chat_type: str) -> bool:
        """Whether a channel message is dropped by ``only_at_reply``.

        Direct messages are never gated — there is nobody else to address.

        Args:
            text (`str`): The message text, still carrying mention markup.
            chat_type (`str`): ``channel`` / ``group`` / ``im`` / ``mpim``.

        Returns:
            `bool`: ``True`` to ignore the message.
        """
        if chat_type == "im" or not self._config.only_at_reply:
            return False
        if not self._bot_user_id:
            # Identity unknown: we cannot tell whether the @ was for us, so
            # fail closed rather than answer every message in the channel.
            logger.warning(
                "Slack '%s' bot id unknown; dropping unverified message",
                self._channel_id,
            )
            return True
        return f"<@{self._bot_user_id}>" not in text

    def _strip_mention(self, text: str) -> str:
        """Remove the bot's own @mention markup from a message.

        Args:
            text (`str`): The raw message text.
        """
        if not self._bot_user_id:
            return text
        return text.replace(f"<@{self._bot_user_id}>", "").strip()

    async def _download(self, spec: dict) -> DataBlock | None:
        """Download one shared file into a base64 ``DataBlock``.

        Slack's private file URLs need the bot token as a bearer, so this
        goes through httpx rather than the SDK.

        Args:
            spec (`dict`): The file entry from the message event.

        Returns:
            `DataBlock | None`: The block, or ``None`` on error.
        """
        import httpx

        url = spec.get("url_private_download") or spec.get("url_private")
        if not url:
            return None
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                resp = await http.get(
                    url,
                    headers={"Authorization": f"Bearer {self._bot_token}"},
                )
            if resp.status_code != 200:
                logger.warning(
                    "Slack file download returned %d",
                    resp.status_code,
                )
                return None
        except Exception:  # pylint: disable=broad-except
            logger.debug("Slack file download failed")
            return None
        fallback = resp.headers.get(
            "content-type",
            "application/octet-stream",
        )
        return DataBlock(
            source=Base64Source(
                data=base64.b64encode(resp.content).decode("ascii"),
                media_type=spec.get("mimetype") or fallback,
            ),
            name=spec.get("name") or "file",
        )

    async def _on_interaction(self, payload: dict) -> None:
        """Emit an approval-card click as a decision and freeze the card.

        Args:
            payload (`dict`): The ``interactive`` envelope payload.
        """
        if payload.get("type") != "block_actions":
            return
        actions = payload.get("actions") or []
        parsed = next(
            (
                p
                for p in (_parse_action(a.get("value")) for a in actions)
                if p is not None
            ),
            None,
        )
        if parsed is None:
            return
        tool_call_id, chat_id, approved, agent_id, session_id = parsed
        clicker = (payload.get("user") or {}).get("id", "")
        if self._emit:
            await self._emit(
                ChannelConfirmationResultEvent(
                    channel_id=self._channel_id,
                    chat_id=chat_id,
                    channel_user_id=clicker,
                    agent_id=agent_id,
                    session_id=session_id,
                    tool_call_id=tool_call_id,
                    approved=approved,
                    actor=clicker,
                ),
            )
        message = payload.get("message") or {}
        ts = message.get("ts") or ""
        target = (payload.get("channel") or {}).get("id") or chat_id
        if ts and target:
            await self._update(
                target,
                ts,
                "Allowed" if approved else "Denied",
                blocks=_resolved_blocks(approved),
            )

    # -- Chat metadata --

    async def chat_name(self, chat_id: str) -> str:
        """The conversation's name (cached); ``""`` when unavailable.

        Args:
            chat_id (`str`): The conversation to look up.
        """
        if not chat_id:
            return ""
        cached = self._chat_name_cache.get(chat_id)
        if cached:
            return cached
        info = await self._conversation(chat_id)
        name = (info or {}).get("name") or ""
        if name:
            self._chat_name_cache[chat_id] = name
        return name

    async def chat_kind(self, chat_id: str) -> ChatKind | None:
        """Group vs 1:1 — from the inbound cache, else ``conversations.info``.

        Args:
            chat_id (`str`): The conversation to classify.
        """
        if not chat_id:
            return None
        cached = self._chat_kind_cache.get(chat_id)
        if cached is not None:
            return cached
        info = await self._conversation(chat_id)
        if info is None:
            return None
        kind = ChatKind.PRIVATE if info.get("is_im") else ChatKind.GROUP
        self._chat_kind_cache[chat_id] = kind
        return kind

    async def _conversation(self, chat_id: str) -> dict | None:
        """Fetch ``conversations.info`` for a chat.

        Args:
            chat_id (`str`): The conversation to look up.
        """
        web = self._web_client()
        if web is None:
            return None
        try:
            resp = await web.conversations_info(channel=chat_id)
            return resp.get("channel") or {}
        except Exception:  # pylint: disable=broad-except
            logger.debug("Slack conversations.info failed for %s", chat_id)
            return None

    async def _user_name(self, user_id: str) -> str:
        """The sender's display name (cached); empty on failure.

        Args:
            user_id (`str`): The Slack user id.
        """
        if not user_id:
            return ""
        cached = self._user_name_cache.get(user_id)
        if cached:
            return cached
        web = self._web_client()
        if web is None:
            return ""
        try:
            resp = await web.users_info(user=user_id)
            user = resp.get("user") or {}
            name = (
                (user.get("profile") or {}).get("display_name")
                or user.get("real_name")
                or user.get("name")
                or ""
            )
        except Exception:  # pylint: disable=broad-except
            logger.debug("Slack users.info failed for %s", user_id)
            return ""
        if name:
            self._user_name_cache[user_id] = name
        return name

    async def list_bot_chats(self) -> list[dict]:
        """List the conversations the bot is in as ``{chat_id, name,
        chat_type}``."""
        web = self._web_client()
        if web is None:
            return []
        results: list[dict] = []
        cursor = ""
        while True:
            try:
                resp = await web.conversations_list(
                    types="public_channel,private_channel,mpim,im",
                    exclude_archived=True,
                    limit=200,
                    cursor=cursor or None,
                )
            except Exception:  # pylint: disable=broad-except
                logger.debug("Slack conversations.list failed")
                break
            for item in resp.get("channels") or []:
                results.append(
                    {
                        "chat_id": item.get("id", ""),
                        "name": item.get("name", "")
                        or item.get("user", "")
                        or "",
                        "chat_type": "im" if item.get("is_im") else "channel",
                    },
                )
            meta = resp.get("response_metadata") or {}
            cursor = meta.get("next_cursor") or ""
            if not cursor:
                break
        return results

    async def list_chat_members(self, chat_id: str) -> list[dict]:
        """List a conversation's members as ``{user_id, name}`` dicts.

        Args:
            chat_id (`str`): The conversation whose members to list.

        Returns:
            `list[dict]`: One ``{user_id, name}`` per member.
        """
        web = self._web_client()
        if web is None:
            return []
        results: list[dict] = []
        cursor = ""
        while True:
            try:
                resp = await web.conversations_members(
                    channel=chat_id,
                    limit=200,
                    cursor=cursor or None,
                )
            except Exception:  # pylint: disable=broad-except
                logger.debug("Slack conversations.members failed")
                break
            for user_id in resp.get("members") or []:
                results.append(
                    {
                        "user_id": user_id,
                        "name": await self._user_name(user_id),
                    },
                )
            meta = resp.get("response_metadata") or {}
            cursor = meta.get("next_cursor") or ""
            if not cursor:
                break
        return results

    # -- Outbound --

    async def send_response(
        self,
        event: ChannelEvent,
        events: AsyncIterator[dict],
    ) -> None:
        """Post one message on the first output and edit it in place as
        the run produces more, then deliver attachments and any approval
        cards.

        Args:
            event (`ChannelEvent`): The send target (channel id).
            events (`AsyncIterator[dict]`): The run's session events.
        """
        reply: Msg | None = None
        confirm: RequireUserConfirmEvent | None = None
        ts: str | None = None
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
            if reply is None:
                continue
            text = self._text_of(reply)
            if not text:
                continue
            now = time.monotonic()
            if now - last < _STREAM_MIN_INTERVAL:
                continue
            last = now
            if ts is None:
                ts = await self._post(event.chat_id, text[:_MAX_LEN])
            else:
                await self._update(event.chat_id, ts, text[:_MAX_LEN])

        blocks = self._render(
            reply,
            show_thinking=self._config.show_thinking,
            show_tool_process=self._config.show_tool_process,
        )
        text = "".join(b.text for b in blocks if isinstance(b, TextBlock))
        await self._finish(event.chat_id, ts, text)
        for block in blocks:
            if isinstance(block, DataBlock):
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

    async def _send_segments(
        self,
        chat_id: str,
        parts: list[str],
        *,
        pace_first: bool = False,
    ) -> dict:
        """Post ``parts`` in order, pacing and checking each one.

        Slack allows roughly one write per second per conversation, and a
        rejected segment must stop the rest rather than leaving a reply
        with a hole in it.

        Args:
            chat_id (`str`): The conversation to post to.
            parts (`list[str]`): The message segments, in order.
            pace_first (`bool`): Wait before the first segment too, when
                the caller has just written to this conversation.

        Returns:
            `dict`: ``{"ok": bool, "sent_ts": [...], "segments": int}``,
            plus ``"error"`` and ``"failed_segment"`` on a partial send.
        """
        sent: list[str] = []
        for index, part in enumerate(parts):
            if index or pace_first:
                await asyncio.sleep(_STREAM_MIN_INTERVAL)
            ts = await self._post(chat_id, part)
            if ts is None:
                return {
                    "ok": False,
                    "error": self.status.last_error
                    or "the platform rejected the request",
                    "sent_ts": sent,
                    "failed_segment": index,
                    "segments": len(parts),
                }
            sent.append(ts)
        return {"ok": True, "sent_ts": sent, "segments": len(parts)}

    async def _finish(
        self,
        chat_id: str,
        ts: str | None,
        text: str,
    ) -> None:
        """Write the complete reply, splitting anything over the limit.

        The first segment replaces the streamed message when that edit
        lands; when it does not, the streamed message is left stale and
        the whole reply goes out as new messages instead, so no segment is
        dropped on the strength of an edit that never happened.

        Args:
            chat_id (`str`): The conversation to write to.
            ts (`str | None`): The streamed message's timestamp, if any.
            text (`str`): The complete reply text.
        """
        parts = [p for p in self._split_long_message(text) if p]
        if not parts:
            return
        edited = False
        if ts is not None:
            edited = await self._update(chat_id, ts, parts[0])
            if edited:
                parts = parts[1:]
            else:
                logger.warning(
                    "Slack '%s' could not finalise the streamed message; "
                    "posting the reply instead",
                    chat_id,
                )
        if not parts:
            return
        result = await self._send_segments(chat_id, parts, pace_first=edited)
        if not result["ok"]:
            logger.warning(
                "Slack '%s' delivered %d of %d reply segments: %s",
                chat_id,
                len(result["sent_ts"]),
                result["segments"],
                result["error"],
            )

    async def _send_data_block(self, chat_id: str, block: DataBlock) -> None:
        """Upload a reply attachment to ``chat_id``.

        Args:
            chat_id (`str`): The conversation to upload into.
            block (`DataBlock`): The attachment to deliver.
        """
        if not isinstance(block.source, Base64Source):
            return
        await self.upload_file(
            chat_id,
            base64.b64decode(block.source.data),
            block.name or "file",
        )

    async def _present_confirm(
        self,
        event: ChannelEvent,
        req: RequireUserConfirmEvent,
    ) -> None:
        """Post one approval card per tool call, each carrying its own
        lookup keys in the button values.

        Args:
            event (`ChannelEvent`): The send target (channel id).
            req (`RequireUserConfirmEvent`): The approval request to show.
        """
        for tool in req.tool_calls:
            await self._post(
                event.chat_id,
                f"Tool execution needs approval: {tool.name}",
                blocks=_build_approval_blocks(
                    tool.id,
                    event.chat_id,
                    tool.name,
                    tool.input,
                    event.metadata.get("agent_id", ""),
                    event.metadata.get("session_id", ""),
                ),
            )

    # -- Agent-callable tools --

    async def list_tools(
        self,
        workspace: "WorkspaceBase",
    ) -> list["ToolBase"]:
        """Expose the Slack send/discovery tools to the agent.

        Args:
            workspace (`WorkspaceBase`):
                The calling session's workspace; the file tools read their
                payload from its backend by absolute path.

        Returns:
            `list[ToolBase]`: The Slack agent tools.
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

    async def send_message_to(self, chat_id: str, text: str) -> dict:
        """Send a message to any conversation or user.

        Long text is split the same way the reply path splits it, rather
        than truncated, and every posted segment's timestamp is reported
        so a partial delivery is visible to the caller.

        Args:
            chat_id (`str`): A conversation id, or a user id for a DM.
            text (`str`): The message text.

        Returns:
            `dict`: ``{"ok": bool, "sent_ts": [...], "segments": int}``,
            plus ``"error"`` and ``"failed_segment"`` when a segment did
            not go out.
        """
        parts = [p for p in self._split_long_message(text) if p]
        if not parts:
            return {
                "ok": False,
                "error": "there was no text to send",
                "sent_ts": [],
                "segments": 0,
            }
        return await self._send_segments(chat_id, parts)

    async def upload_file(
        self,
        chat_id: str,
        data: bytes,
        file_name: str,
    ) -> dict:
        """Upload a file into a conversation.

        Slack renders images inline from the same upload, so this backs
        both the file and the image tool.

        Args:
            chat_id (`str`): The conversation to upload into.
            data (`bytes`): The file bytes.
            file_name (`str`): The file's display name.

        Returns:
            `dict`: ``{"ok": bool}``, with ``"error"`` when it failed.
        """
        web = self._web_client()
        if web is None:
            return {"ok": False, "error": "slack_sdk is not installed"}
        try:
            await web.files_upload_v2(
                channel=chat_id,
                file=data,
                filename=file_name,
                title=file_name,
            )
            return {"ok": True}
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Slack upload to '%s' failed: %s", chat_id, e)
            return {"ok": False, "error": str(e)}

    # -- Slack API helpers --

    async def _post(
        self,
        chat_id: str,
        text: str,
        blocks: list[dict] | None = None,
    ) -> str | None:
        """Post a message, returning its timestamp.

        Args:
            chat_id (`str`): The conversation to post to.
            text (`str`): Message text, also the notification fallback.
            blocks (`list[dict] | None`): Block Kit blocks, if any.

        Returns:
            `str | None`: The message ``ts``, or ``None`` on error.
        """
        web = self._web_client()
        if not chat_id or web is None:
            return None
        try:
            # Only pass ``blocks`` when there are some: these methods send
            # their kwargs as a JSON body, and slack_sdk does not drop the
            # None values from it the way it does for form params.
            extra = {"blocks": blocks} if blocks else {}
            resp = await web.chat_postMessage(
                channel=chat_id,
                text=text,
                **extra,
            )
            return resp.get("ts")
        except Exception as e:  # pylint: disable=broad-except
            self.status.last_error = str(e)
            logger.warning("Slack post to '%s' failed: %s", chat_id, e)
            return None

    async def _update(
        self,
        chat_id: str,
        ts: str,
        text: str,
        blocks: list[dict] | None = None,
    ) -> bool:
        """Edit an already-posted message in place.

        Args:
            chat_id (`str`): The conversation the message is in.
            ts (`str`): The message timestamp to edit.
            text (`str`): The replacement text.
            blocks (`list[dict] | None`): Replacement blocks, if any.

        Returns:
            `bool`: Whether the edit landed. Callers mid-stream can ignore
            this, but the final write has to know: a failed edit leaves a
            stale message that something else must replace.
        """
        web = self._web_client()
        if web is None:
            return False
        try:
            extra = {"blocks": blocks} if blocks else {}
            await web.chat_update(
                channel=chat_id,
                ts=ts,
                text=text,
                **extra,
            )
            return True
        except Exception as e:  # pylint: disable=broad-except
            logger.debug("Slack update in '%s' failed: %s", chat_id, e)
            return False
