# -*- coding: utf-8 -*-
"""Tests for the optional AgentScope Textual UI."""

# Test names describe behavior; fixtures also intentionally exercise private
# widgets because the public ChatUI composes them internally.
# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=protected-access

import asyncio
from collections.abc import AsyncGenerator
import json
from typing import Any
import unittest
from unittest.mock import patch

from textual.app import App, ComposeResult
from textual.message import Message as TextualMessage
from textual.widgets import Collapsible, Input, OptionList, Static

from agentscope.event import (
    ExternalExecutionResultEvent,
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
    HintBlock,
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.permission import PermissionBehavior, PermissionRule
from agentscope.tool import AskUser
from agentscope.tui import ChatUI, MessagesUI
from agentscope.tui._ask_user import AskUserUI
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
            self.assertEqual(thinking._title.collapsed_symbol, "→")
            self.assertEqual(thinking._title.expanded_symbol, "↓")

    async def test_hint_uses_shared_disclosure_arrows(self) -> None:
        msg = AssistantMsg(
            name="agent",
            content=[HintBlock(id="hint-1", hint="Use the shared arrow")],
        )
        app = _MessagesApp([msg])
        async with app.run_test() as pilot:
            await pilot.pause()
            hint = app.query_one(".as-hint", Collapsible)

            self.assertEqual(hint._title.collapsed_symbol, "→")
            self.assertEqual(hint._title.expanded_symbol, "↓")
            self.assertEqual(hint.styles.margin.top, 0)
            self.assertEqual(hint.styles.margin.bottom, 1)

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
                    message_uis = list(chat.query(MessageUI))
                    tool_group = chat.query_one(ToolGroupUI)
                    text_block = chat.query_one(TextBlockUI)
                    footer = chat.query_one(".as-message-footer", Static)
                    messages_ui = chat.query_one(MessagesUI)
                    editor = chat.query_one(_ComposerTextArea)

                    self.assertEqual(
                        (chat.region.width, chat.region.height),
                        size,
                    )
                    self.assertEqual(
                        message_uis[0].region.x,
                        message_uis[1].region.x,
                    )
                    self.assertEqual(
                        message_uis[0].region.x,
                        chat.content_region.x + 1,
                    )
                    self.assertLessEqual(
                        message_uis[0].region.right,
                        chat.content_region.right - 1,
                    )
                    self.assertEqual(
                        message_uis[1]
                        .query_one(".as-message-header", Static)
                        .region.x,
                        text_block.region.x,
                    )
                    self.assertEqual(
                        message_uis[1]
                        .query_one(".as-message-header", Static)
                        .styles.margin.bottom,
                        1,
                    )
                    self.assertEqual(message_uis[0].styles.margin.bottom, 0)
                    self.assertEqual(
                        len(tool_group.query(Collapsible)),
                        0,
                    )
                    self.assertEqual(tool_group.styles.margin.top, 0)
                    self.assertEqual(tool_group.styles.margin.bottom, 1)
                    self.assertIn("✓", str(tool_group.title))
                    self.assertFalse(footer.display)
                    self.assertEqual(
                        str(messages_ui.styles.scrollbar_visibility),
                        "hidden",
                    )
                    self.assertEqual(editor.styles.background.a, 0)
                    self.assertEqual(editor.region.height, 1)
                    self.assertEqual(
                        str(editor.styles.scrollbar_visibility),
                        "hidden",
                    )
                    self.assertEqual(
                        len(composer.query(".as-section-rule")),
                        2,
                    )
                    for rule in composer.query(".as-section-rule"):
                        self.assertEqual(rule.region.x, chat.region.x)
                        self.assertEqual(rule.region.width, chat.region.width)
                    self.assertEqual(
                        message_uis[0]._header_text().title.plain,
                        "user",
                    )
                    self.assertEqual(
                        message_uis[1]._header_text().title.plain,
                        "agent",
                    )
                    self.assertNotIn("YOU", screenshot)
                    self.assertNotIn("AGENT", screenshot)
                    tool_group.collapsed = False
                    await pilot.pause()
                    self.assertTrue(editor.has_focus)
                    self.assertLess(
                        tool_group.region.height,
                        chat.region.height,
                    )
                    self.assertLessEqual(composer.region.right, size[0])
                    self.assertLessEqual(composer.region.bottom, size[1])
                    self.assertEqual(len(chat.query("Button")), 0)
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

    async def test_ctrl_c_interrupts_running_reply(self) -> None:
        observed: list[ChatUI.InterruptRequested] = []

        def hook(message: TextualMessage) -> None:
            if isinstance(message, ChatUI.InterruptRequested):
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

            hint = app.query_one("#as-composer-hint", Static)
            self.assertIn("Ctrl+C interrupt", str(hint.render()))
            app.query_one(_ComposerTextArea).focus()
            await pilot.press("ctrl+c")
            await pilot.pause()

            self.assertEqual(len({id(message) for message in observed}), 1)
            self.assertEqual(observed[-1].reply_id, "running")

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
            editor = app.query_one(_ComposerTextArea)
            editor.focus()
            await pilot.press("a", "shift+enter", "b")
            await pilot.pause()
            self.assertEqual(editor.region.height, 2)
            await pilot.press("enter")
            await pilot.pause()

            self.assertEqual(observed[-1].msg.get_text_content(), "a\nb")

    async def test_composer_growth_stops_at_hidden_scroll_cap(self) -> None:
        app = _ChatApp()
        async with app.run_test() as pilot:
            editor = app.query_one(_ComposerTextArea)
            editor.focus()
            keys = ["a"]
            for _ in range(12):
                keys.extend(("shift+enter", "a"))
            await pilot.press(*keys)
            await pilot.pause()

            self.assertEqual(editor.region.height, 10)
            self.assertEqual(
                str(editor.styles.scrollbar_visibility),
                "hidden",
            )

    async def test_hitl_uses_keyboard_selection_and_restores_draft(
        self,
    ) -> None:
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
            hitl = app.query_one(HitlUI)
            self.assertTrue(hitl.display)
            tool_group = app.query_one(ToolGroupUI)
            self.assertIn("[cyan]→", str(tool_group.title))
            self.assertEqual(tool_group._title.collapsed_symbol, "")
            self.assertEqual(tool_group._title.expanded_symbol, "")
            self.assertEqual(len(hitl.query(".as-section-rule")), 1)
            options = app.query_one(OptionList)
            self.assertTrue(options.has_focus)
            self.assertEqual(options.highlighted, 0)
            self.assertEqual(options.region.height, options.option_count)
            self.assertTrue(
                str(options.get_option_at_index(0).prompt).startswith("→ 1."),
            )
            await pilot.press("down")
            await pilot.pause()
            self.assertTrue(
                str(options.get_option_at_index(0).prompt).startswith("  1."),
            )
            self.assertTrue(
                str(options.get_option_at_index(1).prompt).startswith("→ 2."),
            )
            await pilot.press("enter")
            await pilot.pause()

            self.assertEqual(len({id(message) for message in observed}), 1)
            value = observed[-1].value
            self.assertIsInstance(value, UserConfirmResultEvent)
            self.assertEqual(value.reply_id, "r1")
            self.assertFalse(value.confirm_results[0].confirmed)

            self.assertTrue(app.query_one(ComposerUI).display)
            self.assertFalse(app.query_one(HitlUI).display)
            self.assertEqual(editor.text, "draft")
            self.assertTrue(editor.has_focus)

    async def test_next_hitl_request_keeps_keyboard_focus(self) -> None:
        observed: list[ChatUI.Confirmed] = []

        def hook(message: TextualMessage) -> None:
            if isinstance(message, ChatUI.Confirmed):
                observed.append(message)

        app = _ChatApp(
            [
                AssistantMsg(
                    name="agent",
                    id="reply-1",
                    content=[
                        ToolCallBlock(
                            id="call-1",
                            name="Read",
                            input="{}",
                            state="asking",
                        ),
                        ToolCallBlock(
                            id="call-2",
                            name="Bash",
                            input="{}",
                            state="asking",
                        ),
                    ],
                ),
            ],
        )
        async with app.run_test(message_hook=hook) as pilot:
            options = app.query_one(OptionList)
            self.assertTrue(options.has_focus)
            await pilot.press("enter")
            await pilot.pause()

            self.assertTrue(app.query_one(HitlUI).display)
            self.assertTrue(options.has_focus)
            self.assertEqual(options.highlighted, 0)
            self.assertIn(
                "Bash",
                str(app.query_one("#as-hitl-body", Static).render()),
            )

    async def test_permission_rules_are_embedded_in_always_option(
        self,
    ) -> None:
        app = _ChatApp()
        async with app.run_test() as pilot:
            hitl = app.query_one(HitlUI)
            hitl.set_pending(
                [
                    (
                        "r1",
                        "agent",
                        ToolCallBlock(
                            id="c1",
                            name="Bash",
                            input='{"command": "git status"}',
                            state="asking",
                            suggested_rules=[
                                PermissionRule(
                                    tool_name="Bash",
                                    rule_content="git status",
                                    behavior=PermissionBehavior.ALLOW,
                                    source="tool",
                                ),
                            ],
                        ),
                    ),
                ],
            )
            await pilot.pause()

            options = app.query_one(OptionList)
            always = options.get_option_at_index(1)
            body = app.query_one("#as-hitl-body", Static)
            self.assertIn("allow Bash (git status)", str(always.prompt))
            self.assertNotIn("permission rules", str(body.render()).lower())

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
            options = app.query_one(OptionList)
            self.assertEqual(options.option_count, 1)
            self.assertEqual(
                options.get_option_at_index(0).id,
                "interrupt",
            )

    async def test_ask_user_collects_schema_valid_answers(self) -> None:
        observed: list[ChatUI.ExternalExecutionSubmitted] = []

        def hook(message: TextualMessage) -> None:
            if isinstance(message, ChatUI.ExternalExecutionSubmitted):
                observed.append(message)

        questions = [
            {
                "header": "Version",
                "question": "Which version should we install?",
                "context": "The current version is too old.",
                "options": [
                    {
                        "label": "Latest (Recommended)",
                        "description": "Upgrade to the supported release.",
                        "preview": "current: 4.5\nnext: 5.2",
                    },
                    {
                        "label": "Keep current",
                        "description": "Continue without the integration.",
                    },
                ],
            },
            {
                "header": "Features",
                "question": "Which features should be enabled?",
                "options": [
                    {
                        "label": "Rendering",
                        "description": "Enable the render pipeline.",
                    },
                    {
                        "label": "Export",
                        "description": "Enable file export.",
                    },
                ],
                "multi_select": True,
            },
        ]
        app = _ChatApp(
            [
                AssistantMsg(
                    name="agent",
                    id="reply",
                    content=[
                        ToolCallBlock(
                            id="ask",
                            name="AskUser",
                            input=json.dumps({"questions": questions}),
                            state="submitted",
                        ),
                    ],
                ),
            ],
        )
        async with app.run_test(message_hook=hook, size=(100, 30)) as pilot:
            ask_user = app.query_one(AskUserUI)
            options = app.query_one(".as-ask-user-options", OptionList)
            self.assertTrue(ask_user.display)
            self.assertFalse(app.query_one(ComposerUI).display)
            self.assertFalse(app.query_one(HitlUI).display)
            self.assertTrue(options.has_focus)
            self.assertIn(
                "Upgrade to the supported release.",
                str(options.get_option_at_index(0).prompt),
            )
            self.assertTrue(
                app.query_one(".as-ask-user-preview", Static).display,
            )

            await pilot.press("enter")
            await pilot.pause()
            self.assertIn(
                "Enable the render pipeline.",
                str(options.get_option_at_index(0).prompt),
            )
            await pilot.press("enter", "down", "down", "down", "enter")
            await pilot.pause()

            unique = {id(message): message for message in observed}
            self.assertEqual(len(unique), 1)
            value = next(iter(unique.values())).value
            self.assertIsInstance(value, ExternalExecutionResultEvent)
            result = value.execution_results[0]
            self.assertEqual(
                result.metadata,
                {
                    "answers": [
                        {
                            "question": "Which version should we install?",
                            "selected": ["Latest (Recommended)"],
                            "other": None,
                        },
                        {
                            "question": "Which features should be enabled?",
                            "selected": ["Rendering"],
                            "other": None,
                        },
                    ],
                },
            )
            await AskUser().check_external_result(result)
            self.assertFalse(ask_user.display)
            self.assertTrue(app.query_one(ComposerUI).display)

    async def test_ask_user_accepts_other_text(self) -> None:
        observed: list[ChatUI.ExternalExecutionSubmitted] = []

        def hook(message: TextualMessage) -> None:
            if isinstance(message, ChatUI.ExternalExecutionSubmitted):
                observed.append(message)

        tool_call = ToolCallBlock(
            id="ask",
            name="AskUser",
            input=json.dumps(
                {
                    "questions": [
                        {
                            "header": "Approach",
                            "question": "Which approach should we use?",
                            "options": [
                                {"label": "A", "description": "First."},
                                {"label": "B", "description": "Second."},
                            ],
                        },
                    ],
                },
            ),
            state="submitted",
        )
        app = _ChatApp(
            [AssistantMsg(name="agent", id="reply", content=[tool_call])],
        )
        async with app.run_test(message_hook=hook) as pilot:
            await pilot.press("down", "down", "enter")
            await pilot.pause()
            other = app.query_one(".as-ask-user-other", Input)
            self.assertTrue(other.display)
            self.assertTrue(other.has_focus)
            await pilot.press(*"custom plan", "enter")
            await pilot.pause()

            result = observed[0].value.execution_results[0]
            self.assertEqual(
                result.metadata["answers"][0],
                {
                    "question": "Which approach should we use?",
                    "selected": [],
                    "other": "custom plan",
                },
            )
            await AskUser().check_external_result(result)

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

    async def test_edit_shows_diff_and_read_shows_only_result(self) -> None:
        finished_at = "2026-01-01T00:00:01+00:00"
        msg = AssistantMsg(
            name="agent",
            finished_at=finished_at,
            content=[
                ToolCallBlock(
                    id="edit",
                    name="Edit",
                    input='{"file_path": "demo.py", "old_str": "old"}',
                    state="finished",
                ),
                ToolResultBlock(
                    id="edit",
                    name="Edit",
                    output="Successfully updated demo.py",
                    state=ToolResultState.SUCCESS,
                    metadata={"diff": "@@ -1 +1 @@\n-old\n+new\n"},
                ),
                ToolCallBlock(
                    id="read",
                    name="Read",
                    input='{"file_path": "demo.py"}',
                    state="finished",
                ),
                ToolResultBlock(
                    id="read",
                    name="Read",
                    output="     1\tprint('ready')",
                    state=ToolResultState.SUCCESS,
                ),
            ],
        )
        app = _MessagesApp([msg])
        async with app.run_test(size=(100, 24)) as pilot:
            app.query_one(ToolGroupUI).collapsed = False
            await pilot.pause()
            bodies = list(app.query(".as-tool-body"))
            edit_items = bodies[0].render()._renderable.renderables
            read_items = bodies[1].render()._renderable.renderables

            self.assertEqual(
                getattr(edit_items[-1], "code", ""),
                "@@ -1 +1 @@\n-old\n+new\n",
            )
            self.assertEqual(len(read_items), 1)
            self.assertEqual(
                getattr(read_items[0], "plain", ""),
                "     1\tprint('ready')",
            )
            self.assertEqual(bodies[0].styles.padding.left, 1)
            self.assertEqual(bodies[1].styles.padding.left, 1)

    async def test_tool_group_uses_worst_result_state(self) -> None:
        finished_at = "2026-01-01T00:00:01+00:00"
        msg = AssistantMsg(
            name="agent",
            finished_at=finished_at,
            content=[
                ToolCallBlock(
                    id="one",
                    name="Bash",
                    input="{}",
                    state="finished",
                ),
                ToolResultBlock(
                    id="one",
                    name="Bash",
                    output="ok",
                    state=ToolResultState.SUCCESS,
                ),
                ToolCallBlock(
                    id="two",
                    name="Bash",
                    input="{}",
                    state="finished",
                ),
                ToolResultBlock(
                    id="two",
                    name="Bash",
                    output="failed",
                    state=ToolResultState.ERROR,
                ),
            ],
        )
        app = _MessagesApp([msg])
        async with app.run_test() as pilot:
            tool_group = app.query_one(ToolGroupUI)
            self.assertIn("[red]→ ✗", str(tool_group.title))

            tool_group.collapsed = False
            await pilot.pause()
            self.assertIn("[red]↓ ✗", str(tool_group.title))


class _FakeTarget:
    def __init__(self) -> None:
        self.done = asyncio.Event()
        self.inputs: list[Any] = []

    async def reply_stream(
        self,
        inputs: Any,
    ) -> AsyncGenerator[Any, None]:
        self.inputs.append(inputs)
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
            self.assertEqual(target.inputs[0].get_text_content(), "hi")

    async def test_launcher_forwards_ask_user_result(self) -> None:
        target = _FakeTarget()
        app = _AgentScopeTUI(target, [], "user")
        value = ExternalExecutionResultEvent(
            reply_id="reply",
            execution_results=[
                ToolResultBlock(
                    id="ask",
                    name="AskUser",
                    output="Selected A",
                    state=ToolResultState.SUCCESS,
                    metadata={
                        "answers": [
                            {
                                "question": "Which?",
                                "selected": ["A"],
                                "other": None,
                            },
                        ],
                    },
                ),
            ],
        )
        async with app.run_test(size=(80, 24)):
            app._on_external_execution_submitted(
                ChatUI.ExternalExecutionSubmitted(value),
            )
            await asyncio.wait_for(target.done.wait(), timeout=1)

            self.assertIs(target.inputs[0], value)

    def test_exit_command_quits_without_forwarding_to_target(self) -> None:
        target = _FakeTarget()
        app = _AgentScopeTUI(target, [], "user")
        event = ChatUI.Submitted(UserMsg(name="user", content="  /EXIT  "))

        with patch.object(app, "exit") as exit_app:
            app._on_submitted(event)

        exit_app.assert_called_once_with()
        self.assertEqual(target.inputs, [])
        self.assertEqual(app.BINDINGS, [])


if __name__ == "__main__":
    unittest.main()
