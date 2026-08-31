# -*- coding: utf-8 -*-
"""Discord channel (discord.py, gateway WebSocket).

discord.py is async-native and runs on the app event loop, so — unlike
Feishu — there is no thread bridging: ``on_message`` and button callbacks
``await self._emit(...)`` directly. On a button click the channel freezes
its own card and emits a ``ChannelConfirmationResultEvent`` carrying the
tool call's id.

Note: this channel opens one gateway connection per node. Discord's own
model expects one connection per shard; running many nodes for one bot
needs shard coordination, which is out of scope here.
"""
import asyncio
import base64
import io
from typing import AsyncIterator, Awaitable, Callable, TYPE_CHECKING

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

if TYPE_CHECKING:
    import discord

# Discord's hard limit is 2000 characters per message.
_MAX_LEN = 2000
# Give up (and park in 'failed') after this many connects that never came up.
_MAX_CONNECT_ATTEMPTS = 2


class DiscordChannel(ChannelBase):
    """Discord platform channel."""

    channel_type = "discord"
    display_name = "Discord"
    description = "Bot for Discord servers, channels and DMs."
    icon_url = "https://www.google.com/s2/favicons?domain=discord.com&sz=128"
    platform_bot_id_field = "application_id"

    class Credentials(BaseModel):
        """Discord bot credentials."""

        bot_token: str = Field(
            title="Bot Token",
            json_schema_extra={"format": "password"},
        )
        application_id: str = Field(title="Application ID")

    class Config(BaseModel):
        """Discord platform options."""

        only_at_reply: bool = Field(
            default=True,
            title="Reply only when mentioned",
            description="In server channels, reply only when the bot is "
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
        max_message_length=_MAX_LEN,
    )

    def __init__(
        self,
        channel_id: str,
        credentials: "DiscordChannel.Credentials",
        config: "DiscordChannel.Config",
    ) -> None:
        """Read the bot token and options from the validated models.

        Args:
            channel_id (`str`):
                This channel instance's unique id.
            credentials (`DiscordChannel.Credentials`):
                Validated bot credentials (token + application id).
            config (`DiscordChannel.Config`):
                Validated platform options.
        """
        self._channel_id = channel_id
        self._bot_token = credentials.bot_token
        self._config = config
        self.status = ChannelStatus()
        self._client: "discord.Client | None" = None
        self._client_lock = asyncio.Lock()
        self._stopped = False

    @property
    def channel_id(self) -> str:
        """The unique channel instance identifier."""
        return self._channel_id

    async def aclose(self) -> None:
        """Close the client this instance logged in lazily.

        The connection loop closes its own in ``start_listening``; this
        covers the client-only instances, which never run it.
        """
        if self._client is not None and not self._stopped:
            await self._client.close()
            self._client = None

    async def _ensure_client(self) -> "discord.Client":
        """Return a client able to call the REST API, connected or not.

        :class:`~agentscope.app.channel.ChannelClients` builds instances
        that never run :meth:`start_listening`; ``login`` alone gives
        those a working REST client. Its gateway cache stays empty, so
        every read below goes through ``fetch_*`` rather than ``get_*``.
        """
        if self._client is not None:
            return self._client
        # Publish only after login succeeds: a half-built client handed
        # to a concurrent caller would make unauthenticated requests,
        # and a failed one would be cached forever, skipping login.
        async with self._client_lock:
            if self._client is None:
                import discord

                intents = discord.Intents.default()
                intents.message_content = True
                client = discord.Client(intents=intents)
                try:
                    await client.login(self._bot_token)
                except Exception:
                    await client.close()
                    raise
                self._client = client
        return self._client

    # -- Lifecycle --

    async def start_listening(
        self,
        emit: Callable[
            [ChannelEvent | ChannelConfirmationResultEvent],
            Awaitable[None],
        ],
    ) -> None:
        """Build the client, register the message handler, run the gateway
        (discord.py self-reconnects), and close it on exit.

        Args:
            emit (`Callable`): Gateway callback for inbound events.
        """
        import discord

        self._emit = emit
        self.status.state = "connecting"
        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        ever_connected = False

        @self._client.event
        async def on_message(message: "discord.Message") -> None:
            """discord.py inbound-message hook.

            Args:
                message (`discord.Message`): The inbound message.
            """
            await self._on_message(message)

        @self._client.event
        async def on_ready() -> None:
            """Gateway is up — mark the channel connected."""
            nonlocal ever_connected
            ever_connected = True
            self.status.state = "connected"
            self.status.last_error = ""

        try:
            backoff = 1.0
            attempts = 0
            while not self._stopped:
                try:
                    await self._client.start(self._bot_token)
                except Exception as e:  # pylint: disable=broad-except
                    if self._stopped:
                        break
                    self.status.state = "retrying"
                    self.status.last_error = str(e)
                    logger.exception(
                        "Discord '%s' client error",
                        self._channel_id,
                    )
                if self._stopped:
                    break
                # Give up only when we never connected (bad token). Park in
                # 'failed'; editing the channel re-tries. Transient drops
                # after a good connect keep retrying.
                if not ever_connected:
                    attempts += 1
                    if attempts >= _MAX_CONNECT_ATTEMPTS:
                        self.status.state = "failed"
                        if not self.status.last_error:
                            self.status.last_error = "connect failed"
                        logger.error(
                            "Discord '%s' giving up after %d attempts: %s",
                            self._channel_id,
                            attempts,
                            self.status.last_error,
                        )
                        while not self._stopped:
                            await asyncio.sleep(30.0)
                        break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
        finally:
            self._stopped = True
            self.status.state = "stopped"
            await self._client.close()

    # -- Inbound --

    async def _on_message(self, message: "discord.Message") -> None:
        """Normalise an inbound message and emit it — ignoring own
        messages, honouring ``only_at_reply``, downloading attachments.

        Args:
            message (`discord.Message`): The inbound discord.py message.
        """
        if message.author.id == self._client.user.id:
            return  # ignore our own messages
        try:
            is_dm = message.guild is None
            me = self._client.user
            if (
                not is_dm
                and self._config.only_at_reply
                and me not in message.mentions
            ):
                return

            text = message.content or ""
            for token in (f"<@{me.id}>", f"<@!{me.id}>"):
                text = text.replace(token, "")
            text = text.strip()

            content: list[TextBlock | DataBlock] = []
            for attachment in message.attachments:
                try:
                    data = await attachment.read()
                except Exception:  # pylint: disable=broad-except
                    logger.debug("Discord attachment download failed")
                    continue
                content.append(
                    DataBlock(
                        source=Base64Source(
                            data=base64.b64encode(data).decode("ascii"),
                            media_type=attachment.content_type
                            or "application/octet-stream",
                        ),
                        name=attachment.filename,
                    ),
                )
            if text:
                content.append(TextBlock(text=text))
            if not content or not self._emit:
                return

            await self._emit(
                ChannelEvent(
                    channel_id=self._channel_id,
                    channel_user_id=str(message.author.id),
                    chat_id=str(message.channel.id),
                    channel_message_id=str(message.id),
                    content=content,
                    metadata={"chat_type": "dm" if is_dm else "guild"},
                ),
            )
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "Discord '%s' message handling failed",
                self._channel_id,
            )

    # -- Outbound --

    async def send_response(
        self,
        event: ChannelEvent,
        events: AsyncIterator[dict],
    ) -> None:
        """Accumulate the reply (non-streaming) and post it as text +
        file attachments; present an approval card if the run parks.

        Args:
            event (`ChannelEvent`): The send target (chat id).
            events (`AsyncIterator[dict]`): The run's session events.
        """
        import discord

        channel = await self._channel(event.chat_id)
        if channel is None:
            return
        reply: Msg | None = None
        confirm: RequireUserConfirmEvent | None = None
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
        for block in self._render(
            reply,
            show_thinking=self._config.show_thinking,
            show_tool_process=self._config.show_tool_process,
        ):
            if isinstance(block, TextBlock):
                for part in self._split_long_message(block.text):
                    if part:
                        await channel.send(part)
            elif isinstance(block.source, Base64Source):
                await channel.send(
                    file=discord.File(
                        io.BytesIO(base64.b64decode(block.source.data)),
                        filename=block.name or "attachment",
                    ),
                )
        if confirm is not None:
            await self._present_confirm(event, confirm)

    async def _present_confirm(
        self,
        event: ChannelEvent,
        req: RequireUserConfirmEvent,
    ) -> None:
        """Post one allow/deny card per tool call; each button carries its
        ``tool_call_id`` so the click self-correlates.

        Args:
            event (`ChannelEvent`): The send target, for its ``chat_id``.
            req (`RequireUserConfirmEvent`): The approval request to show.
        """
        channel = await self._channel(event.chat_id)
        if channel is None:
            return
        for tool in req.tool_calls:
            await channel.send(
                content="🛡️ Tool execution needs approval\n"
                f"**Tool:** `{tool.name}`\n"
                f"**Arguments:** {str(tool.input)[:800]}",
                view=self._build_view(
                    tool.id,
                    event.metadata.get("agent_id", ""),
                    event.metadata.get("session_id", ""),
                ),
            )

    async def list_bot_chats(self) -> list[dict]:
        """List every text channel the bot can see as ``{chat_id, name}``.

        Paginates over REST rather than reading ``client.guilds`` /
        ``guild.text_channels``: those are the gateway cache, which is
        empty on an instance that only logged in.
        """
        import discord

        client = await self._ensure_client()
        results: list[dict] = []
        async for guild in client.fetch_guilds():
            for channel in await guild.fetch_channels():
                if isinstance(channel, discord.TextChannel):
                    results.append(
                        {
                            "chat_id": str(channel.id),
                            "name": f"{guild.name}#{channel.name}",
                        },
                    )
        return results

    async def chat_kind(self, chat_id: str) -> ChatKind | None:
        """Private for a DM channel, group otherwise; ``None`` if the
        channel id can't be resolved.

        Args:
            chat_id (`str`): The Discord channel id as a string.
        """
        import discord

        channel = await self._channel(chat_id)
        if channel is None:
            return None
        return (
            ChatKind.PRIVATE
            if isinstance(channel, discord.DMChannel)
            else ChatKind.GROUP
        )

    async def chat_name(self, chat_id: str) -> str:
        """The channel's name; ``""`` for DMs or an unresolvable id.

        Args:
            chat_id (`str`): The Discord channel id as a string.
        """
        channel = await self._channel(chat_id)
        return getattr(channel, "name", "") or "" if channel else ""

    # -- Helpers --

    async def _channel(
        self,
        chat_id: str,
    ) -> "discord.abc.Messageable | None":
        """Resolve a channel id (from cache, then fetch) to a channel.

        Args:
            chat_id (`str`):
                The Discord channel id as a string.

        Returns:
            `discord.abc.Messageable | None`:
                The channel, or ``None`` if the id is malformed.
        """
        try:
            cid = int(chat_id)
        except (TypeError, ValueError):
            return None
        client = await self._ensure_client()
        return client.get_channel(cid) or await client.fetch_channel(cid)

    def _build_view(
        self,
        tool_call_id: str,
        agent_id: str = "",
        session_id: str = "",
    ) -> "discord.ui.View":
        """Build a two-button approval view whose callbacks freeze the card
        and emit the decision for ``tool_call_id``.

        Args:
            tool_call_id (`str`): The tool call the buttons answer.
            agent_id (`str`): Target agent, echoed on click to resume the
                exact run without re-resolving routing.
            session_id (`str`): Target session, echoed alongside
                ``agent_id``.

        Returns:
            `discord.ui.View`: The allow/deny view for the card message.
        """
        # pylint: disable=protected-access
        import discord

        channel = self

        class _ApprovalView(discord.ui.View):
            """A persistent (never-timing-out) allow/deny button view."""

            def __init__(self) -> None:
                """Build the view with no timeout."""
                super().__init__(timeout=None)

            @discord.ui.button(
                label="✅ Approve",
                style=discord.ButtonStyle.green,
            )
            async def approve(
                self,
                interaction: "discord.Interaction",
                _button: "discord.ui.Button",
            ) -> None:
                """Emit an approve decision.

                Args:
                    interaction (`discord.Interaction`): The click.
                    _button (`discord.ui.Button`): The clicked button.
                """
                await channel._decide(
                    interaction,
                    tool_call_id,
                    True,
                    agent_id,
                    session_id,
                )

            @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.red)
            async def deny(
                self,
                interaction: "discord.Interaction",
                _button: "discord.ui.Button",
            ) -> None:
                """Emit a deny decision.

                Args:
                    interaction (`discord.Interaction`): The click.
                    _button (`discord.ui.Button`): The clicked button.
                """
                await channel._decide(
                    interaction,
                    tool_call_id,
                    False,
                    agent_id,
                    session_id,
                )

        return _ApprovalView()

    async def _decide(
        self,
        interaction: "discord.Interaction",
        tool_call_id: str,
        approved: bool,
        agent_id: str = "",
        session_id: str = "",
    ) -> None:
        """Freeze the card and emit the decision for ``tool_call_id``.

        Args:
            interaction (`discord.Interaction`): The click, for the card
                message, chat id and clicking user.
            tool_call_id (`str`): The tool call being answered.
            approved (`bool`): The user's decision.
            agent_id (`str`): Target agent pinned on the card at send
                time; resumes the exact run without re-routing.
            session_id (`str`): Target session pinned alongside
                ``agent_id``.
        """
        await interaction.response.defer()
        try:
            await interaction.message.edit(
                content="✅ Approved" if approved else "🚫 Denied",
                view=None,
            )
        except Exception:  # pylint: disable=broad-except
            logger.debug("Discord card freeze failed")
        if self._emit:
            await self._emit(
                ChannelConfirmationResultEvent(
                    channel_id=self._channel_id,
                    chat_id=str(interaction.channel_id),
                    channel_user_id=str(interaction.user.id),
                    agent_id=agent_id,
                    session_id=session_id,
                    tool_call_id=tool_call_id,
                    approved=approved,
                ),
            )
