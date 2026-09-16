# -*- coding: utf-8 -*-
"""Feishu user OAuth used by Wiki tools."""
import asyncio
import time
from typing import Any


_DEVICE_URL = "https://accounts.feishu.cn/oauth/v1/device_authorization"
_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"


class _FeishuDeviceFlowClient:
    """Small replacement for the SDK's broken device-flow helper."""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        http_client: Any = None,
    ) -> None:
        """Initialize with application credentials and an optional client."""
        self._app_id = app_id
        self._app_secret = app_secret
        self._http = http_client
        self._owns_http = http_client is None

    async def close(self) -> None:
        """Close the internally created HTTP client."""
        if self._http is not None and self._owns_http:
            await self._http.aclose()
            self._http = None

    async def start(self, scopes: list[str]) -> dict[str, Any]:
        """Start device authorization and return browser instructions."""
        payload = await self._post(
            _DEVICE_URL,
            {
                "client_id": self._app_id,
                "scope": " ".join(sorted({*scopes, "offline_access"})),
            },
            auth=(self._app_id, self._app_secret),
        )
        return {
            "verification_uri": payload.get("verification_uri", ""),
            "verification_uri_complete": payload.get(
                "verification_uri_complete",
                payload.get("verification_uri", ""),
            ),
            "user_code": payload.get("user_code", ""),
            "device_code": payload.get("device_code", ""),
            "expires_in": int(payload.get("expires_in") or 0),
            "interval": int(payload.get("interval") or 5),
        }

    async def poll(
        self,
        device_code: str,
        *,
        interval: int = 5,
        timeout_seconds: int = 600,
    ) -> dict[str, Any]:
        """Poll until the user grants authorization or it expires."""
        deadline = time.monotonic() + timeout_seconds
        delay = max(interval, 1)
        while time.monotonic() < deadline:
            payload = await self._post(
                _TOKEN_URL,
                {
                    "grant_type": (
                        "urn:ietf:params:oauth:grant-type:device_code"
                    ),
                    "device_code": device_code,
                    "client_id": self._app_id,
                    "client_secret": self._app_secret,
                },
                pending=True,
            )
            error = payload.get("error")
            if not error:
                return self._token(payload)
            if error == "slow_down":
                delay += 2
            await asyncio.sleep(delay)
        raise RuntimeError("Feishu device authorization timed out.")

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        """Refresh a user token."""
        return self._token(
            await self._post(
                _TOKEN_URL,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self._app_id,
                    "client_secret": self._app_secret,
                },
            ),
        )

    async def _post(
        self,
        url: str,
        data: dict[str, str],
        *,
        auth: tuple[str, str] | None = None,
        pending: bool = False,
    ) -> dict[str, Any]:
        """Send one form-encoded OAuth request."""
        if self._http is None:
            import httpx

            self._http = httpx.AsyncClient(timeout=30.0)
        try:
            response = await self._http.post(url, data=data, auth=auth)
            payload = response.json()
        except Exception as exc:  # pylint: disable=broad-except
            raise RuntimeError(
                "Feishu user authorization request failed.",
            ) from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Feishu authorization returned invalid data.")
        payload = payload.get("data") or payload
        error = str(payload.get("error") or "")
        allowed = pending and error in {"authorization_pending", "slow_down"}
        if error and not allowed:
            raise RuntimeError(f"Feishu user authorization failed: {error}.")
        if payload.get("code") not in (None, 0) and not error:
            raise RuntimeError("Feishu user authorization failed.")
        return payload

    @staticmethod
    def _token(payload: dict[str, Any]) -> dict[str, Any]:
        """Convert a token response to the shape used by the channel."""
        if not payload.get("access_token"):
            raise RuntimeError("Feishu authorization returned no token.")
        now = time.time()
        expires_in = int(payload.get("expires_in") or 0)
        refresh_expires_in = int(
            payload.get("refresh_token_expires_in") or 0,
        )
        return {
            "access_token": str(payload["access_token"]),
            "refresh_token": str(payload.get("refresh_token") or ""),
            "expires_at": now + expires_in if expires_in else None,
            "refresh_expires_at": (
                now + refresh_expires_in if refresh_expires_in else None
            ),
            "scopes": str(payload.get("scope") or "")
            .replace(",", " ")
            .split(),
            "open_id": "",
        }
