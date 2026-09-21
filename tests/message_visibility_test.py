# -*- coding: utf-8 -*-
"""Tests for the message/event visibility routing."""
from io import StringIO
from unittest import TestCase
from unittest.async_case import IsolatedAsyncioTestCase

from rich.console import Console
from utils import MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.console import ConsoleRenderer
from agentscope.event import ReplyStartEvent, TextBlockDeltaEvent
from agentscope.message import (
    AssistantMsg,
    Msg,
    SystemMsg,
    TextBlock,
    UserMsg,
)
from agentscope.model import ChatResponse
from agentscope.tool import Toolkit
from agentscope.types import Visibility

REPLY_ID = "reply-visibility"


class MessageVisibilityTest(TestCase):
    """The visibility field on messages."""

    def test_messages_are_user_facing_by_default(self) -> None:
        """Every message factory defaults to the main conversation."""
        for msg in (
            Msg(name="a", content=[TextBlock(text="hi")], role="assistant"),
            UserMsg(name="user", content="hi"),
            AssistantMsg(name="a", content="hi"),
            SystemMsg(name="sys", content="hi"),
        ):
            self.assertEqual(msg.visibility, Visibility.USER)

    def test_factories_carry_the_given_visibility(self) -> None:
        """The factories pass the audience through to the message."""
        self.assertEqual(
            UserMsg(
                name="user",
                content="hi",
                visibility=Visibility.INTERNAL,
            ).visibility,
            Visibility.INTERNAL,
        )
        self.assertEqual(
            AssistantMsg(
                name="coder",
                content="print(1)",
                visibility=Visibility.ARTIFACT,
            ).visibility,
            Visibility.ARTIFACT,
        )
        self.assertEqual(
            SystemMsg(
                name="sys",
                content="hi",
                visibility=Visibility.INTERNAL,
            ).visibility,
            Visibility.INTERNAL,
        )

    def test_visibility_survives_a_json_round_trip(self) -> None:
        """A dumped message keeps its audience as a plain string, so a
        frontend reads it off the wire."""
        msg = AssistantMsg(
            name="coder",
            content="print(1)",
            visibility=Visibility.ARTIFACT,
        )
        dumped = msg.model_dump(mode="json")
        self.assertEqual(dumped["visibility"], "artifact")
        self.assertEqual(
            Msg.model_validate(dumped).visibility,
            Visibility.ARTIFACT,
        )


class EventVisibilityTest(TestCase):
    """The visibility field on events."""

    def test_events_are_user_facing_by_default(self) -> None:
        """An event built by hand belongs to the main conversation."""
        event = ReplyStartEvent(
            session_id="s",
            reply_id=REPLY_ID,
            name="Friday",
        )
        self.assertEqual(event.visibility, Visibility.USER)
        self.assertEqual(
            event.model_dump(mode="json")["visibility"],
            "user",
        )

    def test_visibility_is_accepted_as_a_string(self) -> None:
        """A frontend round-tripping an event doesn't lose the audience."""
        event = ReplyStartEvent(
            session_id="s",
            reply_id=REPLY_ID,
            name="Friday",
            visibility="internal",
        )
        self.assertEqual(event.visibility, Visibility.INTERNAL)


def _text_responses(text: str) -> list:
    """A one-chunk streamed model response carrying ``text``."""
    return [
        [
            ChatResponse(content=[TextBlock(text=text)], is_last=False),
            ChatResponse(content=[TextBlock(text=text)], is_last=True),
        ],
    ]


class AgentVisibilityTest(IsolatedAsyncioTestCase):
    """An agent stamps its audience on everything it emits."""

    async def asyncSetUp(self) -> None:
        """Build an agent over a mock model."""
        self.model = MockModel()
        self.agent = Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=self.model,
            toolkit=Toolkit(),
            injection_config=InjectionConfig(inject_runtime_state=False),
        )

    async def _stream(self, **kwargs: object) -> list:
        """Run one reply and collect the events and the final message."""
        self.model.set_responses(_text_responses("Hello"))
        return [
            item
            async for item in self.agent.reply_stream(
                UserMsg(name="user", content="Hi"),
                yield_final_msg=True,
                **kwargs,
            )
        ]

    async def test_default_agent_is_user_facing(self) -> None:
        """Nothing changes for an agent that never mentions visibility."""
        self.assertEqual(self.agent.visibility, Visibility.USER)
        items = await self._stream()
        self.assertTrue(items)
        for item in items:
            self.assertEqual(item.visibility, Visibility.USER)

    async def test_an_internal_agent_stamps_every_event(self) -> None:
        """A worker agent's whole stream is marked, final message included."""
        self.agent.visibility = Visibility.INTERNAL
        items = await self._stream()
        self.assertTrue(any(isinstance(_, Msg) for _ in items))
        for item in items:
            self.assertEqual(item.visibility, Visibility.INTERNAL)

    async def test_per_reply_visibility_overrides_the_agent(self) -> None:
        """One call can differ from the agent's own setting."""
        items = await self._stream(visibility=Visibility.ARTIFACT)
        for item in items:
            self.assertEqual(item.visibility, Visibility.ARTIFACT)
        # The override is per call — the agent itself is untouched.
        self.assertEqual(self.agent.visibility, Visibility.USER)

    async def test_reply_returns_a_stamped_message(self) -> None:
        """The non-streaming entry point carries the audience too."""
        self.model.set_responses(_text_responses("Hello"))
        msg = await self.agent.reply(
            UserMsg(name="user", content="Hi"),
            visibility=Visibility.INTERNAL,
        )
        self.assertEqual(msg.visibility, Visibility.INTERNAL)

    async def test_the_constructor_sets_the_audience(self) -> None:
        """An agent can be born internal."""
        agent = Agent(
            name="worker",
            system_prompt="You do the work.",
            model=MockModel(),
            toolkit=Toolkit(),
            visibility=Visibility.INTERNAL,
        )
        self.assertEqual(agent.visibility, Visibility.INTERNAL)

    async def test_an_internal_reply_is_still_read_by_its_reader(self) -> None:
        """Visibility is a display hint, not a context filter."""
        self.agent.visibility = Visibility.INTERNAL
        self.model.set_responses(_text_responses("Hello"))
        await self.agent.reply(UserMsg(name="user", content="Hi"))
        self.assertTrue(
            any(
                msg.get_text_content() == "Hi"
                for msg in self.agent.state.context
            ),
        )


class ConsoleVisibilityTest(TestCase):
    """The console renderer honours the audience."""

    def _render(self, audience: object, **kwargs: object) -> str:
        """Render one text delta at the given visibility."""
        buffer = StringIO()
        renderer = ConsoleRenderer(
            console=Console(file=buffer, width=100, highlight=False),
            **kwargs,
        )
        renderer.render(
            TextBlockDeltaEvent(
                reply_id=REPLY_ID,
                block_id="b1",
                delta="hello",
                visibility=audience,
            ),
        )
        return buffer.getvalue()

    def test_user_events_are_rendered(self) -> None:
        """The default stream prints as before."""
        self.assertIn("hello", self._render(Visibility.USER))

    def test_artifact_events_are_rendered_inline(self) -> None:
        """A terminal has no side panel, so an artifact prints anyway."""
        self.assertIn("hello", self._render(Visibility.ARTIFACT))

    def test_internal_events_are_dropped(self) -> None:
        """Agent-to-agent traffic stays out of the transcript."""
        self.assertEqual(self._render(Visibility.INTERNAL), "")

    def test_debug_verbosity_renders_everything(self) -> None:
        """Debugging an orchestration means seeing the workers."""
        self.assertIn(
            "hello",
            self._render(Visibility.INTERNAL, verbosity="debug"),
        )

    def test_an_explicit_selection_wins_over_the_verbosity(self) -> None:
        """An explicit audience list is honoured under debug too."""
        self.assertEqual(
            self._render(
                Visibility.INTERNAL,
                verbosity="debug",
                visibility=[Visibility.USER],
            ),
            "",
        )

    def test_a_dropped_event_does_not_reach_the_accumulated_message(
        self,
    ) -> None:
        """``last_msg`` holds what was shown, not what was filtered out."""
        buffer = StringIO()
        renderer = ConsoleRenderer(
            console=Console(file=buffer, width=100, highlight=False),
        )
        renderer.render(
            ReplyStartEvent(
                session_id="s",
                reply_id=REPLY_ID,
                name="worker",
                visibility=Visibility.INTERNAL,
            ),
        )
        self.assertIsNone(renderer.last_msg)
