# -*- coding: utf-8 -*-
# pylint: disable=missing-module-docstring,missing-class-docstring
# pylint: disable=missing-function-docstring
# Set AGENTSCOPE_TEST_OPENAI_BASE_URL, AGENTSCOPE_TEST_OPENAI_API_KEY and
# AGENTSCOPE_TEST_OPENAI_MODEL to run against a real text-streaming endpoint.
import os
from unittest import IsolatedAsyncioTestCase, skipUnless

import httpx

from agentscope.agent import Agent, InjectionConfig
from agentscope.credential import OpenAICredential
from agentscope.event import TextBlockDeltaEvent, TextBlockStartEvent
from agentscope.message import TextBlock, UserMsg
from agentscope.middleware import SteeringMiddleware, SteeringQueue
from agentscope.model import OpenAIChatModel


@skipUnless(
    all(
        os.environ.get(name)
        for name in (
            "AGENTSCOPE_TEST_OPENAI_BASE_URL",
            "AGENTSCOPE_TEST_OPENAI_API_KEY",
            "AGENTSCOPE_TEST_OPENAI_MODEL",
        )
    ),
    "Requires a real OpenAI-compatible endpoint and model",
)
class AgentModelStreamIntegrationTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.responses: list[httpx.Response] = []

        async def capture(response: httpx.Response) -> None:
            self.responses.append(response)

        self.client = httpx.AsyncClient(
            trust_env=False,
            timeout=120,
            event_hooks={"response": [capture]},
        )
        self.addAsyncCleanup(self.client.aclose)
        self.model = OpenAIChatModel(
            credential=OpenAICredential(
                api_key=os.environ["AGENTSCOPE_TEST_OPENAI_API_KEY"],
                base_url=os.environ["AGENTSCOPE_TEST_OPENAI_BASE_URL"],
            ),
            model=os.environ["AGENTSCOPE_TEST_OPENAI_MODEL"],
            context_size=32768,
            parameters=OpenAIChatModel.Parameters(max_tokens=256),
            max_retries=0,
            client_kwargs={"http_client": self.client, "max_retries": 0},
        )

    async def test_model_close_releases_http_response(self) -> None:
        stream = await self.model(
            messages=[UserMsg("user", "Count from 1 to 100. /no_think")],
        )
        try:
            async for chunk in stream:
                if any(
                    isinstance(block, TextBlock) for block in chunk.content
                ):
                    break
            self.assertEqual(len(self.responses), 1)
            self.assertFalse(self.responses[0].is_closed)
            await stream.aclose()
            self.assertTrue(self.responses[0].is_closed)
        finally:
            await stream.aclose()

    async def test_reply_close_releases_http_response(self) -> None:
        for middleware_count in (0, 1, 2):
            for stop_at in (TextBlockStartEvent, TextBlockDeltaEvent):
                with self.subTest(
                    middleware_count=middleware_count,
                    stop_at=stop_at.__name__,
                ):
                    self.responses.clear()
                    agent = Agent(
                        name="assistant",
                        system_prompt="Follow the user's request.",
                        model=self.model,
                        injection_config=InjectionConfig(
                            inject_runtime_state=False,
                        ),
                        middlewares=[
                            SteeringMiddleware(SteeringQueue())
                            for _ in range(middleware_count)
                        ],
                    )
                    stream = agent.reply_stream(
                        UserMsg("user", "Count from 1 to 100. /no_think"),
                    )
                    try:
                        async for event in stream:
                            if isinstance(event, stop_at):
                                break
                        else:
                            self.fail(f"Reply did not emit {stop_at.__name__}")
                        self.assertEqual(len(self.responses), 1)
                        response = self.responses[0]
                        self.assertFalse(response.is_closed)
                        await stream.aclose()
                        self.assertTrue(response.is_closed)
                    finally:
                        await stream.aclose()

    async def test_completed_reply_releases_http_response(self) -> None:
        agent = Agent(
            name="assistant",
            system_prompt="Follow the user's request.",
            model=self.model,
            injection_config=InjectionConfig(inject_runtime_state=False),
            middlewares=[SteeringMiddleware(SteeringQueue())],
        )
        message = await agent.reply(UserMsg("user", "Say hello. /no_think"))
        self.assertTrue(message.get_text_content())
        self.assertEqual(len(self.responses), 1)
        self.assertTrue(self.responses[0].is_closed)
