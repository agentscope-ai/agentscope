# -*- coding: utf-8 -*-
"""DingTalk channel using the official Stream SDK.

The Stream SDK owns the long-lived inbound connection. Everything outbound
— replies, media transfer, cards, and user search — is DingTalk OpenAPI, so
an instance that never connects can still send.
"""

import asyncio
import base64
import binascii
import json
import mimetypes
import time
from typing import Any, AsyncIterator, Awaitable, Callable, TYPE_CHECKING
from urllib.parse import quote_plus

from pydantic import BaseModel, Field

from ...._logging import logger
from ....event import ReplyEndEvent, RequireUserConfirmEvent
from ....message import (
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from .._base import (
    ChannelBase,
    ChannelCapability,
    ChannelConfirmationResultEvent,
    ChannelEvent,
    ChannelStatus,
    ChatKind,
    _EVENT_ADAPTER,
)
from ._card import (
    _approval_card_data,
    _parse_card_callback,
    _resolved_card_data,
    _tracking_id,
)
from ._openapi import _DingTalkOpenAPI

if TYPE_CHECKING:
    from ....tool import ToolBase
    from ....workspace import WorkspaceBase

_CHATBOT_TOPIC = "/v1.0/im/bot/messages/get"
_CARD_CALLBACK_TOPIC = "/v1.0/card/instances/callback"
_GROUP_CONVERSATION = "2"
_MAX_LEN = 4000
# The streaming AI card DingTalk publishes, and its Markdown variable —
# the template the official typewriter example streams into.
_AI_CARD_TEMPLATE_ID = "8aebdfb9-28f4-4a98-98f5-396c3dde41a0.schema"
_AI_CARD_CONTENT_KEY = "content"
# The general-purpose AI card, the published template that takes its
# layout and buttons at send time — see ``_card`` for what it is sent.
_APPROVAL_CARD_TEMPLATE_ID = "382e4302-551d-4880-bf29-a30acfab2e71.schema"
_STATUS_POLL_INTERVAL = 0.2
_STREAM_MIN_INTERVAL = 0.3
_STREAM_FALLBACK_NOTICE = (
    "Streaming stopped. The complete reply follows as a Markdown message."
)
_DEFAULT_MAX_MEDIA_BYTES = 10 * 1024 * 1024
_MEDIA_MESSAGE_TYPES = frozenset(
    {"audio", "file", "picture", "richText", "video"},
)


class DingTalkChannel(ChannelBase):
    """DingTalk enterprise application robot channel."""

    channel_type = "dingtalk"
    display_name = "DingTalk"
    description = "Enterprise robot for DingTalk groups and direct messages."
    icon_url = "https://www.google.com/s2/favicons?domain=dingtalk.com&sz=128"
    platform_bot_id_field = "client_id"

    class Credentials(BaseModel):
        """DingTalk application credentials."""

        client_id: str = Field(
            title="Client ID",
            description="DingTalk application Client ID (AppKey)",
        )
        client_secret: str = Field(
            title="Client Secret",
            description="DingTalk application Client Secret (AppSecret)",
            json_schema_extra={"format": "password"},
        )

    class Config(BaseModel):
        """DingTalk platform options."""

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
        max_media_bytes: int = Field(
            default=_DEFAULT_MAX_MEDIA_BYTES,
            ge=1,
            le=100 * 1024 * 1024,
            title="Maximum media size",
            description="Maximum bytes accepted for one inbound or "
            "outbound attachment",
        )
        approval_card_template_id: str = Field(
            default=_APPROVAL_CARD_TEMPLATE_ID,
            title="Approval card template ID",
            description="DingTalk Card Platform template used for tool "
            "approval. Defaults to the published card the channel builds "
            "its own layout on; tool calls needing approval stall if it "
            "is cleared.",
        )
        streaming_card_template_id: str = Field(
            default=_AI_CARD_TEMPLATE_ID,
            title="Streaming card template ID",
            description="DingTalk AI Card template used for streaming "
            "replies. Defaults to the public template the official SDK "
            "uses; clear it to reply in plain Markdown instead.",
        )
        streaming_card_key: str = Field(
            default=_AI_CARD_CONTENT_KEY,
            min_length=1,
            title="Streaming card content key",
            description="Template variable key of the AI Card streaming "
            "component.",
        )

    capabilities = ChannelCapability(
        text=True,
        markdown=True,
        image=True,
        file=True,
        interactive=True,
        streaming=False,
        max_message_length=_MAX_LEN,
    )

    def __init__(
        self,
        channel_id: str,
        credentials: "DingTalkChannel.Credentials",
        config: "DingTalkChannel.Config",
    ) -> None:
        """Build a DingTalk channel from validated settings.

        Args:
            channel_id (`str`): This channel instance's unique id.
            credentials (`DingTalkChannel.Credentials`): DingTalk Client ID
                and Client Secret.
            config (`DingTalkChannel.Config`): Channel display and routing
                options.
        """
        self._channel_id = channel_id
        self._client_id = credentials.client_id
        self._client_secret = credentials.client_secret
        self._config = config
        self.capabilities = type(self).capabilities.model_copy(
            update={
                "streaming": bool(config.streaming_card_template_id),
            },
        )
        self.status = ChannelStatus()
        self._stream_client: Any = None
        self._http: Any = None
        self._openapi: _DingTalkOpenAPI | None = None
        self._emit: (
            Callable[
                [ChannelEvent | ChannelConfirmationResultEvent],
                Awaitable[None],
            ]
            | None
        ) = None
        self._chat_names: dict[str, str] = {}

    @property
    def channel_id(self) -> str:
        """The unique channel instance identifier."""
        return self._channel_id

    async def start_listening(
        self,
        emit: Callable[
            [ChannelEvent | ChannelConfirmationResultEvent],
            Awaitable[None],
        ],
    ) -> None:
        """Start the DingTalk Stream connection until cancelled.

        The official SDK performs reconnects. This method owns both the
        SDK client and the asynchronous HTTP client used for replies.

        Args:
            emit (`Callable`): Gateway callback for normalised inbound
                events.
        """
        self._emit = emit
        self.status.state = "connecting"
        self.status.last_error = ""
        stream_task: asyncio.Task[Any] | None = None
        ever_connected = False
        try:
            self._api()
            self._stream_client = self._new_stream_client()
            stream_task = asyncio.create_task(self._stream_client.start())
            while not stream_task.done():
                if getattr(self._stream_client, "websocket", None) is not None:
                    ever_connected = True
                    self.status.state = "connected"
                    self.status.last_error = ""
                elif ever_connected:
                    self.status.state = "retrying"
                await asyncio.sleep(_STATUS_POLL_INTERVAL)
            await stream_task
            if self.status.state != "stopped":
                raise RuntimeError(
                    "DingTalk Stream client stopped unexpectedly",
                )
        except Exception as error:  # pylint: disable=broad-except
            self.status.state = "failed"
            self.status.last_error = str(error)
            logger.exception(
                "DingTalk '%s' Stream client failed",
                self._channel_id,
            )
            while True:
                await asyncio.sleep(30.0)
        finally:
            if self._stream_client is not None:
                try:
                    await self._stream_client.stop()
                except Exception:  # pylint: disable=broad-except
                    logger.debug(
                        "DingTalk '%s' Stream client stop failed",
                        self._channel_id,
                    )
            if stream_task is not None:
                if not stream_task.done():
                    stream_task.cancel()
                await asyncio.gather(stream_task, return_exceptions=True)
            if self._http is not None:
                await self._http.aclose()
            self._stream_client = None
            self._http = None
            self._openapi = None
            self.status.state = "stopped"

    def _api(self) -> _DingTalkOpenAPI:
        """Return the OpenAPI client, building it on first use.

        Everything outbound is plain REST, so an instance built by
        :class:`~agentscope.app.channel.ChannelClients` — one that never
        runs ``start_listening`` — must reach DingTalk too.

        Returns:
            `_DingTalkOpenAPI`: This channel's OpenAPI client.
        """
        if self._openapi is None:
            self._http = self._new_http_client()
            self._openapi = _DingTalkOpenAPI(
                self._client_id,
                self._client_secret,
                self._http,
            )
        return self._openapi

    async def aclose(self) -> None:
        """Close an HTTP client opened outside the connection loop."""
        if self._http is not None:
            await self._http.aclose()
        self._http = None
        self._openapi = None

    async def send_response(
        self,
        event: ChannelEvent,
        events: AsyncIterator[dict],
    ) -> None:
        """Send an Agent reply using an optional DingTalk AI Card stream.

        Without a streaming-card template the complete reply is sent as one
        or more Markdown messages. A configured card is updated at a bounded
        rate and falls back to Markdown if card creation or updating fails.

        Args:
            event (`ChannelEvent`): The DingTalk reply target.
            events (`AsyncIterator[dict]`): The run's streamed Agent events.
        """
        reply: Msg | None = None
        confirm: RequireUserConfirmEvent | None = None
        stream_ref: str | None = None
        stream_failed = False
        last_stream_update = 0.0
        async for raw in events:
            agent_event = _EVENT_ADAPTER.validate_python(raw)
            if isinstance(agent_event, RequireUserConfirmEvent):
                confirm = agent_event
                break
            reply_id = getattr(agent_event, "reply_id", None)
            if reply_id is not None:
                if reply is None:
                    # The reply opens with the agent's own name; keep it,
                    # an approval card names who asked.
                    reply = Msg(
                        name=getattr(agent_event, "name", "") or "assistant",
                        role="assistant",
                        content=[],
                    )
                    reply.id = reply_id
                reply.append_event(agent_event)
            if isinstance(agent_event, ReplyEndEvent):
                break

            if (
                not self.capabilities.streaming
                or stream_failed
                or reply is None
                or not self._has_streaming_text(reply)
            ):
                continue
            rendered = self._render(
                reply,
                show_thinking=self._config.show_thinking,
                show_tool_process=self._config.show_tool_process,
            )
            text = "".join(
                block.text
                for block in rendered
                if isinstance(block, TextBlock)
            )
            if not text:
                continue
            if stream_ref is None:
                stream_ref = await self._open_streaming_card(event.chat_id)
                if stream_ref is None:
                    stream_failed = True
                    continue
            now = time.monotonic()
            if now - last_stream_update >= _STREAM_MIN_INTERVAL:
                last_stream_update = now
                if not await self._update_streaming_card(
                    stream_ref,
                    text,
                ):
                    stream_failed = True

        blocks = self._render(
            reply,
            show_thinking=self._config.show_thinking,
            show_tool_process=self._config.show_tool_process,
        )
        text = "".join(
            block.text for block in blocks if isinstance(block, TextBlock)
        )
        streamed = await self._finish_streaming_card(stream_ref, text)
        for block in blocks:
            if isinstance(block, TextBlock):
                if streamed:
                    continue
                for part in self._split_long_message(block.text):
                    if part:
                        await self._api().send_text(event.chat_id, part)
            elif isinstance(block, DataBlock):
                await self._send_data(event.chat_id, block)
        if confirm is not None:
            await self._present_confirm(
                event,
                confirm,
                reply.name if reply else "",
            )

    def _has_streaming_text(self, reply: Msg) -> bool:
        """Whether the partial reply contains text enabled for display."""
        for block in reply.content:
            if isinstance(block, TextBlock) and block.text.strip():
                return True
            if (
                isinstance(block, ThinkingBlock)
                and self._config.show_thinking
                and block.thinking.strip()
            ):
                return True
            if (
                isinstance(block, ToolCallBlock)
                and self._config.show_tool_process
            ):
                return True
            if (
                isinstance(block, ToolResultBlock)
                and self._config.show_tool_process
                and isinstance(block.output, str)
                and block.output.strip()
            ):
                return True
        return False

    async def _open_streaming_card(self, chat_id: str) -> str | None:
        """Create and deliver a configured AI streaming card."""
        return await self._api().create_streaming_card(
            chat_id,
            self._config.streaming_card_template_id,
            self._config.streaming_card_key,
        )

    async def _update_streaming_card(
        self,
        out_track_id: str,
        text: str,
        *,
        finalize: bool = False,
        is_error: bool = False,
    ) -> bool:
        """Write the complete Markdown value to one AI streaming card."""
        return await self._api().stream_card(
            out_track_id,
            self._config.streaming_card_key,
            text,
            finalize=finalize,
            is_error=is_error,
        )

    async def _finish_streaming_card(
        self,
        out_track_id: str | None,
        text: str,
    ) -> bool:
        """Finalize a live card, returning false for Markdown fallback."""
        if out_track_id is None or not text:
            return False
        updated = await self._update_streaming_card(
            out_track_id,
            text,
            finalize=True,
        )
        if not updated:
            await self._update_streaming_card(
                out_track_id,
                _STREAM_FALLBACK_NOTICE,
                finalize=True,
                is_error=True,
            )
        return updated

    async def chat_kind(self, chat_id: str) -> ChatKind | None:
        """Return the audience kind encoded in a DingTalk chat id.

        Args:
            chat_id (`str`): ``group:<openConversationId>`` or
                ``user:<staffId>``.

        Returns:
            `ChatKind | None`: Group, private, or ``None`` for an unknown
            address.
        """
        if chat_id.startswith("group:"):
            return ChatKind.GROUP
        if chat_id.startswith("user:"):
            return ChatKind.PRIVATE
        return None

    async def chat_name(self, chat_id: str) -> str:
        """Return a cached inbound conversation title.

        Args:
            chat_id (`str`): The encoded DingTalk chat id.

        Returns:
            `str`: The title supplied by DingTalk, or an empty string.
        """
        return self._chat_names.get(chat_id, "")

    async def list_bot_chats(self) -> list[dict]:
        """List conversations observed by this robot process.

        DingTalk does not expose an application-robot equivalent of
        Feishu's bot-chat enumeration API. The result is therefore limited
        to conversations from which this process has received a callback.

        Returns:
            `list[dict]`: Known encoded targets, names, and chat types.
        """
        return [
            {
                "chat_id": chat_id,
                "name": name,
                "chat_type": (
                    "group" if chat_id.startswith("group:") else "private"
                ),
            }
            for chat_id, name in self._chat_names.items()
        ]

    async def list_tools(
        self,
        workspace: "WorkspaceBase",
    ) -> list["ToolBase"]:
        """Expose DingTalk discovery and target-send tools to the agent.

        Args:
            workspace (`WorkspaceBase`): Calling session workspace whose
                backend is used for file reads.

        Returns:
            `list[ToolBase]`: DingTalk agent tools.
        """
        from ._tools import (
            ListConversations,
            ListUsers,
            SendFile,
            SendImage,
            SendMessage,
        )

        backend = workspace.get_backend()
        return [
            ListConversations(self, backend),
            ListUsers(self, backend),
            SendMessage(self, backend),
            SendFile(self, backend),
            SendImage(self, backend),
        ]

    async def search_users(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search users visible to the DingTalk application.

        Args:
            query (`str`): User-name search term.
            limit (`int`): Maximum number of results.

        Returns:
            `list[dict[str, Any]]`: Basic visible user profiles.
        """
        return await self._api().search_users(query, limit)

    async def send_message_to(self, target: str, text: str) -> bool:
        """Send Markdown text to an encoded DingTalk target.

        Args:
            target (`str`): Target from a discovery tool.
            text (`str`): Markdown-formatted message body.

        Returns:
            `bool`: Whether DingTalk accepted the message.
        """
        return await self._api().send_text(target, text)

    async def send_file_to(
        self,
        target: str,
        data: bytes,
        file_name: str,
    ) -> bool:
        """Upload and send a workspace file to a DingTalk target.

        Args:
            target (`str`): Target from a discovery tool.
            data (`bytes`): File bytes.
            file_name (`str`): Display filename.

        Returns:
            `bool`: Whether DingTalk accepted the file.
        """
        if len(data) > self._config.max_media_bytes:
            logger.warning("DingTalk outbound media exceeds the size limit")
            return False
        return await self._api().send_media(
            target,
            data,
            self._safe_file_name(file_name),
            "application/octet-stream",
        )

    async def send_image_to(
        self,
        target: str,
        data: bytes,
        file_name: str,
    ) -> bool:
        """Upload and send a workspace image to a DingTalk target.

        Args:
            target (`str`): Target from a discovery tool.
            data (`bytes`): Image bytes.
            file_name (`str`): Image filename used for MIME detection.

        Returns:
            `bool`: Whether DingTalk accepted the image.
        """
        if len(data) > self._config.max_media_bytes:
            logger.warning("DingTalk outbound media exceeds the size limit")
            return False
        safe_name = self._safe_file_name(file_name)
        media_type = mimetypes.guess_type(safe_name)[0] or ""
        if not media_type.startswith("image/"):
            logger.warning("DingTalk SendImage requires an image file")
            return False
        return await self._api().send_media(
            target,
            data,
            safe_name,
            media_type,
        )

    def _new_http_client(self) -> Any:
        """Create the asynchronous HTTP client used by this channel."""
        import httpx

        return httpx.AsyncClient(timeout=30.0)

    def _new_stream_client(self) -> Any:
        """Build and configure the official DingTalk Stream client."""
        import dingtalk_stream  # type: ignore[import-untyped]
        import websockets  # type: ignore[import-untyped]

        channel = self
        on_callback = self._on_callback
        on_card_callback = self._on_card_callback

        class _StoppableStreamClient(
            dingtalk_stream.DingTalkStreamClient,
        ):
            """Add cooperative shutdown to the official Stream client.

            ``dingtalk-stream`` 0.24.3 has no ``stop`` method and its
            ``start`` loop catches cancellation. Keep the SDK's connection
            and routing methods while providing a lifecycle that Agent
            Service can terminate cleanly.
            """

            def __init__(self, credential: Any) -> None:
                super().__init__(credential)
                self.websocket: Any = None
                self._stop_event = asyncio.Event()
                self._worker_tasks: set[asyncio.Task[Any]] = set()
                self._keepalive_task: asyncio.Task[Any] | None = None

            async def start(self) -> None:
                """Connect and reconnect until :meth:`stop` is called."""
                self.pre_start()
                while not self._stop_event.is_set():
                    try:
                        connection = await asyncio.to_thread(
                            self.open_connection,
                        )
                        if not connection:
                            await self._retry_after(10.0)
                            continue
                        uri = (
                            f"{connection['endpoint']}?ticket="
                            f"{quote_plus(connection['ticket'])}"
                        )
                        async with websockets.connect(uri) as websocket:
                            self.websocket = websocket
                            self._keepalive_task = asyncio.create_task(
                                self.keepalive(websocket),
                            )
                            async for raw_message in websocket:
                                if self._stop_event.is_set():
                                    break
                                task = asyncio.create_task(
                                    self.background_task(
                                        json.loads(raw_message),
                                    ),
                                )
                                self._worker_tasks.add(task)
                                task.add_done_callback(
                                    self._worker_tasks.discard,
                                )
                    except asyncio.CancelledError:
                        raise
                    except (
                        websockets.exceptions.ConnectionClosedError
                    ) as error:
                        if not self._stop_event.is_set():
                            self.logger.warning(
                                "DingTalk Stream connection closed: %s",
                                error,
                            )
                            await self._retry_after(10.0)
                    except Exception as error:  # pylint: disable=broad-except
                        if not self._stop_event.is_set():
                            self.logger.warning(
                                "DingTalk Stream connection failed: %s",
                                error,
                            )
                            await self._retry_after(3.0)
                    finally:
                        await self._cancel_keepalive()
                        self.websocket = None

            async def stop(self) -> None:
                """Stop reconnecting and close all Stream tasks."""
                self._stop_event.set()
                websocket = self.websocket
                if websocket is not None:
                    await websocket.close()
                await self._cancel_keepalive()
                tasks = list(self._worker_tasks)
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                self._worker_tasks.clear()

            async def _retry_after(self, seconds: float) -> None:
                """Wait for a reconnect delay or an earlier stop request."""
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=seconds,
                    )
                except TimeoutError:
                    pass

            async def _cancel_keepalive(self) -> None:
                """Cancel the SDK keepalive task when one is active."""
                task = self._keepalive_task
                self._keepalive_task = None
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        class _MessageHandler(dingtalk_stream.CallbackHandler):
            """Forward one SDK callback into the owning channel."""

            async def process(self, callback: Any) -> tuple[int, str]:
                """Process one robot message callback.

                Args:
                    callback (`Any`): The Stream SDK callback message.

                Returns:
                    `tuple[int, str]`: DingTalk acknowledgement status and
                    message.
                """
                try:
                    await on_callback(callback.data)
                except Exception:  # pylint: disable=broad-except
                    logger.exception(
                        "DingTalk '%s' callback failed",
                        channel.channel_id,
                    )
                    return (
                        dingtalk_stream.AckMessage.STATUS_SYSTEM_EXCEPTION,
                        "ERROR",
                    )
                return dingtalk_stream.AckMessage.STATUS_OK, "OK"

        class _CardHandler(dingtalk_stream.CallbackHandler):
            """Forward an advanced-card callback into the channel."""

            async def process(self, callback: Any) -> tuple[int, str]:
                """Process one approval-card action callback.

                Args:
                    callback (`Any`): The Stream SDK callback message.

                Returns:
                    `tuple[int, str]`: DingTalk acknowledgement status and
                    message.
                """
                try:
                    await on_card_callback(callback.data)
                except Exception:  # pylint: disable=broad-except
                    logger.exception(
                        "DingTalk '%s' card callback failed",
                        channel.channel_id,
                    )
                    return (
                        dingtalk_stream.AckMessage.STATUS_SYSTEM_EXCEPTION,
                        "ERROR",
                    )
                return dingtalk_stream.AckMessage.STATUS_OK, "OK"

        credential = dingtalk_stream.Credential(
            self._client_id,
            self._client_secret,
        )
        client = _StoppableStreamClient(credential)
        client.register_callback_handler(_CHATBOT_TOPIC, _MessageHandler())
        client.register_callback_handler(_CARD_CALLBACK_TOPIC, _CardHandler())
        return client

    async def _present_confirm(
        self,
        event: ChannelEvent,
        request: RequireUserConfirmEvent,
        agent_name: str,
    ) -> None:
        """Create one approval card for each pending tool call.

        Args:
            event (`ChannelEvent`): Chat receiving the cards.
            request (`RequireUserConfirmEvent`): Pending tool calls.
            agent_name (`str`): The agent that asked for approval.
        """
        template_id = self._config.approval_card_template_id
        if not template_id:
            logger.error(
                "DingTalk '%s' cannot deliver tool approval cards",
                self._channel_id,
            )
            # The run is parked on an approval nobody can give. Say so
            # here, or the chat simply goes quiet.
            await self._api().send_text(
                event.chat_id,
                "无法展示工具审批卡片：审批卡片模板为空，请管理员配置。",
            )
            return
        approver_id = (
            event.chat_id.removeprefix("user:")
            if event.chat_id.startswith("user:")
            else ""
        )
        for tool in request.tool_calls:
            out_track_id = await self._api().create_approval_card(
                event.chat_id,
                approver_id,
                template_id,
                _approval_card_data(tool, agent_name),
                _tracking_id(tool.id),
            )
            if out_track_id is None:
                await self._api().send_text(
                    event.chat_id,
                    "工具审批卡片投放失败，请管理员检查卡片模板与应用权限。",
                )

    async def _on_card_callback(self, payload: dict[str, Any]) -> None:
        """Validate a card decision and emit the gateway resume event.

        Args:
            payload (`dict[str, Any]`): Stream advanced-card callback data.
        """
        decision = _parse_card_callback(payload)
        if decision is None:
            # The payload carries the clicker, the conversation and
            # whatever the card was built with; name the card instead.
            logger.warning(
                "DingTalk '%s' ignored a card callback on '%s' from '%s'",
                self._channel_id,
                payload.get("outTrackId"),
                payload.get("userId"),
            )
            return
        if self._emit is None:
            return
        await self._emit(
            ChannelConfirmationResultEvent(
                channel_id=self._channel_id,
                chat_id=decision.chat_id,
                channel_user_id=decision.user_id,
                agent_id=decision.agent_id,
                session_id=decision.session_id,
                tool_call_id=decision.tool_call_id,
                approved=decision.approved,
                actor=decision.user_id,
            ),
        )
        await self._api().update_approval_card(
            decision.out_track_id,
            _resolved_card_data(decision.approved),
        )

    async def _on_callback(self, payload: dict[str, Any]) -> None:
        """Normalise a DingTalk robot callback and emit it.

        Args:
            payload (`dict[str, Any]`): Parsed callback data from the Stream
                SDK.
        """
        conversation_type = str(payload.get("conversationType", ""))
        is_group = conversation_type == _GROUP_CONVERSATION
        if (
            is_group
            and self._config.only_at_reply
            and payload.get("isInAtList") is False
        ):
            return

        user_id = str(
            payload.get("senderStaffId") or payload.get("senderId") or "",
        )
        conversation_id = str(payload.get("conversationId") or "")
        target_id = conversation_id if is_group else user_id
        if not user_id or not target_id:
            logger.warning(
                "DingTalk '%s' ignored message without stable sender/target",
                self._channel_id,
            )
            return

        chat_id = f"group:{target_id}" if is_group else f"user:{target_id}"
        title = str(payload.get("conversationTitle") or "")
        sender_name = str(payload.get("senderNick") or "")
        self._chat_names[chat_id] = title if is_group else sender_name
        content = await self._parse_content(payload)
        if not content or self._emit is None:
            return

        await self._emit(
            ChannelEvent(
                channel_id=self._channel_id,
                channel_user_id=user_id,
                channel_user_name=sender_name,
                chat_id=chat_id,
                chat_name=title if is_group else "",
                channel_message_id=str(payload.get("msgId") or "") or None,
                content=content,
                metadata={
                    "chat_type": "group" if is_group else "private",
                    "conversation_type": conversation_type,
                },
            ),
        )

    async def _parse_content(
        self,
        payload: dict[str, Any],
    ) -> list[TextBlock | DataBlock]:
        """Convert a callback's text and media into content blocks.

        Args:
            payload (`dict[str, Any]`): Parsed DingTalk callback data.

        Returns:
            `list[TextBlock | DataBlock]`: Content in callback order.
        """
        message_type = str(payload.get("msgtype") or "")
        if message_type == "text":
            text_value = payload.get("text") or {}
            text = str(text_value.get("content") or "").strip()
            return [TextBlock(text=text)] if text else []
        if message_type not in _MEDIA_MESSAGE_TYPES:
            return []

        raw_content = payload.get("content") or {}
        if not isinstance(raw_content, dict):
            return []
        if message_type == "richText":
            return await self._parse_rich_text(raw_content)

        blocks: list[TextBlock | DataBlock] = []
        recognition = str(raw_content.get("recognition") or "").strip()
        if recognition:
            blocks.append(TextBlock(text=recognition))
        download_code = str(raw_content.get("downloadCode") or "")
        if not download_code:
            return blocks
        file_name, fallback_type = self._media_description(
            message_type,
            raw_content,
        )
        block = await self._download_media(
            download_code,
            file_name,
            fallback_type,
        )
        if block is not None:
            blocks.append(block)
        elif not blocks:
            blocks.append(
                TextBlock(
                    text=f"Unable to download DingTalk file: {file_name}",
                ),
            )
        return blocks

    async def _parse_rich_text(
        self,
        content: dict[str, Any],
    ) -> list[TextBlock | DataBlock]:
        """Preserve text/image order from a rich-text callback."""
        blocks: list[TextBlock | DataBlock] = []
        items = content.get("richText") or []
        if not isinstance(items, list):
            return blocks
        for item in items:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if text:
                blocks.append(TextBlock(text=text))
            download_code = str(item.get("downloadCode") or "")
            if download_code:
                block = await self._download_media(
                    download_code,
                    "image",
                    "image/jpeg",
                )
                if block is not None:
                    blocks.append(block)
        return blocks

    async def _download_media(
        self,
        download_code: str,
        file_name: str,
        fallback_type: str,
    ) -> DataBlock | None:
        """Download one media code and build a base64 data block.

        Args:
            download_code (`str`): DingTalk robot-message download code.
            file_name (`str`): Attachment display name.
            fallback_type (`str`): MIME type used when the server omits it.

        Returns:
            `DataBlock | None`: Downloaded media, or ``None`` on failure.
        """
        result = await self._api().download_media(
            download_code,
            self._config.max_media_bytes,
        )
        if result is None:
            return None
        data, media_type = result
        if not media_type or media_type == "application/octet-stream":
            media_type = fallback_type
        return DataBlock(
            source=Base64Source(
                data=base64.b64encode(data).decode("ascii"),
                media_type=media_type,
            ),
            name=file_name,
        )

    async def _send_data(self, chat_id: str, block: DataBlock) -> bool:
        """Upload and send one base64 data block through OpenAPI.

        Args:
            chat_id (`str`): Encoded DingTalk destination.
            block (`DataBlock`): Agent-produced attachment.

        Returns:
            `bool`: Whether DingTalk accepted the media message.
        """
        if not isinstance(block.source, Base64Source):
            logger.warning("DingTalk cannot send URL data blocks as files")
            return False
        try:
            data = base64.b64decode(block.source.data, validate=True)
        except (binascii.Error, ValueError):
            logger.warning("DingTalk received invalid base64 output")
            return False
        if len(data) > self._config.max_media_bytes:
            logger.warning("DingTalk outbound media exceeds the size limit")
            return False
        media_type = block.source.media_type or "application/octet-stream"
        if media_type.startswith("image/"):
            fallback_name = "image.png"
        else:
            extension = mimetypes.guess_extension(media_type) or ""
            fallback_name = f"file{extension}"
        file_name = self._safe_file_name(block.name or fallback_name)
        return await self._api().send_media(
            chat_id,
            data,
            file_name,
            media_type,
        )

    @staticmethod
    def _media_description(
        message_type: str,
        content: dict[str, Any],
    ) -> tuple[str, str]:
        """Return a safe filename and fallback MIME type for media."""
        if message_type == "picture":
            return "image", "image/jpeg"
        if message_type == "video":
            suffix = str(content.get("videoType") or "mp4").lower()
            return f"video.{suffix}", f"video/{suffix}"
        if message_type == "audio":
            return "audio", "audio/mpeg"
        file_name = DingTalkChannel._safe_file_name(
            str(content.get("fileName") or "file"),
        )
        guessed_type = mimetypes.guess_type(file_name)[0]
        return file_name, guessed_type or "application/octet-stream"

    @staticmethod
    def _safe_file_name(file_name: str) -> str:
        """Strip directory components from a platform filename."""
        return file_name.replace("\\", "/").rsplit("/", 1)[-1] or "file"
