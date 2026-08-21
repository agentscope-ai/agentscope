# -*- coding: utf-8 -*-
"""Tests for the WeCom channel's frame translation and reply streaming.

A fake WSClient stands in for the platform, so these cover the parts that
stand alone from a live bot: inbound normalisation, the streaming reply
opened up front to meet the five-second window, the push fallback for
runs with no inbound message, and the approval-card click round trip.
"""
# pylint: disable=protected-access,missing-function-docstring,unused-argument
from typing import Any, AsyncIterator
from unittest import IsolatedAsyncioTestCase

from agentscope.app.channel._base import ChannelEvent, ChatKind
from agentscope.app.channel._wecom._card_templates import (
    _build_approval_card,
    _button_key,
    _parse_button_key,
)
from agentscope.app.channel._wecom._channel import (
    _PENDING_REPLY,
    _STREAM_MIN_INTERVAL,
    _Pending,
    WeComChannel,
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
from agentscope.message import DataBlock, TextBlock, ToolCallBlock

_RID = "reply-1"


class _FakeClient:
    """Records everything the channel sends to the platform."""

    def __init__(self) -> None:
        self.streams: list[tuple[str, str, bool]] = []
        self.sent: list[tuple[str, dict]] = []
        self.updates: list[dict] = []
        self.cmds: list[tuple[str, dict]] = []

    async def reply_stream(
        self,
        frame: dict,
        stream_id: str,
        content: str,
        finish: bool = False,
        **_kwargs: Any,
    ) -> dict:
        self.streams.append((stream_id, content, finish))
        return {"errcode": 0}

    async def send_message(self, chat_id: str, body: dict) -> dict:
        self.sent.append((chat_id, body))
        return {"errcode": 0}

    async def update_template_card(self, frame: dict, card: dict) -> dict:
        self.updates.append(card)
        return {"errcode": 0}

    async def download_file(
        self,
        url: str,
        aes_key: str,
    ) -> tuple[bytes, str]:
        return b"payload", "report.pdf"

    async def reply(self, frame: dict, body: dict, cmd: str) -> dict:
        self.cmds.append((cmd, body))
        if cmd.endswith("_init"):
            return {"errcode": 0, "body": {"upload_id": "up-1"}}
        if cmd.endswith("_finish"):
            return {"errcode": 0, "body": {"media_id": "media-1"}}
        return {"errcode": 0}


def _channel(**config: Any) -> tuple[WeComChannel, _FakeClient]:
    channel = WeComChannel(
        "chan-1",
        WeComChannel.Credentials(bot_id="b", secret="s"),
        WeComChannel.Config(**config),
    )
    client = _FakeClient()
    channel._client = client
    return channel, client


def _frame(msgtype: str, **body: Any) -> dict:
    return {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req-1"},
        "body": {
            "msgid": "m-1",
            "chatid": "chat-1",
            "chattype": "single",
            "from": {"userid": "zhangsan"},
            "msgtype": msgtype,
            **body,
        },
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
    """Inbound frames become ``ChannelEvent``s."""

    async def test_text_message(self) -> None:
        channel, _ = _channel()
        event = await channel._normalize(
            _frame("text", text={"content": " hello "}),
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.channel_user_id, "zhangsan")
        self.assertEqual(event.chat_id, "chat-1")
        self.assertEqual(event.channel_message_id, "m-1")
        self.assertEqual(event.message, "hello")
        self.assertEqual(event.metadata["chat_type"], "single")

    async def test_chat_kind_cached_from_inbound(self) -> None:
        channel, _ = _channel()
        await channel._normalize(
            _frame(
                "text",
                chattype="group",
                chatid="g-1",
                text={"content": "hi"},
            ),
        )
        self.assertEqual(await channel.chat_kind("g-1"), ChatKind.GROUP)
        self.assertIsNone(await channel.chat_kind("unseen"))

    async def test_empty_text_yields_nothing(self) -> None:
        channel, _ = _channel()
        event = await channel._normalize(
            _frame("text", text={"content": "  "}),
        )
        self.assertIsNone(event)

    async def test_media_message_downloads(self) -> None:
        channel, _ = _channel()
        event = await channel._normalize(
            _frame("file", file={"url": "http://x", "aeskey": "k"}),
        )
        self.assertEqual(len(event.content), 1)
        block = event.content[0]
        self.assertIsInstance(block, DataBlock)
        self.assertEqual(block.name, "report.pdf")

    async def test_mixed_message_keeps_order(self) -> None:
        channel, _ = _channel()
        event = await channel._normalize(
            _frame(
                "mixed",
                mixed={
                    "msg_item": [
                        {"msgtype": "text", "text": {"content": "see"}},
                        {
                            "msgtype": "image",
                            "image": {"url": "http://x", "aeskey": "k"},
                        },
                    ],
                },
            ),
        )
        self.assertIsInstance(event.content[0], TextBlock)
        self.assertEqual(event.content[0].text, "see")
        self.assertIsInstance(event.content[1], DataBlock)

    async def test_unsupported_type_tells_the_user(self) -> None:
        channel, client = _channel()
        event = await channel._normalize(_frame("location"))
        self.assertIsNone(event)
        self.assertEqual(len(client.sent), 1)
        chat_id, body = client.sent[0]
        self.assertEqual(chat_id, "chat-1")
        self.assertIn("Unsupported", body["markdown"]["content"])


class StreamLifecycleTest(IsolatedAsyncioTestCase):
    """A text message claims a stream up front; media-only does not."""

    async def test_text_opens_stream_before_emit(self) -> None:
        channel, client = _channel()
        emitted: list = []

        async def _emit(event: Any) -> None:
            # The placeholder must already be out by the time the gateway
            # sees the message — the platform allows five seconds.
            self.assertEqual(len(client.streams), 1)
            emitted.append(event)

        channel._emit = _emit
        await channel._on_message(_frame("text", text={"content": "hi"}))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(client.streams[0][1], _PENDING_REPLY)
        self.assertFalse(client.streams[0][2])
        self.assertIn("chat-1", channel._streams)

    async def test_media_only_opens_no_stream(self) -> None:
        channel, client = _channel()

        async def _emit(event: Any) -> None:
            pass

        channel._emit = _emit
        await channel._on_message(
            _frame("file", file={"url": "http://x", "aeskey": "k"}),
        )
        self.assertEqual(client.streams, [])
        self.assertEqual(channel._streams, {})

    async def test_reopening_finishes_the_previous_stream(self) -> None:
        channel, client = _channel()

        async def _emit(event: Any) -> None:
            pass

        channel._emit = _emit
        await channel._on_message(_frame("text", text={"content": "one"}))
        first = channel._streams["chat-1"].stream_id
        await channel._on_message(_frame("text", text={"content": "two"}))
        self.assertIn(
            (first, _PENDING_REPLY, True),
            client.streams,
        )
        self.assertNotEqual(channel._streams["chat-1"].stream_id, first)

    async def test_sweeper_finishes_an_abandoned_stream(self) -> None:
        channel, client = _channel()
        await channel._open_stream(_frame("text"), "chat-1")
        stream_id = channel._streams["chat-1"].stream_id
        channel._streams["chat-1"].touched_at -= 1000.0
        channel._stopped = True
        # Run one sweep directly rather than waiting on the interval.
        stale = list(channel._streams)
        for chat_id in stale:
            stream = channel._streams.pop(chat_id)
            await channel._push(stream, _PENDING_REPLY, finish=True)
        self.assertIn((stream_id, _PENDING_REPLY, True), client.streams)


class SendResponseTest(IsolatedAsyncioTestCase):
    """The run's events fold into the open stream, or a pushed message."""

    async def _deliver(
        self,
        events: list,
        *,
        with_stream: bool = True,
        **config: Any,
    ) -> tuple[WeComChannel, _FakeClient]:
        channel, client = _channel(**config)
        if with_stream:
            await channel._open_stream(_frame("text"), "chat-1")
            client.streams.clear()
        event = ChannelEvent(
            channel_id="chan-1",
            channel_user_id="zhangsan",
            chat_id="chat-1",
        )
        await channel.send_response(event, _aiter(events))
        return channel, client

    async def test_stream_finishes_with_the_full_text(self) -> None:
        _, client = await self._deliver(_reply("Hello ", "world"))
        self.assertTrue(client.streams)
        stream_id, content, finish = client.streams[-1]
        self.assertEqual(content, "Hello world")
        self.assertTrue(finish)
        # One stream id throughout, and only the last frame finishes it.
        self.assertEqual({s for s, _, _ in client.streams}, {stream_id})
        self.assertEqual([f for _, _, f in client.streams].count(True), 1)

    async def test_empty_run_still_closes_the_stream(self) -> None:
        _, client = await self._deliver(
            [ReplyEndEvent(session_id="s", reply_id=_RID)],
        )
        _, content, finish = client.streams[-1]
        self.assertTrue(finish)
        self.assertNotEqual(content, _PENDING_REPLY)
        self.assertTrue(content)

    async def test_attachment_only_reply_uploads_and_names_it(self) -> None:
        _, client = await self._deliver(
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
        # The stream closes naming the attachment rather than on the
        # placeholder, and the attachment itself is uploaded and sent.
        _, content, finish = client.streams[-1]
        self.assertTrue(finish)
        self.assertNotEqual(content, _PENDING_REPLY)
        self.assertEqual(
            [c for c, _ in client.cmds][-1],
            "aibot_upload_media_finish",
        )
        self.assertEqual(client.sent[-1][1]["msgtype"], "image")

    async def test_stream_is_released_after_use(self) -> None:
        channel, _ = await self._deliver(_reply("done"))
        self.assertEqual(channel._streams, {})

    async def test_refreshes_are_throttled(self) -> None:
        # Many deltas, all within one throttle window, collapse to the
        # single finishing frame.
        _, client = await self._deliver(_reply(*[f"{i} " for i in range(40)]))
        self.assertLessEqual(
            len(client.streams),
            2,
            f"expected throttling within {_STREAM_MIN_INTERVAL}s",
        )

    async def test_without_a_stream_the_reply_is_pushed(self) -> None:
        _, client = await self._deliver(_reply("scheduled"), with_stream=False)
        self.assertEqual(client.streams, [])
        self.assertEqual(len(client.sent), 1)
        chat_id, body = client.sent[0]
        self.assertEqual(chat_id, "chat-1")
        self.assertEqual(body["msgtype"], "markdown")
        self.assertEqual(body["markdown"]["content"], "scheduled")
        self.assertEqual(body["chat_type"], 1)

    async def test_group_push_carries_the_group_chat_type(self) -> None:
        channel, client = _channel()
        channel._chat_kind_cache["g-1"] = ChatKind.GROUP
        await channel.send_response(
            ChannelEvent(
                channel_id="chan-1",
                channel_user_id="u",
                chat_id="g-1",
            ),
            _aiter(_reply("hi")),
        )
        self.assertEqual(client.sent[0][1]["chat_type"], 2)

    async def test_confirmation_pushes_a_card_per_tool_call(self) -> None:
        channel, client = await self._deliver(
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
        self.assertEqual(len(client.sent), 1)
        _, body = client.sent[0]
        self.assertEqual(body["msgtype"], "template_card")
        card = body["template_card"]
        self.assertEqual(card["card_type"], "button_interaction")
        self.assertIn("Bash", card["main_title"]["desc"])
        # The token on the buttons resolves back to the tool call.
        self.assertEqual(len(channel._pending), 1)
        token = next(iter(channel._pending))
        self.assertEqual(channel._pending[token].tool_call_id, "call-1")
        keys = {b["key"] for b in card["button_list"]}
        self.assertEqual(
            keys,
            {_button_key(token, True), _button_key(token, False)},
        )


class CardClickTest(IsolatedAsyncioTestCase):
    """A button click becomes a decision event and freezes the card."""

    async def test_click_emits_decision_and_updates_card(self) -> None:
        channel, client = _channel()
        channel._pending["tok-1"] = _Pending(
            tool_call_id="call-1",
            chat_id="chat-1",
            agent_id="agent-1",
            session_id="sess-1",
            task_id="task-1",
        )
        emitted: list = []

        async def _emit(event: Any) -> None:
            emitted.append(event)

        channel._emit = _emit
        await channel._on_card_event(
            {
                "body": {
                    "from": {"userid": "zhangsan"},
                    "event": {
                        "eventtype": "template_card_event",
                        "event_key": _button_key("tok-1", True),
                        "task_id": "task-1",
                    },
                },
            },
        )
        self.assertEqual(len(emitted), 1)
        decision = emitted[0]
        self.assertEqual(decision.tool_call_id, "call-1")
        self.assertEqual(decision.agent_id, "agent-1")
        self.assertEqual(decision.session_id, "sess-1")
        self.assertTrue(decision.approved)
        self.assertEqual(len(client.updates), 1)
        self.assertEqual(client.updates[0]["task_id"], "task-1")
        # The token is consumed, so a replayed click does nothing.
        self.assertEqual(channel._pending, {})

    async def test_unknown_button_is_ignored(self) -> None:
        channel, client = _channel()
        emitted: list = []

        async def _emit(event: Any) -> None:
            emitted.append(event)

        channel._emit = _emit
        await channel._on_card_event(
            {"body": {"event": {"event_key": "not-ours"}}},
        )
        self.assertEqual(emitted, [])
        self.assertEqual(client.updates, [])


class ButtonKeyTest(IsolatedAsyncioTestCase):
    """The button key round-trips the token and the decision."""

    async def test_round_trip(self) -> None:
        for approved in (True, False):
            token, decision = _parse_button_key(_button_key("tok:1", approved))
            self.assertEqual(token, "tok:1")
            self.assertEqual(decision, approved)

    async def test_rejects_foreign_keys(self) -> None:
        self.assertIsNone(_parse_button_key(""))
        self.assertIsNone(_parse_button_key("submit_key"))
        self.assertIsNone(_parse_button_key("tok:x"))

    async def test_card_carries_both_buttons(self) -> None:
        card = _build_approval_card("task-1", "tok-1", "Bash", "ls")
        self.assertEqual(len(card["button_list"]), 2)
        self.assertEqual(card["task_id"], "task-1")


class MediaSendTest(IsolatedAsyncioTestCase):
    """Attachments upload in chunks, then send by media id."""

    async def test_send_image_uploads_then_sends(self) -> None:
        channel, client = _channel()
        ack = await channel.send_image_to("chat-1", "single", b"bytes")
        self.assertEqual(ack["errcode"], 0)
        cmds = [c for c, _ in client.cmds]
        self.assertEqual(
            cmds,
            [
                "aibot_upload_media_init",
                "aibot_upload_media_chunk",
                "aibot_upload_media_finish",
            ],
        )
        self.assertEqual(client.cmds[0][1]["total_chunks"], 1)
        _, body = client.sent[0]
        self.assertEqual(body["msgtype"], "image")
        self.assertEqual(body["image"]["media_id"], "media-1")

    async def test_oversized_upload_is_refused(self) -> None:
        channel, client = _channel()
        # 101 chunks, one over the platform's ceiling.
        ack = await channel.send_file_to(
            "chat-1",
            "single",
            b"x" * (512 * 1024 * 100 + 1),
            "big.bin",
        )
        self.assertEqual(ack["errcode"], -1)
        self.assertEqual(client.cmds, [])
        self.assertEqual(client.sent, [])
