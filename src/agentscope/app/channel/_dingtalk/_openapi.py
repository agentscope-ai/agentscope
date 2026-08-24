# -*- coding: utf-8 -*-
"""Minimal asynchronous DingTalk OpenAPI client for channel media."""

import asyncio
import ipaddress
import json
import time
from typing import Any
from urllib.parse import urlparse
from uuid import uuid1, uuid4

from ...._logging import logger

_API = "https://api.dingtalk.com/v1.0"
_OAPI = "https://oapi.dingtalk.com"
_TOKEN_REFRESH_BUFFER_SECONDS = 300
# Enough of a refusal to name the offending field, not so much that a
# rejected payload is echoed back into the log.
_ERROR_BODY_CHARS = 500
_SUPPORTED_FILE_TYPES = frozenset({"doc", "docx", "pdf", "rar", "xlsx", "zip"})
# An AI card's creation call carries no content: it opens the card in
# a running state, and the update that follows tells it what to render.
# "Done rendering" is about the card's own progress, not about whether
# an approval has been decided. Both go over the wire as strings.
_AI_CARD_RENDERING = "1"
_AI_CARD_RENDERED = "3"


class _DingTalkOpenAPI:
    """Call only the DingTalk OpenAPI operations needed by the channel."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        http: Any,
    ) -> None:
        """Bind application credentials and a shared async HTTP client.

        Args:
            client_id (`str`): DingTalk application Client ID.
            client_secret (`str`): DingTalk application Client Secret.
            http (`Any`): An ``httpx.AsyncClient``-compatible object.
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def download_media(
        self,
        download_code: str,
        max_bytes: int,
    ) -> tuple[bytes, str] | None:
        """Resolve and download one robot-message attachment.

        Args:
            download_code (`str`): Code from the inbound message content.
            max_bytes (`int`): Maximum accepted response size.

        Returns:
            `tuple[bytes, str] | None`: Bytes and response media type, or
            ``None`` when DingTalk rejects the request or it is too large.
        """
        token = await self._access_token()
        if token is None:
            return None
        try:
            response = await self._http.post(
                f"{_API}/robot/messageFiles/download",
                headers=self._headers(token),
                json={
                    "robotCode": self._client_id,
                    "downloadCode": download_code,
                },
            )
            response.raise_for_status()
            result = response.json()
            download_url = self._safe_download_url(
                str(result.get("downloadUrl") or ""),
            )
            if download_url is None:
                logger.warning("DingTalk returned an unsafe media URL")
                return None
            return await self._download_bytes(download_url, max_bytes)
        except Exception:  # pylint: disable=broad-except
            logger.exception("DingTalk media URL resolution failed")
            return None

    async def send_media(
        self,
        chat_id: str,
        data: bytes,
        file_name: str,
        media_type: str,
    ) -> bool:
        """Upload and send an image or file to an encoded chat target.

        Args:
            chat_id (`str`): ``group:<openConversationId>`` or
                ``user:<staffId>``.
            data (`bytes`): Media bytes.
            file_name (`str`): Display filename.
            media_type (`str`): MIME type.

        Returns:
            `bool`: Whether DingTalk accepted both upload and send calls.
        """
        is_image = media_type.startswith("image/")
        suffix = file_name.rsplit(".", 1)[-1].lower()
        if not is_image and suffix not in _SUPPORTED_FILE_TYPES:
            logger.warning(
                "DingTalk does not support outbound '.%s' files",
                suffix,
            )
            return False
        media_id = await self._upload_media(
            data,
            file_name,
            media_type,
            "image" if is_image else "file",
        )
        if media_id is None:
            return False
        if is_image:
            msg_key = "sampleImageMsg"
            msg_param = {"photoURL": media_id}
        else:
            msg_key = "sampleFile"
            msg_param = {
                "mediaId": media_id,
                "fileName": file_name,
                "fileType": suffix,
            }
        return await self._send_message(chat_id, msg_key, msg_param)

    async def send_text(self, chat_id: str, text: str) -> bool:
        """Send Markdown-formatted text to an encoded target.

        Args:
            chat_id (`str`): ``group:<openConversationId>`` or
                ``user:<staffId>``.
            text (`str`): Markdown-formatted message body.

        Returns:
            `bool`: Whether DingTalk accepted the message.
        """
        return await self._send_message(
            chat_id,
            "sampleMarkdown",
            {"title": "AgentScope", "text": text},
        )

    async def create_approval_card(
        self,
        chat_id: str,
        approver_id: str,
        template_id: str,
        card_data: dict[str, str],
        out_track_id: str = "",
    ) -> str | None:
        """Create and deliver one Stream-callback approval card.

        Args:
            chat_id (`str`): Encoded DingTalk user or group target.
            approver_id (`str`): Optional user permitted to see and decide
                the card. Empty means normal audience visibility.
            template_id (`str`): Card Platform template identifier.
            card_data (`dict[str, str]`): Template parameter map.
            out_track_id (`str`): Tracking id to pin on the card; a random
                one when empty.

        Returns:
            `str | None`: The card's outbound tracking id, or ``None`` when
            creation or delivery fails.
        """
        track = await self._create_and_deliver_card(
            chat_id,
            template_id,
            {"flowStatus": _AI_CARD_RENDERING},
            approver_id,
            out_track_id,
        )
        if track is None:
            return None
        settled = await self.update_approval_card(
            track,
            {**card_data, "flowStatus": _AI_CARD_RENDERED},
        )
        return track if settled else None

    async def create_streaming_card(
        self,
        chat_id: str,
        template_id: str,
        content_key: str,
    ) -> str | None:
        """Create and deliver an AI streaming card.

        Args:
            chat_id (`str`): Encoded DingTalk user or group target.
            template_id (`str`): AI Card template identifier.
            content_key (`str`): Template streaming-component variable key.

        Returns:
            `str | None`: Outbound tracking id, or ``None`` on failure.
        """
        return await self._create_and_deliver_card(
            chat_id,
            template_id,
            {content_key: ""},
        )

    async def stream_card(
        self,
        out_track_id: str,
        content_key: str,
        content: str,
        *,
        finalize: bool = False,
        is_error: bool = False,
    ) -> bool:
        """Update an AI Card streaming component with full content.

        Args:
            out_track_id (`str`): Tracking id returned by card creation.
            content_key (`str`): Template streaming-component variable key.
            content (`str`): Complete Markdown content for this update.
            finalize (`bool`): Whether this is the final update.
            is_error (`bool`): Whether the card should enter error state.

        Returns:
            `bool`: Whether DingTalk accepted the streaming update.
        """
        token = await self._access_token()
        if token is None:
            return False
        return await self._request(
            "PUT",
            f"{_API}/card/streaming",
            token,
            {
                "outTrackId": out_track_id,
                "guid": str(uuid1()),
                "key": content_key,
                "content": content,
                "isFull": True,
                "isFinalize": finalize,
                "isError": is_error,
            },
            "card streaming update",
        )

    async def update_approval_card(
        self,
        out_track_id: str,
        card_data: dict[str, str],
    ) -> bool:
        """Replace approval-card template parameters after a decision.

        Args:
            out_track_id (`str`): Tracking id returned by card creation.
            card_data (`dict[str, str]`): Resolved template parameter map.

        Returns:
            `bool`: Whether DingTalk accepted the update.
        """
        token = await self._access_token()
        if token is None:
            return False
        return await self._request(
            "PUT",
            f"{_API}/card/instances",
            token,
            {
                "outTrackId": out_track_id,
                "cardData": {"cardParamMap": card_data},
                # Without this the map replaces the card's whole data, so
                # every variable this update does not name comes back
                # empty — the tool and its arguments included.
                "cardUpdateOptions": {"updateCardDataByKey": True},
            },
            "card update",
        )

    async def _create_and_deliver_card(
        self,
        chat_id: str,
        template_id: str,
        card_data: dict[str, str],
        recipient_id: str = "",
        out_track_id: str = "",
    ) -> str | None:
        """Create a Card Platform instance and deliver it to a target."""
        if chat_id.startswith("group:"):
            conversation_id = chat_id.removeprefix("group:")
            open_space_id = f"dtv1.card//IM_GROUP.{conversation_id}"
            group_model: dict[str, Any] = {"robotCode": self._client_id}
            if recipient_id:
                group_model["recipients"] = [recipient_id]
            delivery_model: dict[str, Any] = {
                "imGroupOpenDeliverModel": group_model,
            }
        elif chat_id.startswith("user:"):
            user_id = chat_id.removeprefix("user:")
            if recipient_id and user_id != recipient_id:
                logger.warning("DingTalk card target does not match user")
                return None
            open_space_id = f"dtv1.card//IM_ROBOT.{user_id}"
            delivery_model = {
                "imRobotOpenDeliverModel": {"spaceType": "IM_ROBOT"},
            }
        else:
            logger.warning("Invalid DingTalk card target")
            return None
        token = await self._access_token()
        if token is None:
            return None
        out_track_id = out_track_id or uuid4().hex
        created = await self._request(
            "POST",
            f"{_API}/card/instances",
            token,
            {
                "cardTemplateId": template_id,
                "outTrackId": out_track_id,
                "cardData": {"cardParamMap": card_data},
                "callbackType": "STREAM",
                "imGroupOpenSpaceModel": {"supportForward": False},
                "imRobotOpenSpaceModel": {"supportForward": False},
            },
            "card creation",
        )
        if not created:
            return None
        delivered = await self._request(
            "POST",
            f"{_API}/card/instances/deliver",
            token,
            {
                "outTrackId": out_track_id,
                "openSpaceId": open_space_id,
                "userIdType": 1,
                **delivery_model,
            },
            "card delivery",
        )
        return out_track_id if delivered else None

    async def search_users(
        self,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Search enterprise users visible to the application.

        DingTalk's search endpoint returns user ids only. This method
        resolves the matching profiles through the user-detail endpoint so
        callers can disambiguate people with similar names.

        Args:
            query (`str`): User-name search term.
            limit (`int`): Maximum number of users to return.

        Returns:
            `list[dict[str, Any]]`: Visible user ids and basic profiles.
        """
        token = await self._access_token()
        if token is None:
            return []
        try:
            response = await self._http.post(
                f"{_API}/contact/users/search",
                headers=self._headers(token),
                json={
                    "queryWord": query,
                    "offset": 0,
                    "size": limit,
                },
            )
            response.raise_for_status()
            try:
                result = response.json()
            except ValueError:
                result = {}
            if not isinstance(result, dict):
                result = {}
            user_ids = [
                str(user_id) for user_id in result.get("list", []) if user_id
            ][:limit]
        except Exception:  # pylint: disable=broad-except
            logger.exception("DingTalk user search failed")
            return []

        users: list[dict[str, Any]] = []
        for user_id in user_ids:
            detail = await self._user_detail(token, user_id)
            users.append(
                detail
                or {
                    "user_id": user_id,
                    "name": "",
                    "title": "",
                    "department_ids": [],
                },
            )
        return users

    async def _access_token(self) -> str | None:
        """Return a cached application token, refreshing it when needed."""
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token
            try:
                response = await self._http.post(
                    f"{_API}/oauth2/accessToken",
                    json={
                        "appKey": self._client_id,
                        "appSecret": self._client_secret,
                    },
                )
                response.raise_for_status()
                result = response.json()
                token = str(result.get("accessToken") or "")
                expires_in = int(result.get("expireIn") or 0)
                if not token or expires_in <= 0:
                    logger.warning("DingTalk returned an invalid access token")
                    return None
                reserve = min(
                    _TOKEN_REFRESH_BUFFER_SECONDS,
                    max(expires_in // 10, 1),
                )
                self._token = token
                self._token_expires_at = (
                    time.monotonic() + expires_in - reserve
                )
                return token
            except Exception:  # pylint: disable=broad-except
                logger.exception("DingTalk access token request failed")
                return None

    async def _download_bytes(
        self,
        url: str,
        max_bytes: int,
    ) -> tuple[bytes, str] | None:
        """Download a response incrementally with a hard size limit."""
        try:
            async with self._http.stream(
                "GET",
                url,
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                raw_length = response.headers.get("content-length")
                if raw_length and int(raw_length) > max_bytes:
                    logger.warning("DingTalk media exceeds the size limit")
                    return None
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        logger.warning("DingTalk media exceeds the size limit")
                        return None
                    chunks.append(chunk)
                media_type = response.headers.get(
                    "content-type",
                    "application/octet-stream",
                ).split(";", 1)[0]
                return b"".join(chunks), media_type
        except Exception:  # pylint: disable=broad-except
            logger.exception("DingTalk media download failed")
            return None

    async def _upload_media(
        self,
        data: bytes,
        file_name: str,
        media_type: str,
        upload_type: str,
    ) -> str | None:
        """Upload media and return the legacy media id."""
        token = await self._access_token()
        if token is None:
            return None
        try:
            response = await self._http.post(
                f"{_OAPI}/media/upload",
                params={"access_token": token},
                data={"type": upload_type},
                files={"media": (file_name, data, media_type)},
            )
            response.raise_for_status()
            result = response.json()
            if result.get("errcode", 0) != 0:
                logger.warning(
                    "DingTalk media upload rejected: code=%s",
                    result.get("errcode"),
                )
                return None
            media_id = str(result.get("media_id") or "")
            return media_id or None
        except Exception:  # pylint: disable=broad-except
            logger.exception("DingTalk media upload failed")
            return None

    async def _user_detail(
        self,
        token: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        """Resolve one user id through the legacy contact endpoint."""
        try:
            response = await self._http.post(
                f"{_OAPI}/topapi/v2/user/get",
                params={"access_token": token},
                json={"userid": user_id, "language": "zh_CN"},
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("errcode", 0) != 0:
                logger.warning(
                    "DingTalk user detail rejected: code=%s",
                    payload.get("errcode"),
                )
                return None
            result = payload.get("result") or {}
            return {
                "user_id": str(result.get("userid") or user_id),
                "name": str(result.get("name") or ""),
                "title": str(result.get("title") or ""),
                "department_ids": result.get("dept_id_list") or [],
            }
        except Exception:  # pylint: disable=broad-except
            logger.exception("DingTalk user detail request failed")
            return None

    async def _send_message(
        self,
        chat_id: str,
        msg_key: str,
        msg_param: dict[str, str],
    ) -> bool:
        """Send a template message to an encoded user or group target."""
        token = await self._access_token()
        if token is None:
            return False
        target: dict[str, Any]
        if chat_id.startswith("group:"):
            url = f"{_API}/robot/groupMessages/send"
            target = {"openConversationId": chat_id.removeprefix("group:")}
        elif chat_id.startswith("user:"):
            url = f"{_API}/robot/oToMessages/batchSend"
            target = {"userIds": [chat_id.removeprefix("user:")]}
        else:
            logger.warning("Invalid DingTalk message target")
            return False
        body: dict[str, Any] = {
            "robotCode": self._client_id,
            "msgKey": msg_key,
            "msgParam": json.dumps(msg_param, ensure_ascii=False),
            **target,
        }
        try:
            response = await self._http.post(
                url,
                headers=self._headers(token),
                json=body,
            )
            if response.status_code >= 400:
                # The status alone never says which field was wrong;
                # DingTalk puts that in the body.
                logger.warning(
                    "DingTalk rejected %s with HTTP %s: %s",
                    msg_key,
                    response.status_code,
                    response.text[:_ERROR_BODY_CHARS],
                )
                return False
            result = response.json()
            code = result.get("code")
            if code not in (None, "", 0, "0"):
                logger.warning(
                    "DingTalk message send rejected: code=%s",
                    code,
                )
                return False
            return True
        except Exception:  # pylint: disable=broad-except
            logger.exception("DingTalk message send failed")
            return False

    async def _request(
        self,
        method: str,
        url: str,
        token: str,
        body: dict[str, Any],
        operation: str,
    ) -> bool:
        """Issue one authenticated JSON request and check API errors."""
        try:
            response = await self._http.request(
                method,
                url,
                headers=self._headers(token),
                json=body,
            )
            if response.status_code >= 400:
                logger.warning(
                    "DingTalk rejected %s with HTTP %s: %s",
                    operation,
                    response.status_code,
                    response.text[:_ERROR_BODY_CHARS],
                )
                return False
            result = response.json()
            code = result.get("code")
            if code not in (None, "", 0, "0"):
                logger.warning(
                    "DingTalk %s rejected: code=%s",
                    operation,
                    code,
                )
                return False
            return True
        except Exception:  # pylint: disable=broad-except
            logger.exception("DingTalk %s failed", operation)
            return False

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        """Build DingTalk OpenAPI authentication headers."""
        return {
            "Content-Type": "application/json",
            "x-acs-dingtalk-access-token": token,
        }

    @staticmethod
    def _safe_download_url(url: str) -> str | None:
        """Return a safe HTTPS media URL from DingTalk OpenAPI.

        DingTalk currently returns signed Alibaba Cloud OSS URLs with an
        ``http`` scheme. Upgrade only those known OSS hosts to HTTPS; never
        follow arbitrary clear-text download URLs.
        """
        parsed = urlparse(url)
        if not parsed.hostname or parsed.username or parsed.password:
            return None
        hostname = parsed.hostname.lower()
        if hostname == "localhost":
            return None
        try:
            if not ipaddress.ip_address(hostname).is_global:
                return None
        except ValueError:
            pass
        if parsed.scheme == "https":
            return url
        if parsed.scheme == "http" and hostname.endswith(".aliyuncs.com"):
            return parsed._replace(scheme="https").geturl()
        return None
