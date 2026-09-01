# -*- coding: utf-8 -*-
"""Feishu app registration by QR code.

Feishu registers an app through an OAuth device flow: we open a session,
show the operator a URL as a QR code, and ask the platform every few
seconds whether they have approved it. ``lark_oapi.aregister_app`` wraps
that in a loop of its own and never surfaces the device code, which
would pin the whole session to one process — so the two calls are made
directly here instead, one step per request.
"""
from typing import Any

import httpx

from .._credential_binding import (
    BindingState,
    BindingStep,
    CredentialBindingBase,
)

_ENDPOINT = "/oauth/v1/app/registration"

_FEISHU_DOMAIN = "https://accounts.feishu.cn"
_LARK_DOMAIN = "https://accounts.larksuite.com"


class FeishuCredentialBinding(CredentialBindingBase):
    """Drive Feishu's device flow one step per call."""

    async def begin(self) -> BindingStep:
        """Open a registration session. See base."""
        payload = await self._post(
            _FEISHU_DOMAIN,
            {
                "action": "begin",
                "archetype": "PersonalAgent",
                "auth_method": "client_secret",
                "request_user_info": "open_id",
            },
        )
        if "device_code" not in payload:
            return BindingStep(
                state=BindingState.FAILED,
                error=payload.get("error_description")
                or payload.get("error")
                or "Feishu did not return a device code.",
            )

        return BindingStep(
            verification_url=payload["verification_uri_complete"],
            provider_state={
                "device_code": payload["device_code"],
                "domain": _FEISHU_DOMAIN,
            },
            retry_after_secs=int(payload.get("interval", 5)),
            expires_in_secs=int(payload.get("expires_in", 600)),
        )

    async def advance(self, provider_state: dict[str, Any]) -> BindingStep:
        """Ask Feishu once whether the QR code has been approved.

        Args:
            provider_state (`dict[str, Any]`):
                Carries the device code and the domain, which moves to
                Lark for a tenant that lives there.

        Returns:
            `BindingStep`: The session's state after this poll.
        """
        domain = provider_state.get("domain", _FEISHU_DOMAIN)
        payload = await self._post(
            domain,
            {"action": "poll", "device_code": provider_state["device_code"]},
        )

        if payload.get("client_id") and payload.get("client_secret"):
            return BindingStep(
                state=BindingState.AUTHORIZED,
                credentials={
                    "app_id": str(payload["client_id"]),
                    "app_secret": str(payload["client_secret"]),
                },
            )

        # A Lark tenant answers on its own domain; keep polling there.
        user_info = payload.get("user_info") or {}
        if user_info.get("tenant_brand") == "lark" and domain != _LARK_DOMAIN:
            return BindingStep(
                provider_state={**provider_state, "domain": _LARK_DOMAIN},
            )

        error = payload.get("error", "")
        if error in ("authorization_pending", "slow_down"):
            return BindingStep(provider_state=provider_state)

        return BindingStep(
            state=BindingState.FAILED,
            error=payload.get("error_description") or error or "unknown",
        )

    @staticmethod
    async def _post(domain: str, data: dict[str, str]) -> dict:
        """POST one form-encoded action to the registration endpoint.

        Args:
            domain (`str`): Feishu or Lark accounts host.
            data (`dict[str, str]`): The action and its arguments.

        Returns:
            `dict`: The decoded response body.
        """
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(domain + _ENDPOINT, data=data)
            return response.json()
