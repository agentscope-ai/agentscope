# -*- coding: utf-8 -*-
"""WhatsApp Business Cloud API channel.

Meta delivers inbound messages through HTTPS webhooks rather than a gateway
socket.  ``handle_webhook`` is intentionally public so an embedding service
can mount Meta's GET verification and POST callback on its own FastAPI app;
the channel's listener consumes the normalised events from an internal queue.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import mimetypes
from collections import OrderedDict
from typing import (
    AsyncIterator,
    Awaitable,
    Callable,
    Iterator,
    TYPE_CHECKING,
)

from pydantic import AliasChoices, BaseModel, Field

from ...._logging import logger
from ....event import ReplyEndEvent, RequireUserConfirmEvent
from ....message import Base64Source, DataBlock, Msg, TextBlock, URLSource
from .._base import (
    ChannelBase,
    ChannelCapability,
    ChannelConfirmationResultEvent,
    ChannelEvent,
    ChannelStatus,
    ChatKind,
    _EVENT_ADAPTER,
)

if TYPE_CHECKING:
    import httpx
    from ....tool import ToolBase
    from ....workspace import WorkspaceBase


class WhatsAppAPIError(RuntimeError):
    """A WhatsApp Graph API request was rejected or returned an error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code


class _WhatsAppMediaDownloadError(RuntimeError):
    """A webhook media attachment could not be downloaded."""


class WhatsAppChannel(ChannelBase):
    """WhatsApp Business Platform Cloud API adapter."""

    channel_type = "whatsapp"
    display_name = "WhatsApp"
    description = "WhatsApp Business Cloud API text and media channel."
    icon_url = (
        "https://www.google.com/s2/favicons?domain=web.whatsapp.com&sz=128"
    )
    platform_bot_id_field = "phone_number_id"

    class Credentials(BaseModel):
        """Credentials required by the WhatsApp Cloud API."""

        phone_number_id: str = Field(title="Phone Number ID")
        access_token: str = Field(
            title="Access Token",
            json_schema_extra={"format": "password"},
        )
        meta_app_secret: str = Field(
            title="Meta App Secret",
            description="Used to verify X-Hub-Signature-256 webhook headers.",
            validation_alias=AliasChoices("meta_app_secret", "app_secret"),
            json_schema_extra={"format": "password"},
        )
        verify_token: str = Field(
            title="Webhook Verify Token",
            json_schema_extra={"format": "password"},
        )

    class Config(BaseModel):
        """Non-secret WhatsApp channel options."""

        api_version: str = Field(default="v25.0", title="Graph API version")
        show_tool_process: bool = Field(
            default=False,
            title="Show tool process",
        )
        show_thinking: bool = Field(default=False, title="Show thinking")

    capabilities = ChannelCapability(
        text=True,
        markdown=False,
        image=True,
        file=True,
        interactive=True,
        max_message_length=4096,
    )

    def __init__(
        self,
        channel_id: str,
        credentials: "WhatsAppChannel.Credentials",
        config: "WhatsAppChannel.Config",
    ) -> None:
        """Initialize the channel from validated credentials and options."""
        self._channel_id = channel_id
        self._phone_number_id = credentials.phone_number_id
        self._access_token = credentials.access_token
        self._app_secret = credentials.meta_app_secret
        self._verify_token = credentials.verify_token
        self._config = config
        self.status = ChannelStatus()
        self._http: "httpx.AsyncClient | None" = None
        self._queue: asyncio.Queue[
            ChannelEvent | ChannelConfirmationResultEvent
        ] = asyncio.Queue()
        self._message_ids: OrderedDict[str, None] = OrderedDict()
        self._processing_message_ids: set[str] = set()
        # Process-local cache. Shared webhook consumers hydrate it from the
        # message-bus seen-chat registry before management/send operations.
        self._seen: dict[str, dict[str, str]] = {}

    @property
    def channel_id(self) -> str:
        return self._channel_id

    def verify_webhook_signature(
        self,
        raw_body: bytes,
        signature_header: str | None,
    ) -> bool:
        """Validate Meta's ``X-Hub-Signature-256`` header.

        The app secret is required by :class:`Credentials`, so webhook POSTs
        always fail closed when the signature is absent or malformed.
        """
        if not signature_header or not signature_header.startswith("sha256="):
            return False
        expected = hmac.new(
            self._app_secret.encode(),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature_header[7:], expected)

    def verify_webhook(
        self,
        mode: str,
        token: str,
        challenge: str,
    ) -> str | None:
        """Return Meta's challenge when a webhook verification is valid."""
        return (
            challenge
            if mode == "subscribe" and token == self._verify_token
            else None
        )

    async def start_listening(
        self,
        emit: Callable[
            [ChannelEvent | ChannelConfirmationResultEvent],
            Awaitable[None],
        ],
    ) -> None:
        """Consume events queued by the embedding service's webhook route."""
        import httpx

        self._http = httpx.AsyncClient(timeout=30.0)
        self.status.state = "connected"
        self.status.last_error = ""
        try:
            while True:
                await emit(await self._queue.get())
        except asyncio.CancelledError:  # pylint: disable=try-except-raise
            raise
        except Exception as exc:  # pylint: disable=broad-except
            self.status.state = "failed"
            self.status.last_error = str(exc)
            logger.exception("WhatsApp webhook listener failed")
        finally:
            self.status.state = "stopped"
            if self._http:
                await self._http.aclose()
                self._http = None

    @staticmethod
    def _webhook_messages(
        payload: dict,
    ) -> Iterator[tuple[dict, dict]]:
        """Yield ``(message, value)`` pairs from a Meta webhook payload."""
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for message in value.get("messages", []):
                    yield message, value

    async def normalize_webhook(
        self,
        payload: dict,
    ) -> list[ChannelEvent | ChannelConfirmationResultEvent]:
        """Normalize webhook messages without requiring a local listener.

        The normal channel listener owns its HTTP client. A shared webhook
        consumer may instead create a short-lived channel on a different
        worker, so this method owns a temporary client when necessary.
        """
        import httpx

        owns_http = self._http is None
        if owns_http:
            self._http = httpx.AsyncClient(timeout=30.0)
        try:
            events: list[ChannelEvent | ChannelConfirmationResultEvent] = []
            for message, value in self._webhook_messages(payload):
                event = await self._normalize_message(message, value)
                if event is not None:
                    events.append(event)
            return events
        finally:
            if owns_http and self._http:
                await self._http.aclose()
                self._http = None

    async def handle_webhook(self, payload: dict) -> int:
        """Parse a Meta webhook payload and enqueue supported messages.

        The embedding HTTP handler should validate Meta's ``X-Hub-Signature``
        before calling this method. Duplicate message ids are ignored.
        """
        count = 0
        for message, value in self._webhook_messages(payload):
            message_id = str(message.get("id", ""))
            if message_id and not self._claim_message(message_id):
                continue
            try:
                event = await self._normalize_message(message, value)
                if event:
                    if isinstance(event, ChannelEvent):
                        self.observe_chat(
                            event.chat_id,
                            str(event.metadata.get("chat_type", "")),
                            event.chat_name,
                        )
                    await self._queue.put(event)
                    if message_id:
                        self._remember_message(message_id)
                    count += 1
            except _WhatsAppMediaDownloadError:
                logger.warning(
                    "WhatsApp media message %s could not be normalized",
                    message_id,
                )
            finally:
                self._processing_message_ids.discard(message_id)
        return count

    def _claim_message(self, message_id: str) -> bool:
        """Claim an unseen message while it is being normalized."""
        if message_id in self._message_ids:
            self._message_ids.move_to_end(message_id)
            return False
        if message_id in self._processing_message_ids:
            return False
        self._processing_message_ids.add(message_id)
        return True

    def _remember_message(self, message_id: str) -> None:
        """Record a handled message in a bounded process-local cache."""
        self._message_ids[message_id] = None
        if len(self._message_ids) > 10_000:
            self._message_ids.popitem(last=False)

    async def _normalize_message(
        self,
        message: dict,
        value: dict,
    ) -> ChannelEvent | ChannelConfirmationResultEvent | None:
        sender = str(message.get("from") or message.get("from_user_id") or "")
        if not sender:
            logger.warning("WhatsApp message has no sender identifier")
            return None
        chat_id, chat_type, chat_name = self._conversation_context(
            message,
            value,
            sender,
        )
        msg_type = message.get("type")
        decision = self._confirmation_event(
            message,
            sender,
            chat_id,
        )
        if decision is not None:
            return decision
        content: list[TextBlock | DataBlock] = []
        if msg_type == "text":
            text = message.get("text", {}).get("body", "").strip()
            if text:
                content = [TextBlock(text=text)]
        elif msg_type in {"image", "document", "audio", "video"}:
            media = message.get(msg_type, {})
            caption = str(media.get("caption") or "").strip()
            block = await self._download_media(
                media.get("id", ""),
                media.get("mime_type", "application/octet-stream"),
                media.get("filename", msg_type),
            )
            if block is None:
                raise _WhatsAppMediaDownloadError(
                    f"Failed to download WhatsApp media {media.get('id', '')}",
                )
            if caption:
                content.append(TextBlock(text=caption))
            content.append(block)
        if not content:
            return None
        profile = (value.get("contacts") or [{}])[0]
        return ChannelEvent(
            channel_id=self._channel_id,
            channel_user_id=sender,
            channel_user_name=profile.get("profile", {}).get("name", ""),
            chat_id=chat_id,
            chat_name=chat_name,
            channel_message_id=message.get("id"),
            content=content,
            metadata={
                "chat_type": chat_type,
                "chat_name": chat_name,
                "display_phone_number": value.get("metadata", {}).get(
                    "display_phone_number",
                    "",
                ),
            },
        )

    @staticmethod
    def _conversation_context(
        message: dict,
        value: dict,
        sender: str,
    ) -> tuple[str, str, str]:
        """Return ``(chat_id, chat_type, chat_name)`` for a webhook.

        Direct messages use the sender's WhatsApp id. Groups API webhook
        messages carry ``group_id`` on the message; ``value.group_id`` is
        accepted for compatibility with webhook payload revisions.
        """
        candidates = (
            message.get("group_id"),
            value.get("group_id"),
        )
        group_id = next((str(item) for item in candidates if item), "")
        if not group_id:
            return sender, "private", ""

        group = message.get("group") or value.get("group") or {}
        if not isinstance(group, dict):
            group = {}
        group_name = str(
            group.get("name")
            or message.get("group_name")
            or value.get("group_name")
            or "",
        )
        return group_id, "group", group_name

    def observe_chat(self, chat_id: str, chat_type: str, name: str) -> None:
        """Cache chat metadata supplied by a retained/shared state source."""
        if not chat_id:
            return
        current = self._seen.get(chat_id, {})
        self._seen[chat_id] = {
            "chat_type": chat_type or current.get("chat_type", "private"),
            "name": name or current.get("name", chat_id),
        }

    def _confirmation_event(
        self,
        message: dict,
        sender: str,
        chat_id: str,
    ) -> ChannelConfirmationResultEvent | None:
        """Parse an approval button or text command from an inbound message."""
        command = ""
        if message.get("type") == "interactive":
            interactive = message.get("interactive") or {}
            command = str(
                (interactive.get("button_reply") or {}).get("id", ""),
            )
        elif message.get("type") == "text":
            command = str((message.get("text") or {}).get("body", "")).strip()

        parts = command.split(maxsplit=1)
        if len(parts) != 2 or parts[0] not in {
            "agentscope:approve",
            "agentscope:deny",
            "/approve",
            "/deny",
        }:
            return None
        try:
            raw = base64.urlsafe_b64decode(parts[1] + "===")
            target = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        tool_call_id = str(target.get("tool_call_id", ""))
        if not tool_call_id:
            return None
        return ChannelConfirmationResultEvent(
            channel_id=self._channel_id,
            chat_id=chat_id,
            channel_user_id=sender,
            agent_id=str(target.get("agent_id", "")),
            session_id=str(target.get("session_id", "")),
            tool_call_id=tool_call_id,
            approved=parts[0] in {"agentscope:approve", "/approve"},
            actor=sender,
        )

    @staticmethod
    def _confirmation_token(
        tool_call_id: str,
        agent_id: str,
        session_id: str,
    ) -> str:
        """Encode the authoritative lookup keys into a button-safe token."""
        raw = json.dumps(
            {
                "tool_call_id": tool_call_id,
                "agent_id": agent_id,
                "session_id": session_id,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    async def send_response(
        self,
        event: ChannelEvent,
        events: AsyncIterator[dict],
    ) -> None:
        reply: Msg | None = None
        confirm: RequireUserConfirmEvent | None = None
        async for raw in events:
            evt = _EVENT_ADAPTER.validate_python(raw)
            if isinstance(evt, RequireUserConfirmEvent):
                confirm = evt
                break
            if getattr(evt, "reply_id", None) is not None:
                if reply is None:
                    reply = Msg(name="assistant", role="assistant", content=[])
                    reply.id = evt.reply_id
                reply.append_event(evt)
            if isinstance(evt, ReplyEndEvent):
                break
        for block in self._render(
            reply,
            show_thinking=self._config.show_thinking,
            show_tool_process=self._config.show_tool_process,
        ):
            recipient_type = self._recipient_type(
                event.chat_id,
                event.metadata,
            )
            if isinstance(block, TextBlock):
                for part in self._split_long_message(block.text):
                    await self._send_json(
                        {
                            "messaging_product": "whatsapp",
                            "recipient_type": recipient_type,
                            "to": event.chat_id,
                            "type": "text",
                            "text": {"body": part},
                        },
                    )
            elif isinstance(block, DataBlock):
                data, name = await self._data_bytes(block)
                if data:
                    if block.source.media_type.startswith("image/"):
                        await self.send_image_to(
                            event.chat_id,
                            recipient_type,
                            data,
                            name,
                        )
                    else:
                        await self.send_file_to(
                            event.chat_id,
                            recipient_type,
                            data,
                            name,
                        )
        if confirm:
            await self._present_confirm(event, confirm)

    async def _present_confirm(
        self,
        event: ChannelEvent,
        request: RequireUserConfirmEvent,
    ) -> None:
        """Present approval buttons in DMs and text commands in groups."""
        recipient_type = self._recipient_type(event.chat_id, event.metadata)
        for tool in request.tool_calls:
            token = self._confirmation_token(
                tool.id,
                str(event.metadata.get("agent_id", "")),
                str(event.metadata.get("session_id", "")),
            )
            prompt = (
                "Tool execution needs approval\n"
                f"Tool: {tool.name}\nArguments: {str(tool.input)[:800]}"
            )
            if recipient_type == "group":
                body = {
                    "messaging_product": "whatsapp",
                    "recipient_type": "group",
                    "to": event.chat_id,
                    "type": "text",
                    "text": {
                        "body": (
                            f"{prompt}\nReply `/approve {token}` or "
                            f"`/deny {token}`."
                        ),
                    },
                }
            else:
                body = {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": event.chat_id,
                    "type": "interactive",
                    "interactive": {
                        "type": "button",
                        "body": {"text": prompt},
                        "action": {
                            "buttons": [
                                {
                                    "type": "reply",
                                    "reply": {
                                        "id": f"agentscope:approve {token}",
                                        "title": "Approve",
                                    },
                                },
                                {
                                    "type": "reply",
                                    "reply": {
                                        "id": f"agentscope:deny {token}",
                                        "title": "Deny",
                                    },
                                },
                            ],
                        },
                    },
                }
            await self._send_json(body)

    async def list_bot_chats(self) -> list[dict]:
        """List active API groups plus DMs observed through webhooks."""
        chats: dict[str, dict] = {
            chat_id: {
                "chat_id": chat_id,
                "name": info.get("name") or chat_id,
                "chat_type": info.get("chat_type", "private"),
            }
            for chat_id, info in self._seen.items()
        }
        after = ""
        while self._http:
            params: dict[str, str | int] = {"limit": 100}
            if after:
                params["after"] = after
            payload = await self._get_json(self._url("groups"), params=params)
            if not payload:
                break
            data = payload.get("data", {})
            groups = data.get("groups", []) if isinstance(data, dict) else data
            for group in groups:
                group_id = str(group.get("id", ""))
                if not group_id:
                    continue
                name = str(group.get("subject", "")) or group_id
                self.observe_chat(group_id, "group", name)
                chats[group_id] = {
                    "chat_id": group_id,
                    "name": name,
                    "chat_type": "group",
                }
            after = str(
                payload.get("paging", {}).get("cursors", {}).get("after", ""),
            )
            if not after:
                break
        return list(chats.values())

    async def list_chat_members(self, chat_id: str) -> list[dict]:
        """Return participants from the Groups API group-info endpoint."""
        payload = await self._get_json(
            self._media_url(chat_id),
            params={"fields": "participants"},
        )
        if not payload:
            return []
        return [
            {
                "id": str(
                    member.get("wa_id")
                    or member.get("user_id")
                    or member.get("username")
                    or "",
                ),
                "name": str(
                    member.get("username") or member.get("wa_id") or "",
                ),
            }
            for member in payload.get("participants", [])
            if isinstance(member, dict)
        ]

    async def chat_kind(self, chat_id: str) -> ChatKind | None:
        """Classify a chat observed through an inbound webhook."""
        if not chat_id:
            return None
        info = self._seen.get(chat_id, {})
        return (
            ChatKind.GROUP
            if info.get("chat_type") == "group"
            else ChatKind.PRIVATE
        )

    async def chat_name(self, chat_id: str) -> str:
        """Return the best known display name for an observed chat."""
        return self._seen.get(chat_id, {}).get("name", "")

    def _recipient_type(self, chat_id: str, metadata: dict) -> str:
        """Resolve the Graph API recipient type for a send target."""
        chat_type = str(metadata.get("chat_type", ""))
        if not chat_type:
            chat_type = self._seen.get(chat_id, {}).get("chat_type", "")
        return "group" if chat_type == "group" else "individual"

    async def list_tools(self, workspace: "WorkspaceBase") -> list["ToolBase"]:
        """Expose WhatsApp discovery and outbound media tools."""
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

    async def send_message_to(
        self,
        to: str,
        recipient_type: str,
        text: str,
    ) -> dict | None:
        """Send text to an individual or Groups API group id."""
        return await self._send_json(
            {
                "messaging_product": "whatsapp",
                "recipient_type": recipient_type,
                "to": to,
                "type": "text",
                "text": {"body": text},
            },
        )

    async def send_file_to(
        self,
        to: str,
        recipient_type: str,
        data: bytes,
        file_name: str,
    ) -> dict | None:
        """Upload and send a document to an individual or group."""
        media_id = await self._upload(data, file_name)
        return (
            await self._send_json(
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": recipient_type,
                    "to": to,
                    "type": "document",
                    "document": {"id": media_id, "filename": file_name},
                },
            )
            if media_id
            else None
        )

    async def send_image_to(
        self,
        to: str,
        recipient_type: str,
        data: bytes,
        file_name: str = "image",
    ) -> dict | None:
        """Upload and send an image to an individual or group."""
        media_id = await self._upload(data, file_name)
        return (
            await self._send_json(
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": recipient_type,
                    "to": to,
                    "type": "image",
                    "image": {"id": media_id},
                },
            )
            if media_id
            else None
        )

    async def _send_json(self, body: dict) -> dict | None:
        if not self._http:
            return None
        try:
            response = await self._http.post(
                self._url("messages"),
                headers={"Authorization": f"Bearer {self._access_token}"},
                json=body,
            )
            try:
                payload = response.json()
            except Exception as exc:  # pylint: disable=broad-except
                status_code = getattr(response, "status_code", "unknown")
                message = (
                    "WhatsApp API returned invalid JSON "
                    f"(HTTP status {status_code})."
                )
                self.status.last_error = message
                raise WhatsAppAPIError(
                    message,
                    status_code=getattr(response, "status_code", None),
                ) from exc
            try:
                response.raise_for_status()
            except Exception as exc:  # pylint: disable=broad-except
                message = self._api_error_message(payload, response)
                self.status.last_error = message
                raise WhatsAppAPIError(
                    message,
                    status_code=getattr(response, "status_code", None),
                ) from exc
            if isinstance(payload, dict) and payload.get("error"):
                message = self._api_error_message(payload, response)
                self.status.last_error = message
                raise WhatsAppAPIError(
                    message,
                    status_code=getattr(response, "status_code", None),
                )
            if not isinstance(payload, dict):
                status_code = getattr(response, "status_code", "unknown")
                message = (
                    "WhatsApp API returned a non-object response "
                    f"(HTTP status {status_code})."
                )
                self.status.last_error = message
                raise WhatsAppAPIError(
                    message,
                    status_code=getattr(response, "status_code", None),
                )
            return payload
        except WhatsAppAPIError:
            logger.exception("WhatsApp API request failed")
            raise
        except Exception as exc:  # pylint: disable=broad-except
            self.status.last_error = str(exc)
            logger.exception("WhatsApp API request failed")
            raise WhatsAppAPIError(str(exc)) from exc

    @staticmethod
    def _api_error_message(payload: object, response: object) -> str:
        """Build a safe, useful message from a Graph API response."""
        error = payload.get("error") if isinstance(payload, dict) else None
        status_code = getattr(response, "status_code", "unknown")
        if isinstance(error, dict):
            code = error.get("code")
            message = str(error.get("message") or "Graph API request failed")
            if code is not None:
                message = f"Graph API error {code}: {message}"
            return f"HTTP status {status_code}: {message}"
        return f"Graph API request failed with HTTP status {status_code}."

    async def _get_json(
        self,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
    ) -> dict | None:
        """Perform an authenticated Graph API GET and return JSON."""
        if not self._http:
            return None
        try:
            response = await self._http.get(
                url,
                headers={"Authorization": f"Bearer {self._access_token}"},
                params=params,
            )
            response.raise_for_status()
            return response.json()
        except Exception:  # pylint: disable=broad-except
            logger.exception("WhatsApp API GET failed")
            return None

    async def _upload(self, data: bytes, file_name: str) -> str | None:
        if not self._http:
            return None
        media_type = (
            mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        )
        try:
            response = await self._http.post(
                self._url("media"),
                headers={"Authorization": f"Bearer {self._access_token}"},
                data={"messaging_product": "whatsapp"},
                files={"file": (file_name, data, media_type)},
            )
            response.raise_for_status()
            return response.json().get("id")
        except Exception:  # pylint: disable=broad-except
            logger.exception("WhatsApp media upload failed")
            return None

    async def _download_media(
        self,
        media_id: str,
        media_type: str,
        name: str,
    ) -> DataBlock | None:
        if not self._http or not media_id:
            return None
        try:
            meta = await self._get_json(self._media_url(media_id))
            url = (meta or {}).get("url")
            if not url:
                return None
            response = await self._http.get(
                url,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            response.raise_for_status()
            return DataBlock(
                source=Base64Source(
                    data=base64.b64encode(response.content).decode(),
                    media_type=media_type,
                ),
                name=name,
            )
        except Exception:  # pylint: disable=broad-except
            logger.exception("WhatsApp media download failed")
            return None

    async def _data_bytes(self, block: DataBlock) -> tuple[bytes | None, str]:
        if isinstance(block.source, Base64Source):
            return (
                base64.b64decode(block.source.data),
                block.name or "attachment",
            )
        if isinstance(block.source, URLSource) and self._http:
            response = await self._http.get(str(block.source.url))
            response.raise_for_status()
            return response.content, block.name or "attachment"
        return None, block.name or "attachment"

    def _url(self, resource: str) -> str:
        return (
            "https://graph.facebook.com/"
            f"{self._config.api_version}/{self._phone_number_id}/{resource}"
        )

    def _media_url(self, media_id: str) -> str:
        return (
            f"https://graph.facebook.com/{self._config.api_version}/{media_id}"
        )
