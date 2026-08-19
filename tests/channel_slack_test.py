# -*- coding: utf-8 -*-
"""Tests for the Slack channel's event translation and reply streaming.

A fake AsyncWebClient stands in for the platform, so these cover the parts
that stand alone from a live workspace: inbound normalisation and mention
gating, the post-then-edit streaming reply, pagination of the discovery
calls, and the approval-card click round trip.
"""
# pylint: disable=protected-access,missing-function-docstring,unused-argument
import asyncio
from contextlib import suppress
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app.channel._base import ChannelEvent, ChatKind
from agentscope.app.channel._slack._card_templates import (
    _build_approval_blocks,
    _parse_action,
)
from agentscope.app.channel._slack._channel import (
    _MAX_LEN,
    _STREAM_MIN_INTERVAL,
    SlackChannel,
)
from agentscope.event import (
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
)
from agentscope.message import (
    Base64Source,
    DataBlock,
    TextBlock,
    ToolCallBlock,
)

_RID = "reply-1"
_BOT = "U0BOT"


class _FakeWeb:
    """Records every Slack Web API call the channel makes."""

    def __init__(self, token: str = "") -> None:
        self.token = token
        self.posts: list[dict] = []
        self.updates: list[dict] = []
        self.uploads: list[dict] = []
        self.list_pages: list[dict] = []
        self.member_pages: list[dict] = []
        self._ts = 0

    async def auth_test(self) -> dict:
        return {"user_id": _BOT, "ok": True}

    # The real methods are keyword-only and send their kwargs as a JSON
    # body without dropping Nones, so these record exactly what was passed.
    async def chat_postMessage(self, **kwargs: Any) -> dict:
        self._ts += 1
        ts = f"{self._ts}.000100"
        self.posts.append({**kwargs, "ts": ts})
        return {"ok": True, "ts": ts}

    async def chat_update(self, **kwargs: Any) -> dict:
        self.updates.append(dict(kwargs))
        return {"ok": True}

    async def files_upload_v2(
        self,
        *,
        channel: str,
        file: bytes,
        filename: str,
        title: str,
    ) -> dict:
        self.uploads.append(
            {"channel": channel, "file": file, "filename": filename},
        )
        return {"ok": True}

    async def conversations_info(self, channel: str) -> dict:
        return {"channel": {"name": f"name-of-{channel}", "is_im": False}}

    async def users_info(self, user: str) -> dict:
        return {"user": {"profile": {"display_name": f"Name{user}"}}}

    async def conversations_list(self, **kwargs: Any) -> dict:
        if self.list_pages:
            return self.list_pages.pop(0)
        return {"channels": []}

    async def conversations_members(self, **kwargs: Any) -> dict:
        if self.member_pages:
            return self.member_pages.pop(0)
        return {"members": []}


def _channel(**config: Any) -> tuple[SlackChannel, _FakeWeb]:
    channel = SlackChannel(
        "chan-1",
        SlackChannel.Credentials(
            app_id="A1",
            bot_token="xoxb-x",
            app_token="xapp-x",
        ),
        SlackChannel.Config(**config),
    )
    web = _FakeWeb()
    channel._web = web
    channel._bot_user_id = _BOT
    return channel, web


def _event(**over: Any) -> dict:
    return {
        "type": "message",
        "channel": "C1",
        "channel_type": "channel",
        "user": "U1",
        "text": f"<@{_BOT}> hello",
        "ts": "1700000000.000100",
        **over,
    }


async def _aiter(events: list) -> AsyncIterator[dict]:
    for evt in events:
        yield evt.model_dump(mode="json")


def _text_blocks(*deltas: str) -> list:
    events: list = [TextBlockStartEvent(reply_id=_RID, block_id="t1")]
    events += [
        TextBlockDeltaEvent(reply_id=_RID, block_id="t1", delta=d)
        for d in deltas
    ]
    events.append(TextBlockEndEvent(reply_id=_RID, block_id="t1"))
    return events


def _reply(*deltas: str) -> list:
    return [
        ReplyStartEvent(session_id="s", reply_id=_RID, name="a"),
        *_text_blocks(*deltas),
        ReplyEndEvent(session_id="s", reply_id=_RID),
    ]


class NormalizeTest(IsolatedAsyncioTestCase):
    """Slack message events become ``ChannelEvent``s."""

    async def test_mentioned_channel_message(self) -> None:
        channel, _ = _channel()
        event = await channel._normalize(_event())
        self.assertIsNotNone(event)
        self.assertEqual(event.channel_user_id, "U1")
        self.assertEqual(event.chat_id, "C1")
        self.assertEqual(event.channel_message_id, "1700000000.000100")
        self.assertEqual(event.metadata["chat_type"], "channel")
        # The bot's own mention markup is stripped from the text.
        self.assertEqual(event.message, "hello")
        self.assertEqual(event.channel_user_name, "NameU1")
        self.assertEqual(event.chat_name, "name-of-C1")

    async def test_unmentioned_channel_message_is_dropped(self) -> None:
        channel, _ = _channel()
        self.assertIsNone(await channel._normalize(_event(text="hello")))

    async def test_unmentioned_allowed_when_gate_off(self) -> None:
        channel, _ = _channel(only_at_reply=False)
        event = await channel._normalize(_event(text="hello"))
        self.assertIsNotNone(event)

    async def test_direct_message_is_never_gated(self) -> None:
        channel, _ = _channel()
        event = await channel._normalize(
            _event(channel_type="im", channel="D1", text="hello"),
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.message, "hello")
        # A DM gets no chat_name; the session is named after the person.
        self.assertEqual(event.chat_name, "")
        self.assertEqual(await channel.chat_kind("D1"), ChatKind.PRIVATE)

    async def test_unknown_bot_id_fails_closed(self) -> None:
        channel, _ = _channel()
        channel._bot_user_id = ""
        self.assertIsNone(await channel._normalize(_event()))

    async def test_own_message_is_ignored(self) -> None:
        channel, _ = _channel()
        self.assertIsNone(await channel._normalize(_event(user=_BOT)))
        self.assertIsNone(await channel._normalize(_event(bot_id="B1")))

    async def test_noise_subtypes_are_ignored(self) -> None:
        channel, _ = _channel()
        for subtype in ("channel_join", "message_changed", "bot_message"):
            self.assertIsNone(
                await channel._normalize(_event(subtype=subtype)),
                subtype,
            )

    async def test_file_share_is_kept(self) -> None:
        channel, _ = _channel()

        async def _fake_download(spec: dict) -> DataBlock:
            return DataBlock(
                source=Base64Source(data="aW1n", media_type="image/png"),
                name=spec.get("name", ""),
            )

        setattr(channel, "_download", _fake_download)
        event = await channel._normalize(
            _event(
                subtype="file_share",
                files=[{"name": "chart.png", "url_private_download": "u"}],
            ),
        )
        self.assertIsInstance(event.content[0], DataBlock)
        self.assertEqual(event.content[0].name, "chart.png")
        # Attachments come first so the text reads as their caption.
        self.assertIsInstance(event.content[1], TextBlock)

    async def test_empty_message_yields_nothing(self) -> None:
        channel, _ = _channel()
        self.assertIsNone(
            await channel._normalize(_event(text=f"<@{_BOT}>   ")),
        )


class SendResponseTest(IsolatedAsyncioTestCase):
    """The run's events post once, then edit that message in place."""

    async def _deliver(
        self,
        events: list,
        **config: Any,
    ) -> tuple[SlackChannel, _FakeWeb]:
        channel, web = _channel(**config)
        await channel.send_response(
            ChannelEvent(
                channel_id="chan-1",
                channel_user_id="U1",
                chat_id="C1",
            ),
            _aiter(events),
        )
        return channel, web

    async def test_posts_once_then_edits(self) -> None:
        _, web = await self._deliver(_reply("Hello ", "world"))
        self.assertEqual(len(web.posts), 1)
        self.assertEqual(web.posts[0]["channel"], "C1")
        # The final text lands via an edit of that same message.
        self.assertTrue(web.updates)
        self.assertEqual(web.updates[-1]["text"], "Hello world")
        self.assertEqual(web.updates[-1]["ts"], web.posts[0]["ts"])

    async def test_plain_message_omits_blocks(self) -> None:
        # slack_sdk sends these kwargs as a JSON body without stripping
        # Nones, so a plain message must not carry a null 'blocks'.
        _, web = await self._deliver(_reply("hi"))
        for call in (*web.posts, *web.updates):
            self.assertNotIn("blocks", call)

    async def test_refreshes_are_throttled(self) -> None:
        _, web = await self._deliver(_reply(*[f"{i} " for i in range(40)]))
        self.assertEqual(
            len(web.posts),
            1,
            f"expected throttling within {_STREAM_MIN_INTERVAL}s",
        )
        self.assertLessEqual(len(web.updates), 2)

    async def test_long_reply_splits_into_follow_ups(self) -> None:
        _, web = await self._deliver(_reply("x" * (_MAX_LEN + 500)))
        # First chunk edits the streamed message, the rest are new posts.
        self.assertEqual(len(web.updates), 1)
        self.assertEqual(len(web.updates[0]["text"]), _MAX_LEN)
        self.assertEqual(len(web.posts), 2)
        self.assertEqual(len(web.posts[1]["text"]), 500)

    async def test_attachment_is_uploaded(self) -> None:
        _, web = await self._deliver(
            [
                ReplyStartEvent(session_id="s", reply_id=_RID, name="a"),
                DataBlockStartEvent(
                    reply_id=_RID,
                    block_id="d1",
                    media_type="image/png",
                ),
                DataBlockDeltaEvent(
                    reply_id=_RID,
                    block_id="d1",
                    data="aW1n",
                    media_type="image/png",
                ),
                DataBlockEndEvent(reply_id=_RID, block_id="d1"),
                ReplyEndEvent(session_id="s", reply_id=_RID),
            ],
        )
        self.assertEqual(len(web.uploads), 1)
        self.assertEqual(web.uploads[0]["channel"], "C1")
        self.assertEqual(web.uploads[0]["file"], b"img")

    async def test_confirmation_posts_a_card_per_tool_call(self) -> None:
        _, web = await self._deliver(
            [
                ReplyStartEvent(session_id="s", reply_id=_RID, name="a"),
                *_text_blocks("working"),
                RequireUserConfirmEvent(
                    id="req-1",
                    reply_id=_RID,
                    tool_calls=[
                        ToolCallBlock(
                            id="call-1",
                            name="Bash",
                            input='{"command": "ls"}',
                        ),
                    ],
                ),
            ],
        )
        card = next(p for p in web.posts if p.get("blocks"))
        actions = next(b for b in card["blocks"] if b["type"] == "actions")
        parsed = [_parse_action(e["value"]) for e in actions["elements"]]
        self.assertEqual(len(parsed), 2)
        for entry in parsed:
            self.assertIsNotNone(entry)
            self.assertEqual(entry[0], "call-1")
            self.assertEqual(entry[1], "C1")
        self.assertTrue(parsed[0][2])
        self.assertFalse(parsed[1][2])


class InteractionTest(IsolatedAsyncioTestCase):
    """A button click becomes a decision event and freezes the card."""

    def _payload(self, value: str) -> dict:
        return {
            "type": "block_actions",
            "user": {"id": "U9"},
            "channel": {"id": "C1"},
            "message": {"ts": "42.0001"},
            "actions": [{"action_id": "x", "value": value}],
        }

    async def test_click_emits_decision_and_updates_card(self) -> None:
        channel, web = _channel()
        emitted: list = []

        async def _emit(event: Any) -> None:
            emitted.append(event)

        channel._emit = _emit
        blocks = _build_approval_blocks(
            "call-1",
            "C1",
            "Bash",
            "ls",
            "agent-1",
            "sess-1",
        )
        value = blocks[-1]["elements"][0]["value"]
        await channel._on_interaction(self._payload(value))

        self.assertEqual(len(emitted), 1)
        decision = emitted[0]
        self.assertEqual(decision.tool_call_id, "call-1")
        self.assertEqual(decision.agent_id, "agent-1")
        self.assertEqual(decision.session_id, "sess-1")
        self.assertEqual(decision.channel_user_id, "U9")
        # Who actually clicked, recorded for auditing.
        self.assertEqual(decision.actor, "U9")
        self.assertTrue(decision.approved)
        self.assertEqual(len(web.updates), 1)
        self.assertEqual(web.updates[0]["ts"], "42.0001")

    async def test_foreign_button_is_ignored(self) -> None:
        channel, web = _channel()
        emitted: list = []

        async def _emit(event: Any) -> None:
            emitted.append(event)

        channel._emit = _emit
        await channel._on_interaction(self._payload("not-ours"))
        self.assertEqual(emitted, [])
        self.assertEqual(web.updates, [])


class ActionValueTest(IsolatedAsyncioTestCase):
    """The button value round-trips the routing keys."""

    async def test_round_trip(self) -> None:
        blocks = _build_approval_blocks("c1", "C1", "Bash", "ls", "a1", "s1")
        approve, deny = blocks[-1]["elements"]
        self.assertEqual(
            _parse_action(approve["value"]),
            ("c1", "C1", True, "a1", "s1"),
        )
        self.assertEqual(
            _parse_action(deny["value"]),
            ("c1", "C1", False, "a1", "s1"),
        )

    async def test_rejects_foreign_values(self) -> None:
        self.assertIsNone(_parse_action("not json"))
        self.assertIsNone(_parse_action('{"type": "something_else"}'))
        self.assertIsNone(_parse_action(None))

    async def test_button_value_fits_slack_limit(self) -> None:
        blocks = _build_approval_blocks(
            "c" * 200,
            "C" * 200,
            "Bash",
            "x" * 5000,
            "a" * 200,
            "s" * 200,
        )
        for element in blocks[-1]["elements"]:
            self.assertLessEqual(len(element["value"]), 2000)
        section = blocks[1]["text"]["text"]
        self.assertLessEqual(len(section), 3000)


class DiscoveryTest(IsolatedAsyncioTestCase):
    """The listing calls follow Slack's cursor pagination."""

    async def test_list_bot_chats_pages(self) -> None:
        channel, web = _channel()
        web.list_pages = [
            {
                "channels": [{"id": "C1", "name": "general"}],
                "response_metadata": {"next_cursor": "c2"},
            },
            {
                "channels": [{"id": "D1", "user": "U1", "is_im": True}],
                "response_metadata": {"next_cursor": ""},
            },
        ]
        chats = await channel.list_bot_chats()
        self.assertEqual(
            chats,
            [
                {"chat_id": "C1", "name": "general", "chat_type": "channel"},
                {"chat_id": "D1", "name": "U1", "chat_type": "im"},
            ],
        )

    async def test_list_chat_members_pages_and_names(self) -> None:
        channel, web = _channel()
        web.member_pages = [
            {
                "members": ["U1"],
                "response_metadata": {"next_cursor": "c2"},
            },
            {"members": ["U2"], "response_metadata": {"next_cursor": ""}},
        ]
        members = await channel.list_chat_members("C1")
        self.assertEqual(
            members,
            [
                {"user_id": "U1", "name": "NameU1"},
                {"user_id": "U2", "name": "NameU2"},
            ],
        )


def _request(**over: Any) -> Any:
    """A stand-in for the SDK's SocketModeRequest."""
    fields: dict[str, Any] = {
        "type": "events_api",
        "envelope_id": "env-1",
        "retry_attempt": 0,
        "retry_reason": "",
        "payload": {"event_id": "Ev123", "event": _event()},
    }
    fields.update(over)
    return SimpleNamespace(**fields)


class DeliveryMetadataTest(IsolatedAsyncioTestCase):
    """The envelope's identifiers survive onto the emitted event."""

    async def test_identifiers_are_preserved(self) -> None:
        channel, _ = _channel()
        emitted: list = []

        async def _emit(event: Any) -> None:
            emitted.append(event)

        channel._emit = _emit
        await channel._on_event(
            _request(retry_attempt=2, retry_reason="http_timeout"),
        )
        self.assertEqual(len(emitted), 1)
        meta = emitted[0].metadata
        self.assertEqual(meta["event_id"], "Ev123")
        self.assertEqual(meta["envelope_id"], "env-1")
        self.assertEqual(meta["retry_attempt"], 2)
        self.assertEqual(meta["retry_reason"], "http_timeout")
        # The existing routing key is untouched.
        self.assertEqual(meta["chat_type"], "channel")

    async def test_non_message_events_are_skipped(self) -> None:
        channel, _ = _channel()
        emitted: list = []

        async def _emit(event: Any) -> None:
            emitted.append(event)

        channel._emit = _emit
        await channel._on_event(
            _request(payload={"event": {"type": "reaction_added"}}),
        )
        self.assertEqual(emitted, [])


class _FailingWeb(_FakeWeb):
    """A web client whose auth.test rejects the bot token."""

    async def auth_test(self) -> dict:
        raise RuntimeError("invalid_auth")


class LifecycleTest(IsolatedAsyncioTestCase):
    """Start-up failures still run the finalizer and clear state."""

    def _channel(self) -> SlackChannel:
        return SlackChannel(
            "chan-1",
            SlackChannel.Credentials(
                app_id="A1",
                bot_token="xoxb-x",
                app_token="xapp-x",
            ),
            SlackChannel.Config(),
        )

    async def _wait_for(self, channel: SlackChannel, state: str) -> None:
        for _ in range(200):
            if channel.status.state == state:
                return
            await asyncio.sleep(0.01)
        self.fail(f"channel never reached {state!r}")

    async def test_cancel_while_parked_on_bad_auth(self) -> None:
        channel = self._channel()

        async def _emit(event: Any) -> None:
            pass

        with patch(
            "slack_sdk.web.async_client.AsyncWebClient",
            _FailingWeb,
        ):
            task = asyncio.create_task(channel.start_listening(_emit))
            # It parks in 'failed' rather than returning, so the dispatcher
            # does not immediately restart it.
            await self._wait_for(channel, "failed")
            self.assertIn("invalid_auth", channel.status.last_error)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        # Cancellation still reaches the finalizer.
        self.assertEqual(channel.status.state, "stopped")
        self.assertIsNone(channel._web)
        self.assertIsNone(channel._client)

    async def test_socket_client_construction_failure_is_cleaned_up(
        self,
    ) -> None:
        channel = self._channel()

        async def _emit(event: Any) -> None:
            pass

        def _boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("socket mode unavailable")

        with patch(
            "slack_sdk.web.async_client.AsyncWebClient",
            _FakeWeb,
        ), patch(
            "slack_sdk.socket_mode.aiohttp.SocketModeClient",
            _boom,
        ):
            with self.assertRaises(RuntimeError):
                await channel.start_listening(_emit)
        self.assertEqual(channel.status.state, "stopped")
        self.assertIsNone(channel._web)
        self.assertIsNone(channel._client)


class ProactiveSendTest(IsolatedAsyncioTestCase):
    """send_message_to splits rather than truncating."""

    async def test_long_message_is_split_not_truncated(self) -> None:
        channel, web = _channel()
        text = "a" * _MAX_LEN + "b" * 200
        with patch.object(channel, "_post", wraps=channel._post) as post:
            result = await channel.send_message_to("C1", text)
        self.assertTrue(result["ok"])
        self.assertEqual(result["segments"], 2)
        self.assertEqual(len(result["sent_ts"]), 2)
        self.assertEqual(post.call_count, 2)
        # Nothing was dropped.
        self.assertEqual(
            "".join(call["text"] for call in web.posts),
            text,
        )

    async def test_short_message_reports_one_segment(self) -> None:
        channel, _ = _channel()
        result = await channel.send_message_to("C1", "hi")
        self.assertTrue(result["ok"])
        self.assertEqual(result["segments"], 1)

    async def test_partial_failure_reports_what_landed(self) -> None:
        channel, _ = _channel()
        calls: list[str] = []

        async def _flaky(chat_id: str, text: str, blocks: Any = None) -> Any:
            calls.append(text)
            return None if len(calls) > 1 else "1.0001"

        setattr(channel, "_post", _flaky)
        result = await channel.send_message_to(
            "C1",
            "a" * _MAX_LEN + "b" * 200,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["sent_ts"], ["1.0001"])
        self.assertEqual(result["failed_segment"], 1)
        self.assertEqual(result["segments"], 2)

    async def test_empty_message_is_refused(self) -> None:
        channel, web = _channel()
        result = await channel.send_message_to("C1", "")
        self.assertFalse(result["ok"])
        self.assertEqual(web.posts, [])
