# -*- coding: utf-8 -*-
"""Tests for the optional AgentScope Textual UI."""

# Test names describe behavior; fixtures also intentionally exercise private
# widgets because the public ChatUI composes them internally.
# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=protected-access

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
import unittest

from textual.app import App, ComposeResult
from textual.message import Message as TextualMessage

from agentscope.event import (
    ReplyEndEvent,
    ReplyStartEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    UserConfirmResultEvent,
)
from agentscope.message import (
    AssistantMsg,
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.tui import ChatUI, MessagesUI
from agentscope.tui._chat import ComposerUI, HitlUI, _ComposerTextArea
from agentscope.tui._launcher import _AgentScopeTUI
from agentscope.tui._messages import (
    MessageUI,
    TextBlockUI,
    ThinkingUI,
    ToolGroupUI,
)


class _MessagesApp(App):
    def __init__(self, messages: list[Msg]) -> None:
        super().__init__()
        self.initial_messages = messages

    def compose(self) -> ComposeResult:
        yield MessagesUI(self.initial_messages, id="messages")


class _ChatApp(App):
    def __init__(self, messages: list[Msg] | None = None) -> None:
        super().__init__()
        self.initial_messages = messages or []

    def compose(self) -> ComposeResult:
        yield ChatUI(self.initial_messages, id="chat")


class MessagesUITest(unittest.IsolatedAsyncioTestCase):
    async def test_running_thinking_updates_elapsed_title(self) -> None:
        msg = AssistantMsg(
            name="agent",
            id="reply-1",
            content=[
                ThinkingBlock(
                    id="thinking-1",
                    thinking="Working",
                ),
            ],
        )
        app = _MessagesApp([msg])
        async with app.run_test() as pilot:
            thinking = app.query_one(ThinkingUI)
            thinking._update_title()
            await pilot.pause()

            self.assertTrue(str(thinking.title).startswith("◌ Thinking"))

    async def test_full_snapshot_keeps_unchanged_message_widget(self) -> None:
        msg = UserMsg(name="user", content="first", id="user-1")
        app = _MessagesApp([msg])
        async with app.run_test() as pilot:
            messages_ui = app.query_one(MessagesUI)
            original = app.query_one(MessageUI)
            updated = UserMsg(name="user", content="updated", id="user-1")

            await messages_ui.set_messages([updated])
            await pilot.pause()

            self.assertIs(original, app.query_one(MessageUI))
            self.assertEqual(
                messages_ui.messages[0].get_text_content(),
                "updated",
            )
            self.assertEqual(app.query_one(TextBlockUI).source, "updated")

    async def test_interleaved_reply_events_are_isolated_by_reply_id(
        self,
    ) -> None:
        app = _MessagesApp([])
        async with app.run_test() as pilot:
            ui = app.query_one(MessagesUI)
            ui.feed(
                ReplyStartEvent(
                    session_id="s",
                    reply_id="r1",
                    name="planner",
                ),
            )
            ui.feed(
                ReplyStartEvent(
                    session_id="s",
                    reply_id="r2",
                    name="executor",
                ),
            )
            ui.feed(TextBlockStartEvent(reply_id="r1", block_id="t1"))
            ui.feed(TextBlockStartEvent(reply_id="r2", block_id="t2"))
            ui.feed(
                TextBlockDeltaEvent(
                    reply_id="r2",
                    block_id="t2",
                    delta="execute",
                ),
            )
            ui.feed(
                TextBlockDeltaEvent(
                    reply_id="r1",
                    block_id="t1",
                    delta="plan",
                ),
            )
            await pilot.pause()

            self.assertEqual([msg.id for msg in ui.messages], ["r1", "r2"])
            self.assertEqual(ui.messages[0].get_text_content(), "plan")
            self.assertEqual(ui.messages[1].get_text_content(), "execute")

    async def test_streaming_text_updates_only_its_block(self) -> None:
        app = _MessagesApp([])
        async with app.run_test() as pilot:
            ui = app.query_one(MessagesUI)
            ui.feed(
                ReplyStartEvent(
                    session_id="s",
                    reply_id="r1",
                    name="agent",
                ),
            )
            ui.feed(TextBlockStartEvent(reply_id="r1", block_id="t1"))
            await pilot.pause()
            message_widget = app.query_one(MessageUI)
            text_widget = app.query_one(TextBlockUI)

            ui.feed(
                TextBlockDeltaEvent(
                    reply_id="r1",
                    block_id="t1",
                    delta="hello",
                ),
            )
            ui.feed(TextBlockEndEvent(reply_id="r1", block_id="t1"))
            ui.feed(ReplyEndEvent(session_id="s", reply_id="r1"))
            await pilot.pause()

            self.assertIs(message_widget, app.query_one(MessageUI))
            self.assertIs(text_widget, app.query_one(TextBlockUI))
            self.assertEqual(ui.messages[0].get_text_content(), "hello")


class ChatUITest(unittest.IsolatedAsyncioTestCase):
    async def test_visual_layout_at_supported_terminal_sizes(self) -> None:
        finished_at = "2026-01-01T00:00:01+00:00"
        history: list[Msg] = [
            UserMsg(name="user", content="Explain the change", id="user-1"),
            AssistantMsg(
                name="agent",
                id="reply-1",
                finished_at=finished_at,
                content=[
                    ThinkingBlock(
                        id="thinking-1",
                        thinking="Check the implementation.",
                        finished_at=finished_at,
                    ),
                    TextBlock(
                        id="text-1",
                        text="## Result\n\nThe update is **ready**.",
                        finished_at=finished_at,
                    ),
                    ToolCallBlock(
                        id="edit-1",
                        name="Edit",
                        input='{"file_path": "demo.py"}',
                        state="finished",
                        finished_at=finished_at,
                    ),
                    ToolResultBlock(
                        id="edit-1",
                        name="Edit",
                        output="updated",
                        state="success",
                        metadata={"diff": "@@ -1 +1 @@\n-old\n+new\n"},
                        finished_at=finished_at,
                    ),
                ],
            ),
        ]

        for size in ((120, 40), (80, 24), (50, 20)):
            with self.subTest(size=size):
                app = _ChatApp(history)
                async with app.run_test(size=size) as pilot:
                    await pilot.pause()
                    chat = app.query_one(ChatUI)
                    composer = app.query_one(ComposerUI)
                    screenshot = app.export_screenshot(simplify=True)

                    self.assertEqual(
                        (chat.region.width, chat.region.height),
                        size,
                    )
                    self.assertLessEqual(composer.region.right, size[0])
                    self.assertLessEqual(composer.region.bottom, size[1])
                    self.assertIn("<svg", screenshot)

    async def test_running_reply_keeps_composer_available(self) -> None:
        observed: list[ChatUI.Submitted] = []

        def hook(message: TextualMessage) -> None:
            if isinstance(message, ChatUI.Submitted):
                observed.append(message)

        app = _ChatApp()
        async with app.run_test(message_hook=hook) as pilot:
            chat = app.query_one(ChatUI)
            chat.feed(
                ReplyStartEvent(
                    session_id="s",
                    reply_id="running",
                    name="agent",
                ),
            )
            await pilot.pause()
            composer = app.query_one(ComposerUI)
            self.assertTrue(composer.display)
            self.assertFalse(app.query_one(_ComposerTextArea).disabled)

            app.query_one(_ComposerTextArea).focus()
            await pilot.press("h", "i", "enter")
            await pilot.pause()

            # Textual's message hook sees the same bubbling message at each
            # pump; there must still be only one logical Submitted instance.
            self.assertEqual(len({id(message) for message in observed}), 1)
            self.assertEqual(observed[-1].msg.get_text_content(), "hi")

    async def test_explicit_input_disable(self) -> None:
        app = _ChatApp()
        async with app.run_test() as pilot:
            chat = app.query_one(ChatUI)
            chat.input_enabled = False
            await pilot.pause()
            self.assertTrue(app.query_one(ComposerUI).display)
            self.assertTrue(app.query_one(_ComposerTextArea).disabled)

    async def test_shift_enter_inserts_newline_before_submit(self) -> None:
        observed: list[ChatUI.Submitted] = []

        def hook(message: TextualMessage) -> None:
            if isinstance(message, ChatUI.Submitted):
                observed.append(message)

        app = _ChatApp()
        async with app.run_test(message_hook=hook) as pilot:
            app.query_one(_ComposerTextArea).focus()
            await pilot.press("a", "shift+enter", "b", "enter")
            await pilot.pause()

            self.assertEqual(observed[-1].msg.get_text_content(), "a\nb")

    async def test_hitl_replaces_composer_and_posts_confirmation(self) -> None:
        observed: list[ChatUI.Confirmed] = []

        def hook(message: TextualMessage) -> None:
            if isinstance(message, ChatUI.Confirmed):
                observed.append(message)

        app = _ChatApp()
        async with app.run_test(message_hook=hook) as pilot:
            chat = app.query_one(ChatUI)
            editor = app.query_one(_ComposerTextArea)
            editor.focus()
            await pilot.press("d", "r", "a", "f", "t")
            chat.feed(
                ReplyStartEvent(
                    session_id="s",
                    reply_id="r1",
                    name="agent",
                ),
            )
            chat.feed(
                ToolCallStartEvent(
                    reply_id="r1",
                    tool_call_id="c1",
                    tool_call_name="Edit",
                ),
            )
            chat.feed(
                ToolCallDeltaEvent(
                    reply_id="r1",
                    tool_call_id="c1",
                    delta='{"file_path": "demo.py"}',
                ),
            )
            chat.feed(ToolCallEndEvent(reply_id="r1", tool_call_id="c1"))
            chat.feed(
                RequireUserConfirmEvent(
                    reply_id="r1",
                    tool_calls=[
                        ToolCallBlock(
                            id="c1",
                            name="Edit",
                            input='{"file_path": "demo.py"}',
                        ),
                    ],
                ),
            )
            await pilot.pause()

            self.assertFalse(app.query_one(ComposerUI).display)
            self.assertTrue(app.query_one(HitlUI).display)
            await pilot.click("#as-allow")
            await pilot.pause()

            self.assertEqual(len({id(message) for message in observed}), 1)
            value = observed[-1].value
            self.assertIsInstance(value, UserConfirmResultEvent)
            self.assertEqual(value.reply_id, "r1")
            self.assertTrue(value.confirm_results[0].confirmed)

            chat.feed(value)
            await pilot.pause()
            self.assertTrue(app.query_one(ComposerUI).display)
            self.assertFalse(app.query_one(HitlUI).display)
            self.assertEqual(editor.text, "draft")
            self.assertTrue(editor.has_focus)

    async def test_external_execution_waiting_replaces_composer(self) -> None:
        app = _ChatApp()
        async with app.run_test() as pilot:
            chat = app.query_one(ChatUI)
            chat.feed(
                ReplyStartEvent(
                    session_id="s",
                    reply_id="r1",
                    name="agent",
                ),
            )
            chat.feed(
                ToolCallStartEvent(
                    reply_id="r1",
                    tool_call_id="c1",
                    tool_call_name="external_tool",
                ),
            )
            chat.feed(ToolCallEndEvent(reply_id="r1", tool_call_id="c1"))
            chat.feed(
                RequireExternalExecutionEvent(
                    reply_id="r1",
                    tool_calls=[
                        ToolCallBlock(
                            id="c1",
                            name="external_tool",
                            input="{}",
                        ),
                    ],
                ),
            )
            await pilot.pause()

            self.assertFalse(app.query_one(ComposerUI).display)
            self.assertFalse(app.query_one("#as-allow").display)
            self.assertTrue(app.query_one("#as-hitl-abort").display)

    async def test_edit_tool_uses_authoritative_diff_stats(self) -> None:
        app = _ChatApp()
        async with app.run_test() as pilot:
            chat = app.query_one(ChatUI)
            chat.feed(
                ReplyStartEvent(
                    session_id="s",
                    reply_id="r1",
                    name="agent",
                ),
            )
            chat.feed(
                ToolCallStartEvent(
                    reply_id="r1",
                    tool_call_id="c1",
                    tool_call_name="Edit",
                ),
            )
            chat.feed(
                ToolCallDeltaEvent(
                    reply_id="r1",
                    tool_call_id="c1",
                    delta='{"file_path": "demo.py"}',
                ),
            )
            chat.feed(ToolCallEndEvent(reply_id="r1", tool_call_id="c1"))
            chat.feed(
                ToolResultStartEvent(
                    reply_id="r1",
                    tool_call_id="c1",
                    tool_call_name="Edit",
                ),
            )
            chat.feed(
                ToolResultEndEvent(
                    reply_id="r1",
                    tool_call_id="c1",
                    state=ToolResultState.SUCCESS,
                    metadata={"diff": "@@ -1 +1 @@\n-old\n+new\n"},
                ),
            )
            await pilot.pause()

            self.assertIn("+1 -1", app.query_one(ToolGroupUI).title)


class _FakeTarget:
    def __init__(self) -> None:
        self.done = asyncio.Event()

    async def reply_stream(
        self,
        inputs: Any,
    ) -> AsyncGenerator[Any, None]:
        del inputs
        yield ReplyStartEvent(
            session_id="s",
            reply_id="reply",
            name="agent",
        )
        yield TextBlockStartEvent(reply_id="reply", block_id="text")
        yield TextBlockDeltaEvent(
            reply_id="reply",
            block_id="text",
            delta="response",
        )
        yield TextBlockEndEvent(reply_id="reply", block_id="text")
        yield ReplyEndEvent(session_id="s", reply_id="reply")
        self.done.set()


class LauncherTest(unittest.IsolatedAsyncioTestCase):
    async def test_launcher_forwards_submission_and_streams_reply(
        self,
    ) -> None:
        target = _FakeTarget()
        app = _AgentScopeTUI(target, [], "user")
        async with app.run_test(size=(80, 24)) as pilot:
            editor = app.query_one(_ComposerTextArea)
            editor.focus()
            await pilot.press("h", "i", "enter")
            await asyncio.wait_for(target.done.wait(), timeout=1)
            await pilot.pause()

            messages = app.query_one(ChatUI).messages
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0].get_text_content(), "hi")
            self.assertEqual(messages[1].get_text_content(), "response")


if __name__ == "__main__":
    unittest.main()
