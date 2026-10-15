# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for the built-in reverse message masking strategy."""
import asyncio
from copy import deepcopy
from typing import Any
from unittest import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig, ModelConfig
from agentscope.exception import ContentBlockedError, ContentBlockedPhase
from agentscope.message import (
    AssistantMsg,
    Base64Source,
    DataBlock,
    Msg,
    SystemMsg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.middleware import (
    ContentBlockFallbackMiddleware,
    ReverseMaskStrategy,
)
from agentscope.model import ChatResponse
from agentscope.state import AgentState

_MASK = "[Content omitted after a provider content block.]"


class _RecordingModel(MockModel):
    """Record complete model inputs for comparison across attempts."""

    def __init__(self) -> None:
        """Initialize the recorded message history."""
        super().__init__()
        self.inputs: list[list[Msg]] = []

    async def _call_api(self, *args: Any, **kwargs: Any) -> Any:
        """Record the request before returning the configured response."""
        self.inputs.append(deepcopy(kwargs["messages"]))
        return await super()._call_api(*args, **kwargs)


class ReverseMaskStrategyTest(IsolatedAsyncioTestCase):
    """Verify cumulative masking through the agent's middleware chain."""

    def setUp(self) -> None:
        """Build an agent using the public built-in strategy."""
        self.model = _RecordingModel()
        self.middleware = ContentBlockFallbackMiddleware(
            strategy=ReverseMaskStrategy(),
            max_fallback_attempts=3,
        )
        self.agent = Agent(
            name="test-agent",
            system_prompt="test",
            model=self.model,
            middlewares=[self.middleware],
            model_config=ModelConfig(max_retries=0),
            injection_config=InjectionConfig(inject_runtime_state=False),
        )

    @staticmethod
    def _blocked() -> ContentBlockedError:
        """Create a deterministic content-block exception."""
        return ContentBlockedError(
            "blocked",
            phase=ContentBlockedPhase.INPUT,
            provider="test",
        )

    async def test_cumulative_masks_preserve_protocol_and_history(
        self,
    ) -> None:
        """Mask whole messages in reverse order without losing tool pairs."""
        messages = [
            SystemMsg(name="system", content="instructions"),
            UserMsg(name="user", content="old request"),
            AssistantMsg(name="assistant", content="old answer"),
            AssistantMsg(
                name="assistant",
                content=[
                    ToolCallBlock(id="call-1", name="lookup", input="{}"),
                    ToolResultBlock(
                        id="call-1",
                        name="lookup",
                        output="first tool result",
                        state=ToolResultState.SUCCESS,
                        metadata={"source": "test"},
                    ),
                ],
            ),
            UserMsg(name="user", content="current request"),
            AssistantMsg(
                name="assistant",
                content=[
                    ThinkingBlock(thinking="reasoning", signature="signed"),
                    TextBlock(text="latest answer"),
                    ToolCallBlock(id="call-2", name="lookup", input="{}"),
                    ToolResultBlock(
                        id="call-2",
                        name="lookup",
                        output=[
                            TextBlock(text="second tool result"),
                            DataBlock(
                                source=Base64Source(
                                    data="dGVzdA==",
                                    media_type="image/png",
                                ),
                            ),
                        ],
                        state=ToolResultState.SUCCESS,
                    ),
                    TextBlock(text="more text"),
                ],
            ),
        ]
        original = deepcopy(messages)
        self.agent.state.context.extend(messages)
        expected = [deepcopy(messages)]
        latest = deepcopy(expected[-1])
        latest[5].content[1].text = _MASK
        latest[5].content[3].output = _MASK
        latest[5].content[4].text = _MASK
        expected.append(latest)
        tool_result = deepcopy(latest)
        tool_result[3].content[1].output = _MASK
        expected.append(tool_result)
        oldest = deepcopy(tool_result)
        oldest[2].content[0].text = _MASK
        expected.append(oldest)
        response = ChatResponse(
            content=[TextBlock(text="recovered")],
            is_last=True,
        )
        self.model.set_responses(
            [self._blocked(), self._blocked(), self._blocked(), response],
        )

        result = await self.agent._call_model(messages=messages, tools=[])

        self.assertEqual(result, response)
        self.assertEqual(self.model.inputs, expected)
        self.assertEqual(messages, original)
        self.assertEqual(self.agent.state.context, original)
        self.assertEqual(
            self.agent.state.middle_context,
            {
                "ContentBlockFallbackMiddleware": {
                    "quarantined_blocks": {
                        messages[5].id: [
                            messages[5].content[1].id,
                            messages[5].content[3].id,
                            messages[5].content[4].id,
                        ],
                        messages[3].id: [messages[3].content[1].id],
                        messages[2].id: [messages[2].content[0].id],
                    },
                },
            },
        )

        self.model.set_responses([response])
        await self.agent._call_model(messages=messages, tools=[])
        self.assertEqual(self.model.inputs[-1], expected[-1])
        self.assertEqual(self.agent.state.context, original)

    async def test_exhaustion_stops_without_masking_protected_messages(
        self,
    ) -> None:
        """Stop after the only candidate without retrying protected input."""
        messages = [
            SystemMsg(name="system", content="instructions"),
            UserMsg(name="user", content="old request"),
            AssistantMsg(name="assistant", content="answer"),
            AssistantMsg(
                name="assistant",
                content=[
                    ThinkingBlock(thinking="preserve"),
                    TextBlock(text=""),
                ],
            ),
            UserMsg(name="user", content="current request"),
        ]
        masked = deepcopy(messages)
        masked[2].content[0].text = _MASK
        last_error = self._blocked()
        self.model.set_responses([self._blocked(), last_error])

        with self.assertRaises(ContentBlockedError) as raised:
            await self.agent._call_model(messages=messages, tools=[])

        self.assertIs(raised.exception, last_error)
        self.assertEqual(self.model.inputs, [messages, masked])

    async def test_no_candidate_makes_no_replacement_call(self) -> None:
        """An input containing only protected messages is not retried."""
        messages = [
            SystemMsg(name="system", content="instructions"),
            UserMsg(name="user", content="request"),
        ]
        error = self._blocked()
        self.model.set_responses([error])

        with self.assertRaises(ContentBlockedError) as raised:
            await self.agent._call_model(messages=messages, tools=[])

        self.assertIs(raised.exception, error)
        self.assertEqual(self.model.inputs, [messages])

    async def test_stream_recovery_reuses_quarantine_on_next_call(
        self,
    ) -> None:
        """Later calls reuse successful isolation before sending requests."""
        messages = [
            AssistantMsg(name="assistant", content="previous answer"),
            UserMsg(name="user", content="request"),
        ]
        masked = deepcopy(messages)
        masked[0].content[0].text = _MASK
        for initial in ([self._blocked()], None):
            responses = [
                [
                    ChatResponse(
                        content=[TextBlock(text="recovered")],
                        is_last=False,
                    ),
                ],
            ]
            if initial is not None:
                responses.insert(0, initial)
            self.model.set_responses(
                responses,
            )
            stream = await self.agent._call_model(messages=messages, tools=[])
            chunks = [chunk async for chunk in stream]
            self.assertEqual(chunks[-1].content[0].text, "recovered")
        self.assertEqual(self.model.inputs, [messages, masked, masked])

    async def test_new_blocks_and_messages_are_not_automatically_masked(
        self,
    ) -> None:
        """Use block IDs so later additions to a reply remain visible."""
        original = AssistantMsg(name="assistant", content="old answer")
        messages = [original]
        self.model.set_responses(
            [self._blocked(), ChatResponse(content=[], is_last=True)],
        )
        await self.agent._call_model(messages=messages, tools=[])

        original.content.append(TextBlock(text="new text in the same reply"))
        messages.extend(
            [
                AssistantMsg(
                    name="assistant",
                    content=[
                        TextBlock(
                            id=original.content[0].id,
                            text="same block ID in a different message",
                        ),
                    ],
                ),
                UserMsg(name="user", content="next request"),
            ],
        )
        expected = deepcopy(messages)
        expected[0].content[0].text = _MASK
        self.model.set_responses([ChatResponse(content=[], is_last=True)])

        await self.agent._call_model(messages=messages, tools=[])

        self.assertEqual(self.model.inputs[-1], expected)
        self.assertEqual(original.content[0].text, "old answer")
        self.assertEqual(self.model.cnt, 1)

    async def test_state_round_trip_restores_quarantine(self) -> None:
        """A new middleware instance can use IDs loaded with agent state."""
        self.agent.state.context.append(
            AssistantMsg(name="assistant", content="old answer"),
        )
        original = deepcopy(self.agent.state.context)
        self.model.set_responses(
            [self._blocked(), ChatResponse(content=[], is_last=True)],
        )
        await self.agent._call_model(
            messages=self.agent.state.context,
            tools=[],
        )
        restored = AgentState.model_validate_json(
            self.agent.state.model_dump_json(),
        )
        model = _RecordingModel()
        agent = Agent(
            name="restored",
            system_prompt="test",
            model=model,
            state=restored,
            middlewares=[
                ContentBlockFallbackMiddleware(ReverseMaskStrategy()),
            ],
        )
        expected = deepcopy(original)
        expected[0].content[0].text = _MASK
        model.set_responses([ChatResponse(content=[], is_last=True)])

        await agent._call_model(messages=agent.state.context, tools=[])

        self.assertEqual(model.inputs, [expected])
        self.assertEqual(agent.state, self.agent.state)
        self.assertEqual(agent.state.context, original)

    async def test_clear_restores_original_input(self) -> None:
        """Clearing isolation preserves history and other middleware state."""
        messages = [AssistantMsg(name="assistant", content="old answer")]
        self.agent.state.middle_context["other-middleware"] = {"value": 1}
        self.model.set_responses(
            [self._blocked(), ChatResponse(content=[], is_last=True)],
        )
        await self.agent._call_model(messages=messages, tools=[])

        await self.middleware.clear_quarantine(self.agent)
        self.model.set_responses([ChatResponse(content=[], is_last=True)])
        await self.agent._call_model(messages=messages, tools=[])

        self.assertEqual(self.model.inputs[-1], messages)
        self.assertEqual(
            self.agent.state.middle_context,
            {"other-middleware": {"value": 1}},
        )

    async def test_failed_and_cancelled_attempts_do_not_commit(self) -> None:
        """Discard provisional isolation on errors, cancellation, or close."""
        messages = [AssistantMsg(name="assistant", content="old answer")]
        for mode in ("blocked", "error", "cancel", "stream_cancel", "close"):
            with self.subTest(mode=mode):
                response = ChatResponse(
                    content=[TextBlock(text="partial")],
                    is_last=False,
                )
                replacements = {
                    "blocked": self._blocked(),
                    "error": RuntimeError("failed"),
                    "cancel": asyncio.CancelledError(),
                    "stream_cancel": [response, asyncio.CancelledError()],
                    "close": [response],
                }
                self.model.set_responses(
                    [self._blocked(), replacements[mode]],
                )
                if mode in ("blocked", "error"):
                    with self.assertRaises(
                        (ContentBlockedError, RuntimeError),
                    ):
                        await self.agent._call_model(
                            messages=messages,
                            tools=[],
                        )
                else:
                    result = await self.agent._call_model(
                        messages=messages,
                        tools=[],
                    )
                    if mode == "close":
                        await anext(result)
                        await result.aclose()
                    elif mode == "stream_cancel":
                        async for _ in result:
                            pass

                self.assertEqual(self.agent.state.middle_context, {})
                self.assertEqual(messages[0].content[0].text, "old answer")

    async def test_existing_quarantine_survives_failed_new_recovery(
        self,
    ) -> None:
        """A failed later recovery preserves earlier successful records."""
        messages = [AssistantMsg(name="assistant", content="old answer")]
        self.model.set_responses(
            [self._blocked(), ChatResponse(content=[], is_last=True)],
        )
        await self.agent._call_model(messages=messages, tools=[])
        previous = deepcopy(self.agent.state.middle_context)
        messages.append(AssistantMsg(name="assistant", content="new answer"))
        expected = deepcopy(messages)
        expected[0].content[0].text = _MASK
        self.model.set_responses([self._blocked(), self._blocked()])

        with self.assertRaises(ContentBlockedError):
            await self.agent._call_model(messages=messages, tools=[])

        self.assertEqual(self.agent.state.middle_context, previous)
        self.assertEqual(self.model.inputs[-2], expected)

    async def test_shared_middleware_does_not_leak_between_agents(
        self,
    ) -> None:
        """Isolation is scoped to agent state, not the middleware instance."""
        messages = [AssistantMsg(name="assistant", content="old answer")]
        self.model.set_responses(
            [self._blocked(), ChatResponse(content=[], is_last=True)],
        )
        await self.agent._call_model(messages=messages, tools=[])
        other = Agent(
            name="other",
            system_prompt="test",
            model=self.model,
            middlewares=[self.middleware],
        )
        self.model.set_responses([ChatResponse(content=[], is_last=True)])

        await other._call_model(messages=messages, tools=[])

        self.assertEqual(self.model.inputs[-1], messages)
        self.assertEqual(other.state.middle_context, {})
