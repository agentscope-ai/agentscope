# -*- coding: utf-8 -*-
"""Tests for the service-owned slash command registry."""

from typing import Any, cast
from unittest import IsolatedAsyncioTestCase, TestCase

from agentscope.app._command import (
    CommandContext,
    dispatch_command,
    list_commands,
    parse_command,
)
from agentscope.app._router._chat import chat as chat_route
from agentscope.app._router._command import commands as command_list_route
from agentscope.app._router._schema import ChatRequest
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import SessionConfig, SessionRecord
from agentscope.event import ConfirmResult, UserConfirmResultEvent
from agentscope.message import (
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolCallState,
    UserMsg,
)
from agentscope.state import AgentState


class SlashCommandRegistryTest(TestCase):
    """Exercise command discovery and strict input recognition."""

    def test_registry_exposes_clear_metadata(self) -> None:
        """The discovery registry has one stable built-in command."""
        self.assertEqual(
            list_commands(),
            (
                list_commands()[0].__class__(
                    name="clear",
                    description="Clear the current conversation context",
                ),
            ),
        )

    def test_parser_recognizes_plain_user_command(self) -> None:
        """Whitespace and command case are normalized."""
        message = UserMsg(
            name="user",
            content=[TextBlock(text="  /ClEaR  ")],
        )

        match = parse_command(message)

        self.assertIsNotNone(match)
        self.assertEqual(match.spec, list_commands()[0])
        self.assertEqual(match.args, "")
        self.assertIs(match.message, message)

    def test_parser_preserves_arguments_for_validation(self) -> None:
        """The router receives the unconsumed argument string."""
        match = parse_command(
            [UserMsg(name="user", content="/clear now please")],
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.args, "now please")

    def test_non_commands_fall_through(self) -> None:
        """Unknown, escaped, and non-plain inputs remain chat input."""
        values = [
            UserMsg(name="user", content="/unknown"),
            UserMsg(name="user", content="//clear"),
            UserMsg(name="user", content="/"),
            UserMsg(
                name="user",
                content=[
                    TextBlock(text="/clear"),
                    TextBlock(text="attached content"),
                ],
            ),
        ]

        self.assertEqual(
            [parse_command(value) for value in values],
            [None] * 4,
        )


class SlashCommandRouteTest(IsolatedAsyncioTestCase):
    """Verify commands are handled before chat execution."""

    async def test_discovery_matches_registry(self) -> None:
        """The discovery endpoint serializes the registry contract."""
        response = await command_list_route()

        self.assertEqual(
            response.model_dump(),
            {
                "commands": [
                    {
                        "name": "clear",
                        "command": "/clear",
                        "aliases": [],
                        "description": (
                            "Clear the current conversation context"
                        ),
                        "accepts_args": False,
                    },
                ],
            },
        )

    async def test_clear_bypasses_chat_run(self) -> None:
        """The chat route delegates clear directly to SessionService."""

        class _ClearService:
            """Record clear calls made by the route."""

            def __init__(self) -> None:
                self.calls: list[tuple[str, str, str]] = []

            async def clear_conversation(
                self,
                user_id: str,
                agent_id: str,
                session_id: str,
            ) -> tuple[str, ...]:
                """Record and complete a clear command."""
                self.calls.append((user_id, agent_id, session_id))
                return (session_id,)

        service = _ClearService()
        unused = cast(Any, object())

        response = await chat_route(
            ChatRequest(
                agent_id="agent",
                session_id="session",
                input=UserMsg(name="user", content="/clear"),
            ),
            user_id="user",
            chat_service=unused,
            chat_run_registry=unused,
            message_bus=unused,
            storage=unused,
            session_service=cast(Any, service),
        )

        self.assertEqual(service.calls, [("user", "agent", "session")])
        self.assertEqual(
            response.model_dump(),
            {
                "status": "command_completed",
                "session_id": "session",
                "command": "clear",
            },
        )

    async def test_dispatcher_returns_structured_result(self) -> None:
        """The shared dispatcher exposes affected sessions and notice."""

        class _ClearService:
            """Return a deterministic team clear result."""

            def __init__(self) -> None:
                self.assertions: tuple[str, str, str] | None = None

            async def clear_conversation(
                self,
                user_id: str,
                agent_id: str,
                session_id: str,
            ) -> tuple[str, ...]:
                """Return the root and one worker target."""
                self.assertions = (user_id, agent_id, session_id)
                return (session_id, "worker")

        message = UserMsg(name="user", content="/clear")
        match = parse_command(message)
        self.assertIsNotNone(match)
        service = _ClearService()

        result = await dispatch_command(
            match,
            CommandContext(
                user_id="user",
                agent_id="agent",
                session_id="session",
                source="http",
                command_message_id=message.id,
            ),
            cast(Any, service),
        )

        self.assertEqual(service.assertions, ("user", "agent", "session"))
        self.assertEqual(
            result.__dict__,
            {
                "name": "clear",
                "root_session_id": "session",
                "affected_session_ids": ("session", "worker"),
                "message": "Conversation cleared.",
            },
        )


class HitlResumeRouteTest(IsolatedAsyncioTestCase):
    """Stale approvals do not start a run after conversation clear."""

    @staticmethod
    def _request(tool_call: ToolCallBlock) -> ChatRequest:
        """Build one approval request for the supplied tool call."""
        return ChatRequest(
            agent_id="agent",
            session_id="session",
            input=UserConfirmResultEvent(
                reply_id="reply",
                confirm_results=[
                    ConfirmResult(
                        confirmed=True,
                        tool_call=tool_call,
                    ),
                ],
            ),
        )

    async def _trigger(self, session: SessionRecord) -> tuple[Any, list]:
        """Call the route and return its response and queued triggers."""

        class _Storage:
            """Return the session state under test."""

            async def get_session(self, *args: object) -> SessionRecord:
                """Return the configured session."""
                del args
                return session

        tool_call = ToolCallBlock(
            id="tool-call",
            name="shell",
            input="{}",
            state=ToolCallState.ASKING,
        )
        bus = InMemoryMessageBus()
        unused = cast(Any, object())
        response = await chat_route(
            self._request(tool_call),
            user_id="user",
            chat_service=unused,
            chat_run_registry=unused,
            message_bus=bus,
            storage=cast(Any, _Storage()),
            session_service=unused,
        )
        entries = await bus.queue_drain(MessageBusKeys.wakeup_queue())
        return response, entries

    async def test_cleared_approval_is_ignored(self) -> None:
        """An approval whose reply was cleared never reaches dispatcher."""
        session = SessionRecord(
            id="session",
            user_id="user",
            agent_id="agent",
            config=SessionConfig(workspace_id="workspace"),
            state=AgentState(session_id="session"),
            conversation_revision=1,
        )

        response, entries = await self._trigger(session)

        self.assertEqual(
            response.model_dump(),
            {
                "status": "started",
                "session_id": "session",
                "command": None,
            },
        )
        self.assertEqual(entries, [])

    async def test_current_approval_is_enqueued(self) -> None:
        """A matching parked approval keeps the existing resume path."""
        session = SessionRecord(
            id="session",
            user_id="user",
            agent_id="agent",
            config=SessionConfig(workspace_id="workspace"),
            state=AgentState(
                session_id="session",
                reply_id="reply",
                context=[
                    Msg(
                        name="agent",
                        role="assistant",
                        content=[
                            ToolCallBlock(
                                id="tool-call",
                                name="shell",
                                input="{}",
                                state=ToolCallState.ASKING,
                            ),
                        ],
                    ),
                ],
            ),
            conversation_revision=4,
        )

        response, entries = await self._trigger(session)

        self.assertEqual(response.status, "started")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1]["kind"], "resume")
        self.assertEqual(entries[0][1]["conversation_revision"], 4)
