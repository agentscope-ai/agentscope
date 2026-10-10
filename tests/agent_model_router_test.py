# -*- coding: utf-8 -*-
# pylint: disable=protected-access,unused-argument
"""The unittests for the dynamic model routing of the agent class."""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig, ModelConfig
from agentscope.message import TextBlock, UserMsg
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.tool import Toolkit


def _text_response(text: str) -> ChatResponse:
    """Build a non-streaming response carrying a single text block."""
    return ChatResponse(content=[TextBlock(text=text)], is_last=True)


class AgentModelRouterTest(IsolatedAsyncioTestCase):
    """Test the ``model_router`` of the model config."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.default_model = MockModel(model="default-model")
        self.fast_model = MockModel(model="fast-model")
        self.reasoning_model = MockModel(model="reasoning-model")

        for model in [
            self.default_model,
            self.fast_model,
            self.reasoning_model,
        ]:
            model.set_responses([_text_response(f"Reply from {model.model}")])

    def _create_agent(self, model_config: ModelConfig) -> Agent:
        """Create an agent equipped with the given model config."""
        return Agent(
            name="Friday",
            system_prompt="You are a helpful assistant.",
            model=self.default_model,
            toolkit=Toolkit(),
            model_config=model_config,
            injection_config=InjectionConfig(inject_runtime_state=False),
        )

    async def test_default_model_without_router(self) -> None:
        """Without a router, the agent's own model is always called."""
        agent = self._create_agent(ModelConfig())

        msg = await agent.reply(UserMsg(name="user", content="Hi"))

        self.assertEqual(msg.get_text_content(), "Reply from default-model")
        self.assertEqual(self.default_model.cnt, 1)
        self.assertEqual(self.fast_model.cnt, 0)

    async def test_router_returns_model_instance(self) -> None:
        """A router returning a model instance overrides the agent model."""
        captured: dict[str, Any] = {}

        def router(
            agent: Agent,
            messages: list,
            tools: list,
        ) -> ChatModelBase:
            """Always route to the reasoning model."""
            captured["agent"] = agent
            captured["messages"] = messages
            captured["tools"] = tools
            return self.reasoning_model

        agent = self._create_agent(ModelConfig(model_router=router))

        msg = await agent.reply(UserMsg(name="user", content="Hi"))

        self.assertEqual(msg.get_text_content(), "Reply from reasoning-model")
        self.assertEqual(self.reasoning_model.cnt, 1)
        self.assertEqual(self.default_model.cnt, 0)
        # The router sees the agent and the prepared model input
        self.assertIs(captured["agent"], agent)
        self.assertEqual(captured["messages"][0].role, "system")
        self.assertEqual(captured["messages"][-1].get_text_content(), "Hi")
        self.assertEqual(captured["tools"], [])

    async def test_router_returns_candidate_name(self) -> None:
        """A router may return a key of the candidate models."""

        async def router(messages: list, **kwargs: Any) -> str:
            """Pick the model by inspecting the latest user input."""
            if "think" in messages[-1].get_text_content():
                return "reasoning"
            return "fast"

        agent = self._create_agent(
            ModelConfig(
                model_router=router,
                candidate_models={
                    "fast": self.fast_model,
                    "reasoning": self.reasoning_model,
                },
            ),
        )

        msg = await agent.reply(UserMsg(name="user", content="just chat"))
        self.assertEqual(msg.get_text_content(), "Reply from fast-model")

        msg = await agent.reply(
            UserMsg(name="user", content="please think hard"),
        )
        self.assertEqual(msg.get_text_content(), "Reply from reasoning-model")

        self.assertEqual(self.fast_model.cnt, 1)
        self.assertEqual(self.reasoning_model.cnt, 1)
        self.assertEqual(self.default_model.cnt, 0)

    async def test_router_returning_none_keeps_default_model(self) -> None:
        """Returning ``None`` falls back to the agent's own model."""

        def router(**kwargs: Any) -> None:
            """Abstain from routing."""
            return None

        agent = self._create_agent(ModelConfig(model_router=router))

        msg = await agent.reply(UserMsg(name="user", content="Hi"))

        self.assertEqual(msg.get_text_content(), "Reply from default-model")
        self.assertEqual(self.default_model.cnt, 1)

    async def test_router_switches_model_across_iterations(self) -> None:
        """The router is consulted before every reasoning step, so the model
        can change according to what the model returned before."""
        self.fast_model.set_responses(
            [_text_response("I need to escalate, let's think")],
        )
        self.reasoning_model.set_responses([_text_response("Final answer")])

        def router(messages: list, **kwargs: Any) -> str:
            """Escalate once the previous reply asks for thinking."""
            for msg in reversed(messages):
                if msg.role == "assistant":
                    if "think" in msg.get_text_content():
                        return "reasoning"
                    break
            return "fast"

        agent = self._create_agent(
            ModelConfig(
                model_router=router,
                candidate_models={
                    "fast": self.fast_model,
                    "reasoning": self.reasoning_model,
                },
            ),
        )

        first = await agent.reply(UserMsg(name="user", content="Hi"))
        self.assertEqual(
            first.get_text_content(),
            "I need to escalate, let's think",
        )

        second = await agent.reply(UserMsg(name="user", content="Go on"))
        self.assertEqual(second.get_text_content(), "Final answer")

        self.assertEqual(self.fast_model.cnt, 1)
        self.assertEqual(self.reasoning_model.cnt, 1)

    async def test_routed_model_reported_in_model_call_start_event(
        self,
    ) -> None:
        """The ``MODEL_CALL_START`` event carries the routed model name."""
        agent = self._create_agent(
            ModelConfig(
                model_router=lambda **kwargs: "fast",
                candidate_models={"fast": self.fast_model},
            ),
        )

        model_names = [
            event.model_name
            async for event in agent.reply_stream(
                UserMsg(name="user", content="Hi"),
            )
            if getattr(event, "type", None) == "MODEL_CALL_START"
        ]

        self.assertEqual(model_names, ["fast-model"])

    async def test_unknown_candidate_name_raises(self) -> None:
        """An unknown candidate name is a configuration error."""
        agent = self._create_agent(
            ModelConfig(
                model_router=lambda **kwargs: "missing",
                candidate_models={"fast": self.fast_model},
            ),
        )

        with self.assertRaises(ValueError) as ctx:
            await agent._resolve_model(messages=[], tools=[])

        self.assertIn("missing", str(ctx.exception))
        self.assertIn("['fast']", str(ctx.exception))

    async def test_invalid_router_return_type_raises(self) -> None:
        """A router must return a model, a candidate name or ``None``."""
        agent = self._create_agent(
            ModelConfig(model_router=lambda **kwargs: 123),
        )

        with self.assertRaises(TypeError) as ctx:
            await agent._resolve_model(messages=[], tools=[])

        self.assertIn("int", str(ctx.exception))

    async def test_fallback_model_applies_to_routed_model(self) -> None:
        """The fallback model still covers a failing routed model."""
        self.fast_model.set_responses([RuntimeError("boom")])
        fallback = MockModel(model="fallback-model")
        fallback.set_responses([_text_response("Reply from fallback-model")])

        agent = self._create_agent(
            ModelConfig(
                model_router=lambda **kwargs: self.fast_model,
                fallback_model=fallback,
            ),
        )

        msg = await agent.reply(UserMsg(name="user", content="Hi"))

        self.assertEqual(msg.get_text_content(), "Reply from fallback-model")
        self.assertEqual(self.fast_model.cnt, 1)
        self.assertEqual(fallback.cnt, 1)
        self.assertEqual(self.default_model.cnt, 0)
