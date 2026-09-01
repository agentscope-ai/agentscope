# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for Feishu's device-flow credential binding.

The provider is driven one step per call rather than by a loop of its
own, so each response the platform can give is checked on its own.
"""
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app.channel import BindingState
from agentscope.app.channel._feishu._credential_binding import (
    FeishuCredentialBinding,
)


class _Response:
    """Minimal stand-in for an ``httpx`` response."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        """Return the canned body."""
        return self._payload


class _Client:
    """Records each POST and answers from a script."""

    posts: list[tuple[str, dict]] = []
    script: list[dict] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _ = args, kwargs

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass

    async def post(self, url: str, data: dict) -> _Response:
        """Record the call and return the next scripted body."""
        type(self).posts.append((url, data))
        return _Response(type(self).script.pop(0))


class FeishuCredentialBindingTest(IsolatedAsyncioTestCase):
    """Each device-flow outcome maps onto one binding step."""

    def setUp(self) -> None:
        _Client.posts = []
        _Client.script = []
        self.binding = FeishuCredentialBinding()
        patcher = patch("httpx.AsyncClient", _Client)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_begin_returns_the_url_and_the_platform_interval(
        self,
    ) -> None:
        """The opening call carries everything the next step needs."""
        _Client.script = [
            {
                "device_code": "dc-1",
                "verification_uri_complete": "https://feishu.test/qr?x=1",
                "interval": 7,
                "expires_in": 300,
            },
        ]

        step = await self.binding.begin()

        self.assertDictEqual(
            step.model_dump(),
            {
                "state": BindingState.PENDING,
                "verification_url": "https://feishu.test/qr?x=1",
                "credentials": {},
                "error": "",
                "provider_state": {
                    "device_code": "dc-1",
                    "domain": "https://accounts.feishu.cn",
                    "interval": 7,
                },
                "retry_after_secs": 7,
                "expires_in_secs": 300,
            },
        )
        self.assertEqual(
            _Client.posts[0][0],
            "https://accounts.feishu.cn/oauth/v1/app/registration",
        )

    async def test_begin_reports_a_refusal_instead_of_half_starting(
        self,
    ) -> None:
        """No device code means the session never opened."""
        _Client.script = [{"error_description": "app quota exceeded"}]

        step = await self.binding.begin()

        self.assertEqual(step.state, BindingState.FAILED)
        self.assertEqual(step.error, "app quota exceeded")

    async def test_approval_yields_the_credentials(self) -> None:
        """The platform's client id/secret become channel credentials."""
        _Client.script = [{"client_id": "cli-1", "client_secret": "sec-1"}]

        step = await self.binding.advance(
            {"device_code": "dc-1", "domain": "https://accounts.feishu.cn"},
        )

        self.assertEqual(step.state, BindingState.AUTHORIZED)
        self.assertDictEqual(
            step.credentials,
            {"app_id": "cli-1", "app_secret": "sec-1"},
        )

    async def test_pending_keeps_the_state_and_the_interval(self) -> None:
        """Waiting must not disturb the pace already agreed."""
        _Client.script = [{"error": "authorization_pending"}]
        state = {
            "device_code": "dc-1",
            "domain": "https://accounts.feishu.cn",
            "interval": 7,
        }

        step = await self.binding.advance(state)

        self.assertEqual(step.state, BindingState.PENDING)
        self.assertIsNone(step.retry_after_secs)
        self.assertDictEqual(step.provider_state, state)

    async def test_slow_down_widens_the_gap_for_later_steps(self) -> None:
        """A throttled session must actually back off, not keep its
        original rate."""
        _Client.script = [{"error": "slow_down"}]

        step = await self.binding.advance(
            {
                "device_code": "dc-1",
                "domain": "https://accounts.feishu.cn",
                "interval": 5,
            },
        )

        self.assertEqual(step.retry_after_secs, 10)
        self.assertEqual(step.provider_state["interval"], 10)

    async def test_a_lark_tenant_moves_to_its_own_domain(self) -> None:
        """The next poll must go where that tenant actually answers."""
        _Client.script = [{"user_info": {"tenant_brand": "lark"}}]

        step = await self.binding.advance(
            {"device_code": "dc-1", "domain": "https://accounts.feishu.cn"},
        )

        self.assertEqual(step.state, BindingState.PENDING)
        self.assertEqual(
            step.provider_state["domain"],
            "https://accounts.larksuite.com",
        )

    async def test_a_denial_ends_the_session(self) -> None:
        """A refused approval is terminal, not something to keep
        polling."""
        _Client.script = [
            {"error": "access_denied", "error_description": "user said no"},
        ]

        step = await self.binding.advance(
            {"device_code": "dc-1", "domain": "https://accounts.feishu.cn"},
        )

        self.assertEqual(step.state, BindingState.FAILED)
        self.assertEqual(step.error, "user said no")
