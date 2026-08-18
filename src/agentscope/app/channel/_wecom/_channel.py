# -*- coding: utf-8 -*-
"""WeCom (企业微信) channel — new ChannelBase interface (PoC).

Receives messages through the WeCom callback URL (HTTP, AES-encrypted
payloads) and sends replies through the WeCom ``message/send`` API.

This is a **proof-of-concept** implementation: text send/receive is wired
end to end, matching the :class:`~agentscope.app.channel.ChannelBase`
contract. Image/file/media sending and interactive approval cards are
deliberately left out — the integration point is proven and the missing
pieces are mechanical.

Requires ``aiohttp`` (callback web server) and ``httpx`` (API client) at
runtime; both are imported lazily so the module loads without them until a
WeCom channel actually starts. AES decryption relies on ``pycryptodome``.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import struct
import time
import xml.etree.ElementTree as ET
from typing import Any, AsyncIterator, Awaitable, Callable, TYPE_CHECKING

from pydantic import BaseModel, Field

from ...._logging import logger
from ....event import ReplyEndEvent, RequireUserConfirmEvent
from ....message import DataBlock, Msg, TextBlock
from .._base import (
    ChannelBase,
    ChannelCapability,
    ChannelConfirmationResultEvent,
    ChannelEvent,
    ChannelStatus,
    _EVENT_ADAPTER,
)

try:  # pragma: no cover - optional dependency
    from Crypto.Cipher import AES
except Exception:  # pylint: disable=broad-except
    AES = None  # type: ignore[assignment]

if TYPE_CHECKING:
    import httpx

_API = "https://qyapi.weixin.qq.com/cgi-bin"
# WeCom errcodes that mean "access_token invalid/expired — refresh & retry".
_TOKEN_EXPIRED_CODES = frozenset({40014, 41001, 42001})
# Give up (let the dispatcher disable the channel) after this many connects
# that never came up — the credentials are bad.
_MAX_CONNECT_ATTEMPTS = 2


class WeComChannel(ChannelBase):
    """WeCom (Enterprise WeChat) platform channel — callback mode (PoC)."""

    channel_type = "wecom"
    display_name = "WeCom (企业微信)"
    description = "Enterprise WeChat bot via callback URL (proof of concept)."
    icon_url = (
        "https://www.google.com/s2/favicons?domain=work.weixin.qq.com&sz=128"
    )
    platform_bot_id_field = "corpid"

    class Credentials(BaseModel):
        """WeCom application credentials."""

        corpid: str = Field(title="Corp ID", description="WeCom Corp ID")
        corpsecret: str = Field(
            title="Corp Secret",
            description="WeCom app Corp Secret",
            json_schema_extra={"format": "password"},
        )
        agent_id: int = Field(
            title="Agent ID",
            description="WeCom application Agent ID",
        )
        token: str = Field(
            title="Callback Token",
            description="WeCom callback Token",
            json_schema_extra={"format": "password"},
        )
        encoding_aes_key: str = Field(
            title="EncodingAESKey",
            description="WeCom EncodingAESKey (43 characters)",
            json_schema_extra={"format": "password"},
        )

    class Config(BaseModel):
        """WeCom platform options."""

        host: str = Field(
            default="0.0.0.0",
            title="Callback bind host",
            description="Host the callback web server binds to.",
        )
        port: int = Field(
            default=8081,
            title="Callback bind port",
            description="Port the callback web server listens on.",
        )
        callback_path: str = Field(
            default="/wecom/callback",
            title="Callback path",
            description="URL path WeCom posts callbacks to.",
        )
        only_at_reply: bool = Field(
            default=True,
            title="Reply only when mentioned",
            description="In group chats, reply only when the bot is "
            "@mentioned (PoC: best-effort, see note).",
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
        image=False,
        file=False,
        interactive=False,
        streaming=False,
        max_message_length=2000,
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
                Validated corpid / secret / agent id / callback token / key.
            config (`WeComChannel.Config`):
                Validated platform options.
        """
        self._channel_id = channel_id
        self._corpid = credentials.corpid
        self._corpsecret = credentials.corpsecret
        self._agent_id = credentials.agent_id
        self._cb_token = credentials.token
        self._aes_key = credentials.encoding_aes_key
        self._config = config
        self.status = ChannelStatus()
        self._http: "httpx.AsyncClient | None" = None
        self._access_token: str | None = None
        self._stop = False
        self._runner = None

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
        """Start the callback web server and keep the access token fresh.

        Args:
            emit (`Callable`): Gateway callback for inbound events.
        """
        import aiohttp
        import httpx

        self._emit = emit
        self.status.state = "connecting"
        self._http = httpx.AsyncClient(timeout=30.0)
        app = aiohttp.web.Application()
        app.router.add_get(self._config.callback_path, self._handle_verify)
        app.router.add_post(
            self._config.callback_path,
            self._handle_callback,
        )
        self._runner = aiohttp.web.AppRunner(app)
        await self._runner.setup()
        site = aiohttp.web.TCPSite(
            self._runner,
            self._config.host,
            self._config.port,
        )
        await site.start()
        self.status.state = "connected"
        logger.info(
            "WeCom callback listening on %s:%s%s",
            self._config.host,
            self._config.port,
            self._config.callback_path,
        )
        try:
            while not self._stop:
                await asyncio.sleep(5.0)
                if self._access_token is None:
                    await self._refresh_token()
        finally:
            self.status.state = "stopped"
            await self._runner.cleanup()
            self._runner = None
            if self._http is not None:
                await self._http.aclose()
                self._http = None

    def stop(self) -> None:
        """Signal the receive loop to tear down."""
        self._stop = True

    # -- Inbound (WeCom → gateway) --

    async def _handle_verify(self, request: "Any") -> "Any":
        """Respond to WeCom's URL verification handshake (GET)."""
        import aiohttp

        params = request.query
        echo = params.get("echostr", "")
        if not self._verify_signature(
            params.get("timestamp", ""),
            params.get("nonce", ""),
            echo,
            params.get("msg_signature", ""),
        ):
            return aiohttp.web.Response(text="", status=403)
        plain, receive_id = self._decrypt(echo)
        if receive_id != self._corpid:
            return aiohttp.web.Response(text="", status=403)
        return aiohttp.web.Response(text=plain.decode("utf-8", "ignore"))

    async def _handle_callback(self, request: "Any") -> "Any":
        """Receive an encrypted message callback (POST) and emit it."""
        import aiohttp

        params = request.query
        body = await request.text()
        try:
            outer = ET.fromstring(body)
        except ET.ParseError:
            return aiohttp.web.Response(text="success")
        encrypt = outer.findtext("Encrypt") or ""
        if not self._verify_signature(
            params.get("timestamp", ""),
            params.get("nonce", ""),
            encrypt,
            params.get("msg_signature", ""),
        ):
            return aiohttp.web.Response(text="", status=403)
        plain, receive_id = self._decrypt(encrypt)
        if receive_id != self._corpid:
            return aiohttp.web.Response(text="success")
        try:
            inner = ET.fromstring(plain)
        except ET.ParseError:
            return aiohttp.web.Response(text="success")
        msg_type = inner.findtext("MsgType")
        if msg_type != "text":
            # PoC only handles text; acknowledge everything else.
            return aiohttp.web.Response(text="success")
        content = inner.findtext("Content") or ""
        from_user = inner.findtext("FromUserName") or ""
        chat_id_node = inner.findtext("ChatId")
        agent_id = inner.findtext("AgentID")
        msg_id = inner.findtext("MsgId") or ""
        chat_id = f"wecom:{chat_id_node}" if chat_id_node else f"wecom:{from_user}"
        event = ChannelEvent(
            channel_id=self._channel_id,
            channel_user_id=from_user,
            chat_id=chat_id,
            channel_message_id=msg_id,
            content=[TextBlock(text=content)],
            metadata={"agent_id": agent_id, "msg_type": msg_type},
        )
        try:
            await self._emit(event)
        except Exception:  # pylint: disable=broad-except
            logger.debug("WeCom emit failed")
        return aiohttp.web.Response(text="success")

    # -- WeCom crypto (callback payload) --

    def _verify_signature(
        self,
        timestamp: str,
        nonce: str,
        encrypt: str,
        signature: str,
    ) -> bool:
        """sha1 over the sorted [token, timestamp, nonce, encrypt] tuple."""
        expect = hashlib.sha1(
            "".join(sorted([self._cb_token, timestamp, nonce, encrypt])).encode(
                "utf-8",
            ),
        ).hexdigest()
        return expect == signature

    def _decrypt(self, encrypt_b64: str) -> tuple[bytes, str]:
        """AES-256-CBC decrypt a WeCom callback payload.

        Returns:
            `tuple[bytes, str]`: ``(message_xml, receive_id)``.

        Raises:
            `RuntimeError`: If ``pycryptodome`` is unavailable or the
            ciphertext cannot be decrypted.
        """
        if AES is None:
            raise RuntimeError("pycryptodome is required for WeCom decryption")
        key = base64.b64decode(self._aes_key + "=")  # 43 → 44 chars = 32 B
        cipher = AES.new(key, AES.MODE_CBC, key[:16])
        plain = cipher.decrypt(base64.b64decode(encrypt_b64))
        pad = plain[-1]
        plain = plain[:-pad] if 0 < pad <= 32 else plain
        content = plain[16:]  # drop the 16-byte random prefix
        msg_len = struct.unpack(">I", content[:4])[0]
        msg = content[4 : 4 + msg_len]
        receive_id = content[4 + msg_len :].decode("utf-8", "ignore")
        return msg, receive_id

    # -- Outbound (gateway → WeCom) --

    async def send_response(
        self,
        event: ChannelEvent,
        events: AsyncIterator[dict],
    ) -> None:
        """Consume the run's agent-event stream and send it as text.

        Args:
            event (`ChannelEvent`): The send target (chat id).
            events (`AsyncIterator[dict]`): The run's session events.
        """
        reply: Msg | None = None
        async for raw in events:
            evt = _EVENT_ADAPTER.validate_python(raw)
            if isinstance(evt, RequireUserConfirmEvent):
                # PoC does not render interactive approvals.
                logger.info("WeCom PoC skips RequireUserConfirmEvent")
                break
            reply_id = getattr(evt, "reply_id", None)
            if reply_id is not None:
                if reply is None:
                    reply = Msg(name="assistant", role="assistant", content=[])
                    reply.id = reply_id
                reply.append_event(evt)
            if isinstance(evt, ReplyEndEvent):
                break
        blocks = self._render(
            reply,
            show_thinking=self._config.show_thinking,
            show_tool_process=self._config.show_tool_process,
        )
        text = "".join(b.text for b in blocks if isinstance(b, TextBlock))
        if not text:
            return
        for part in self._split_long_message(text):
            await self._send_text(event.channel_user_id, part)

    async def _send_text(self, touser: str, text: str) -> None:
        """Send one text part to ``touser`` via the WeCom message API."""
        await self._api(
            "POST",
            f"{_API}/message/send",
            {
                "touser": touser,
                "msgtype": "text",
                "agentid": self._agent_id,
                "text": {"content": text},
            },
        )

    async def _refresh_token(self) -> None:
        """Fetch a fresh access token and cache it."""
        if self._http is None:
            return
        resp = await self._http.get(
            f"{_API}/gettoken",
            params={"corpid": self._corpid, "corpsecret": self._corpsecret},
        )
        data = resp.json()
        if data.get("errcode") == 0:
            self._access_token = data.get("access_token")
            self.status.last_error = ""
        else:
            self.status.last_error = str(data)
            logger.error("WeCom token refresh failed: %s", data)

    async def _api(
        self,
        method: str,
        url: str,
        body: dict | None = None,
        *,
        _retried: bool = False,
    ) -> dict | None:
        """Authenticated JSON WeCom request; refreshes token once on expiry.

        Args:
            method (`str`): HTTP method.
            url (`str`): The full endpoint URL.
            body (`dict | None`): The JSON body, if any.
            _retried (`bool`): Internal — set on the post-refresh retry.

        Returns:
            `dict | None`: The parsed response, or ``None`` on error.
        """
        if self._http is None or not self._access_token:
            return None
        try:
            resp = await self._http.request(
                method,
                url,
                json=body,
                params={"access_token": self._access_token},
            )
            data = resp.json()
            if data.get("errcode") == 0:
                return data
            if (
                not _retried
                and data.get("errcode") in _TOKEN_EXPIRED_CODES
            ):
                await self._refresh_token()
                return await self._api(method, url, body, _retried=True)
            logger.warning("WeCom API %s failed: %s", method, data)
            return data
        except Exception:  # pylint: disable=broad-except
            logger.debug("WeCom API %s request failed", method)
            return None
