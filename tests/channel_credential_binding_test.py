# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for interactive channel credential binding.

A session lives in the message bus rather than in any one process, so
every test drives it through *two* services sharing one bus — the two
replicas a client's requests would land on in turn.
"""
import asyncio
from contextlib import AsyncExitStack
from typing import Any
from unittest import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.app._service import (
    CredentialBindingError,
    CredentialBindingService,
)
from agentscope.app.channel import (
    BindingState,
    BindingStep,
    ChannelBase,
    ChannelTypeRegistry,
    CredentialBindingBase,
)
from agentscope.app.message_bus import InMemoryMessageBus


class _ScriptedBinding(CredentialBindingBase):
    """Return a scripted step per call, recording how often it was asked."""

    script: list[BindingStep] = []
    calls: int = 0
    retry_after_secs: int = 0
    during_advance: Any = None
    """Awaited while "the platform" is being asked, to interleave a
    concurrent request the way a real round trip would allow."""

    async def begin(self) -> BindingStep:
        """Open with a verification URL."""
        return BindingStep(
            verification_url="https://example.test/qr",
            provider_state={"device_code": "dc-1"},
            retry_after_secs=type(self).retry_after_secs,
            expires_in_secs=600,
        )

    async def advance(self, provider_state: dict[str, Any]) -> BindingStep:
        """Pop the next scripted outcome."""
        _ = provider_state
        type(self).calls += 1
        hook = type(self).during_advance
        if hook is not None:
            await hook()
        return type(self).script.pop(0)


class _BoundChannel(ChannelBase):
    """A channel type offering interactive binding."""

    channel_type = "scripted"
    display_name = "Scripted"
    platform_bot_id_field = "app_id"
    credential_binding = _ScriptedBinding

    @property
    def channel_id(self) -> str:
        """Unused by these tests."""
        return "scripted"

    async def start_listening(self, emit: Any) -> None:
        """Unused by these tests."""

    async def send_response(self, *args: Any, **kwargs: Any) -> None:
        """Unused by these tests."""


class _FormOnlyChannel(_BoundChannel):
    """A channel type with no interactive binding."""

    channel_type = "form-only"
    display_name = "Form only"
    credential_binding = None


class CredentialBindingTest(IsolatedAsyncioTestCase):
    """Sessions are driven from any replica and consumed exactly once."""

    async def asyncSetUp(self) -> None:
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(
            InMemoryMessageBus(),
        )
        registry = ChannelTypeRegistry([_BoundChannel, _FormOnlyChannel])
        # Two services, one bus: the replicas a client hops between.
        self.node_a = CredentialBindingService(self.bus, registry)
        self.node_b = CredentialBindingService(self.bus, registry)
        _ScriptedBinding.script = []
        _ScriptedBinding.calls = 0
        _ScriptedBinding.retry_after_secs = 0
        _ScriptedBinding.during_advance = None

    async def asyncTearDown(self) -> None:
        await self._stack.aclose()

    async def test_a_session_opened_on_one_node_advances_on_another(
        self,
    ) -> None:
        """The client hops replicas between every step and still wins."""
        opened = await self.node_a.start("u", "scripted")
        self.assertDictEqual(
            opened.model_dump(),
            {
                "binding_id": AnyString(),
                "state": BindingState.PENDING,
                "verification_url": "https://example.test/qr",
                "error": "",
                "retry_after_secs": 0,
            },
        )

        _ScriptedBinding.script = [
            BindingStep(provider_state={"device_code": "dc-1"}),
            BindingStep(
                state=BindingState.AUTHORIZED,
                credentials={"app_id": "a", "app_secret": "s"},
            ),
        ]

        still_waiting = await self.node_b.poll("u", opened.binding_id)
        self.assertEqual(still_waiting.state, BindingState.PENDING)

        # The whole view, so a secret can never leak into it unnoticed.
        self.assertDictEqual(
            (await self.node_a.poll("u", opened.binding_id)).model_dump(),
            {
                "binding_id": opened.binding_id,
                "state": BindingState.AUTHORIZED,
                "verification_url": "https://example.test/qr",
                "error": "",
                "retry_after_secs": 0,
            },
        )

        self.assertDictEqual(
            await self.node_b.claim("u", opened.binding_id, "scripted"),
            {"app_id": "a", "app_secret": "s"},
        )

    async def test_credentials_can_only_be_claimed_once(self) -> None:
        """A second claim finds nothing, however it races the first."""
        opened = await self.node_a.start("u", "scripted")
        _ScriptedBinding.script = [
            BindingStep(
                state=BindingState.AUTHORIZED,
                credentials={"app_id": "a", "app_secret": "s"},
            ),
        ]
        await self.node_a.poll("u", opened.binding_id)

        await self.node_a.claim("u", opened.binding_id, "scripted")
        with self.assertRaises(CredentialBindingError) as ctx:
            await self.node_b.claim("u", opened.binding_id, "scripted")
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_a_cancel_during_an_upstream_poll_wins(self) -> None:
        """The regression this design exists for: node A read the
        session, then the operator cancelled on node B while A was
        asking the platform. A's approval must not revive it."""
        opened = await self.node_a.start("u", "scripted")

        async def _cancel_midway() -> None:
            await self.node_b.cancel("u", opened.binding_id)

        _ScriptedBinding.during_advance = _cancel_midway
        _ScriptedBinding.script = [
            BindingStep(
                state=BindingState.AUTHORIZED,
                credentials={"app_id": "a", "app_secret": "s"},
            ),
        ]

        self.assertDictEqual(
            (await self.node_a.poll("u", opened.binding_id)).model_dump(),
            {
                "binding_id": opened.binding_id,
                "state": BindingState.CANCELLED,
                "verification_url": "https://example.test/qr",
                "error": "",
                "retry_after_secs": 0,
            },
        )
        with self.assertRaises(CredentialBindingError):
            await self.node_b.claim("u", opened.binding_id, "scripted")

    async def test_polling_faster_than_the_platform_allows_is_absorbed(
        self,
    ) -> None:
        """The client sets the request rate, the platform's interval
        still sets the upstream rate."""
        _ScriptedBinding.retry_after_secs = 60
        opened = await self.node_a.start("u", "scripted")

        _ScriptedBinding.script = [BindingStep(), BindingStep()]
        await self.node_a.poll("u", opened.binding_id)
        await self.node_b.poll("u", opened.binding_id)
        await self.node_a.poll("u", opened.binding_id)

        self.assertEqual(_ScriptedBinding.calls, 1)

    async def test_a_session_is_invisible_to_other_users(self) -> None:
        """Another user cannot even tell the id exists."""
        opened = await self.node_a.start("u", "scripted")
        with self.assertRaises(CredentialBindingError) as ctx:
            await self.node_b.poll("intruder", opened.binding_id)
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_a_form_only_type_is_rejected(self) -> None:
        """A type without a provider says so instead of half-starting."""
        with self.assertRaises(CredentialBindingError) as ctx:
            await self.node_a.start("u", "form-only")
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_a_rejected_claim_leaves_the_session_usable(self) -> None:
        """Checks come before the destructive take, so a claim for the
        wrong type — or one from an intruder — cannot burn a session."""
        opened = await self.node_a.start("u", "scripted")

        with self.assertRaises(CredentialBindingError) as early:
            await self.node_a.claim("u", opened.binding_id, "scripted")
        self.assertEqual(early.exception.status_code, 409)

        with self.assertRaises(CredentialBindingError) as intruder:
            await self.node_b.claim("hacker", opened.binding_id, "scripted")
        self.assertEqual(intruder.exception.status_code, 404)

        _ScriptedBinding.script = [
            BindingStep(
                state=BindingState.AUTHORIZED,
                credentials={"app_id": "a", "app_secret": "s"},
            ),
        ]
        await self.node_a.poll("u", opened.binding_id)

        with self.assertRaises(CredentialBindingError) as wrong_type:
            await self.node_b.claim("u", opened.binding_id, "form-only")
        self.assertEqual(wrong_type.exception.status_code, 409)

        # Still there after all of that.
        self.assertDictEqual(
            await self.node_a.claim("u", opened.binding_id, "scripted"),
            {"app_id": "a", "app_secret": "s"},
        )

    async def test_only_one_of_two_concurrent_polls_reaches_upstream(
        self,
    ) -> None:
        """The client sets its request rate; the platform's interval
        still bounds ours, even when two replicas poll at once."""
        _ScriptedBinding.retry_after_secs = 60
        opened = await self.node_a.start("u", "scripted")
        _ScriptedBinding.script = [BindingStep(), BindingStep()]

        await asyncio.gather(
            self.node_a.poll("u", opened.binding_id),
            self.node_b.poll("u", opened.binding_id),
        )

        self.assertEqual(_ScriptedBinding.calls, 1)
