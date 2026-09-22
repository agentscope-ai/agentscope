# -*- coding: utf-8 -*-
"""Tests for the model router middleware."""
from typing import Any, Mapping
from unittest import IsolatedAsyncioTestCase

from pydantic import BaseModel

from utils import MockModel
from agentscope.agent import Agent, InjectionConfig
from agentscope.classifier import (
    ChoiceAnswer,
    ClassifierModelBase,
    ClassifierQuestion,
    ClassifierResponse,
)
from agentscope.credential import CredentialBase
from agentscope.event import ModelCallStartEvent, ReplyStartEvent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import (
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    UserMsg,
)
from agentscope.middleware import ChatModelCandidate, ModelRouterMiddleware
from agentscope.model import ChatResponse, StructuredResponse


class _MockClassifier(ClassifierModelBase):
    """A classifier that returns the configured choices in turn."""

    def __init__(self, outcomes: list[str | Exception]) -> None:
        """Initialize the classifier with choices or exceptions."""
        super().__init__(CredentialBase(), "mock-classifier")
        self.outcomes = outcomes
        self.calls: list[dict] = []

    async def __call__(
        self,
        state: str | dict,
        questions: Mapping[str, ClassifierQuestion],
        **kwargs: Any,
    ) -> ClassifierResponse:
        """Return the next configured routing choice."""
        self.calls.append(
            {
                "state": state,
                "questions": {k: v.model_dump() for k, v in questions.items()},
            },
        )
        outcome = self.outcomes[len(self.calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return ClassifierResponse(
            model=self.model,
            content={
                "chat_model": ChoiceAnswer(
                    choice=outcome,
                    confidence=0.9,
                    probabilities={outcome: 0.9},
                ),
            },
        )


class _MockRoutingChatModel(MockModel):
    """A chat model that returns the configured choice as structured output."""

    def __init__(self, outcome: str) -> None:
        """Initialize the chat model with its choice."""
        super().__init__(model="routing-chat-model")
        self.outcome = outcome
        self.calls: list[dict] = []

    async def generate_structured_output(
        self,
        messages: list[Msg],
        structured_model: type[BaseModel] | dict,
        **kwargs: Any,
    ) -> StructuredResponse:
        """Return the configured routing choice."""
        self.calls.append(
            {
                "messages": [(m.role, m.get_text_content()) for m in messages],
                "structured_model": structured_model,
            },
        )
        return StructuredResponse(content={"choice": self.outcome})


class ModelRouterMiddlewareTest(IsolatedAsyncioTestCase):
    """Test routing through a full agent reply."""

    def setUp(self) -> None:
        """Create the primary and candidate chat models."""
        self.primary = MockModel(model="primary")
        self.fast = MockModel(model="fast-model")
        self.reasoning = MockModel(model="reasoning-model")
        for model in (self.primary, self.fast, self.reasoning):
            model.set_responses(
                [[ChatResponse(content=[TextBlock(text="ok")], is_last=True)]]
                * 3,
            )
        self.candidates = [
            ChatModelCandidate(
                name="fast",
                model=self.fast,
                description="Short and simple requests.",
            ),
            ChatModelCandidate(
                name="reasoning",
                model=self.reasoning,
                description="Complex reasoning is required.",
            ),
        ]

    async def _reply(
        self,
        middleware: ModelRouterMiddleware,
        *contents: list[TextBlock | DataBlock],
    ) -> tuple[Agent, list[str]]:
        """Run one reply per content through a routed agent and return the
        agent and the model called in each reply."""
        agent = Agent(
            name="Friday",
            system_prompt="Help the user.",
            model=self.primary,
            middlewares=[middleware],
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        called = []
        for content in contents:
            async for event in agent.reply_stream(
                UserMsg(name="user", content=content),
            ):
                if isinstance(event, ModelCallStartEvent):
                    called.append(event.model_name)
        return agent, called

    async def test_classifier_routes_each_reply(self) -> None:
        """Each reply is routed by the classifier and the agent's own model
        is restored afterwards."""
        classifier = _MockClassifier(["reasoning", "fast"])
        middleware = ModelRouterMiddleware(classifier, self.candidates)

        agent, called = await self._reply(
            middleware,
            [TextBlock(text="Prove this theorem.")],
            [TextBlock(text="Say hello.")],
        )

        self.assertListEqual(called, ["reasoning-model", "fast-model"])
        self.assertIs(agent.model, self.primary)
        self.assertListEqual(
            classifier.calls,
            [
                {
                    "state": "Prove this theorem.",
                    "questions": {
                        "chat_model": {
                            "type": "choice",
                            "criteria": {
                                "fast": "Short and simple requests.",
                                "reasoning": "Complex reasoning is required.",
                            },
                            "instructions": (
                                "Select the most suitable chat model for "
                                "responding to the user input."
                            ),
                        },
                    },
                },
                {
                    "state": "Say hello.",
                    "questions": {
                        "chat_model": {
                            "type": "choice",
                            "criteria": {
                                "fast": "Short and simple requests.",
                                "reasoning": "Complex reasoning is required.",
                            },
                            "instructions": (
                                "Select the most suitable chat model for "
                                "responding to the user input."
                            ),
                        },
                    },
                },
            ],
        )
        self.assertDictEqual(
            agent.state.middle_context,
            {"ModelRouterMiddleware": {agent.state.reply_id: "fast"}},
        )

    async def test_resumed_reply_keeps_its_route(self) -> None:
        """A reply resumed without a ReplyStartEvent reuses its route."""
        middleware = ModelRouterMiddleware(
            _MockClassifier(["reasoning"]),
            self.candidates,
        )
        agent, _ = await self._reply(
            middleware,
            [TextBlock(text="Prove this theorem.")],
        )
        active = []

        async def resume(**_: Any) -> Any:
            active.append(agent.model)
            yield ReplyStartEvent(
                session_id=agent.state.session_id,
                reply_id="another-reply",
                name=agent.name,
            )
            active.append(agent.model)

        async for _ in middleware.on_reply(agent, {"inputs": None}, resume):
            pass

        # The resumed reply keeps the route, a new one is routed again
        self.assertListEqual(active, [self.reasoning, self.primary])
        self.assertIs(agent.model, self.primary)

    async def test_new_reply_is_gated_by_its_own_routing(self) -> None:
        """An attachment is judged against the models of *its* reply, not
        against the candidate the previous reply was routed to."""
        # The cheap candidate carries no media at all; the roomy context
        # keeps the recording from being compressed out of the turn.
        self.fast.formatter = OpenAIChatFormatter(input_types=["text/plain"])
        for model in (self.primary, self.fast, self.reasoning):
            model.context_size = 100000
        middleware = ModelRouterMiddleware(
            _MockClassifier(["fast", "reasoning"]),
            self.candidates,
        )

        agent, called = await self._reply(
            middleware,
            [TextBlock(text="Say hello.")],
            [
                TextBlock(text="What is in this recording?"),
                DataBlock(
                    source=Base64Source(data="AA==", media_type="audio/wav"),
                ),
            ],
        )

        self.assertListEqual(called, ["fast-model", "reasoning-model"])
        self.assertDictEqual(
            agent.state.middle_context,
            {"ModelRouterMiddleware": {agent.state.reply_id: "reasoning"}},
        )

        def kinds(msg: Msg) -> list[tuple[str, str]]:
            """The content blocks of *msg*, by type and payload."""
            return [
                (
                    block.type,
                    block.text
                    if block.type == "text"
                    else block.source.media_type,
                )
                for block in msg.content
            ]

        # The recording reaches the model that was asked about it: while the
        # route is still unknown the agent's own model gates the media, and
        # that one accepts audio.
        self.assertListEqual(
            [kinds(_) for _ in agent.state.context if _.role == "user"],
            [
                [("text", "Say hello.")],
                [
                    ("text", "What is in this recording?"),
                    ("data", "audio/wav"),
                ],
            ],
        )

    async def test_chat_model_routes_with_structured_output(self) -> None:
        """A chat model routes through a structured choice."""
        routing_model = _MockRoutingChatModel("fast")
        middleware = ModelRouterMiddleware(routing_model, self.candidates)

        _, called = await self._reply(middleware, [TextBlock(text="Hi.")])

        self.assertListEqual(called, ["fast-model"])
        self.assertListEqual(
            routing_model.calls,
            [
                {
                    "messages": [
                        (
                            "system",
                            "Select the most suitable chat model for "
                            "responding to the user input.\n\n"
                            "Select exactly one candidate using these "
                            "criteria:\n"
                            "{\n"
                            '  "fast": "Short and simple requests.",\n'
                            '  "reasoning": "Complex reasoning is required."'
                            "\n}",
                        ),
                        ("user", "Hi."),
                    ],
                    "structured_model": {
                        "type": "object",
                        "properties": {
                            "choice": {
                                "type": "string",
                                "enum": ["fast", "reasoning"],
                            },
                        },
                        "required": ["choice"],
                        "additionalProperties": False,
                    },
                },
            ],
        )

    async def test_failures_keep_the_agents_model(self) -> None:
        """A routing error, an unknown candidate and an input without text
        all keep the agent's own model."""
        classifier = _MockClassifier(
            [RuntimeError("boom"), "unknown", "reasoning"],
        )
        middleware = ModelRouterMiddleware(classifier, self.candidates)

        agent, called = await self._reply(
            middleware,
            [TextBlock(text="Error.")],
            [TextBlock(text="Unknown.")],
            [
                DataBlock(
                    source=Base64Source(data="AA==", media_type="image/png"),
                ),
            ],
        )

        self.assertListEqual(called, ["primary", "primary", "primary"])
        self.assertListEqual(
            [_["state"] for _ in classifier.calls],
            ["Error.", "Unknown."],
        )
        self.assertDictEqual(
            agent.state.middle_context,
            {"ModelRouterMiddleware": {agent.state.reply_id: None}},
        )

    def test_duplicate_candidates_are_rejected(self) -> None:
        """Candidate names must be unique."""
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            ModelRouterMiddleware(
                _MockClassifier([]),
                self.candidates + self.candidates[:1],
            )
