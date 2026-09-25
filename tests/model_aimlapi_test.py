# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for AIMLAPIChatModel with mocked API responses.

Beyond the usual streaming / non-streaming / tool-calling coverage, two
provider-specific invariants are asserted here because both fail *silently*
in production:

* the attribution headers must be well-formed and scoped to aimlapi.com, and
* no request parameter may ever be sent as an explicit ``null`` — several
  aimlapi.com models reject ``"tools": null`` with a 400, which breaks the
  second turn of a tool-calling loop while every mocked test stays green.
"""
import re
from typing import Any
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from utils import AnyString

from agentscope.credential import AIMLAPICredential, CredentialFactory
from agentscope.formatter import (
    AIMLAPIChatFormatter,
    AIMLAPIMultiAgentFormatter,
    FormatterBase,
)
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import AIMLAPIChatModel
from agentscope.model._aimlapi._model import (
    _AIMLAPI_ATTRIBUTION_HEADERS,
    _build_client_kwargs,
)
from agentscope.tool import ToolChoice

A = AnyString()

# The gateway contract: a partner id that does not match is dropped, earning
# nothing, and the request still succeeds — so only a test catches a typo.
_PARTNER_ID_PATTERN = re.compile(r"^part_[A-Za-z0-9]{1,64}$")
# ``<channel>/<client>``, channel from a closed enum, client lowercase.
_SOURCE_PATTERN = re.compile(r"^(web|agent|mcp)/[a-z0-9-]{1,32}$")

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the weather.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
        },
    },
]


def _make_model(stream: bool = False, **kwargs: Any) -> AIMLAPIChatModel:
    return AIMLAPIChatModel(
        credential=AIMLAPICredential(api_key="test"),
        model="openai/gpt-5-5",
        stream=stream,
        **kwargs,
    )


def _mock_completion(
    text: Any = None,
    tool_calls: Any = None,
    response_id: str = "aimlapi-1",
) -> MagicMock:
    """Build a mock non-streaming ChatCompletion response."""
    msg = MagicMock()
    msg.content = text
    msg.reasoning_content = None
    msg.reasoning = None
    msg.audio = None
    msg.tool_calls = None

    if tool_calls:
        tc_mocks = []
        for tc in tool_calls:
            m = MagicMock()
            m.id = tc["id"]
            m.function.name = tc["name"]
            m.function.arguments = tc["arguments"]
            tc_mocks.append(m)
        msg.tool_calls = tc_mocks

    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"

    resp = MagicMock()
    resp.id = response_id
    resp.choices = [choice]
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    return resp


def _make_stream_chunk(
    delta_text: str | None = None,
    response_id: str = "aimlapi-1",
) -> MagicMock:
    """Build a single mock streaming chunk."""
    chunk = MagicMock()
    chunk.id = response_id
    chunk.usage = None

    delta = MagicMock()
    delta.content = delta_text
    delta.reasoning_content = None
    delta.reasoning = None
    delta.audio = None
    delta.tool_calls = None

    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = None
    chunk.choices = [choice]
    return chunk


class _MockAsyncStream:
    """Mock async stream (context manager + async iterator)."""

    def __init__(self, chunks: list) -> None:
        self._chunks = chunks
        self._index = 0

    async def __aenter__(self) -> "_MockAsyncStream":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def __aiter__(self) -> "_MockAsyncStream":
        return self

    async def __anext__(self) -> Any:
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


class TestAIMLAPIAttribution(unittest.TestCase):
    """The attribution headers and how they are attached."""

    def test_partner_id_and_source_shape(self) -> None:
        """A malformed partner id or source is dropped by the gateway with
        no error, so its shape is asserted here."""
        self.assertRegex(
            _AIMLAPI_ATTRIBUTION_HEADERS["X-AIMLAPI-Partner-ID"],
            _PARTNER_ID_PATTERN,
        )
        self.assertRegex(
            _AIMLAPI_ATTRIBUTION_HEADERS["X-AIMLAPI-Source"],
            _SOURCE_PATTERN,
        )

    def test_referer_and_title_name_the_host_project(self) -> None:
        """``HTTP-Referer`` / ``X-Title`` identify the calling application —
        AgentScope — not the provider."""
        self.assertEqual(
            _AIMLAPI_ATTRIBUTION_HEADERS["HTTP-Referer"],
            "https://github.com/agentscope-ai/agentscope",
        )
        self.assertEqual(_AIMLAPI_ATTRIBUTION_HEADERS["X-Title"], "AgentScope")

    def test_headers_attached_by_default(self) -> None:
        """All four headers reach the client for the default base URL."""
        model = _make_model()
        self.assertEqual(
            model.client_kwargs["default_headers"],
            _AIMLAPI_ATTRIBUTION_HEADERS,
        )

    def test_caller_headers_win_and_survive(self) -> None:
        """Merging, never assigning: a caller's own headers are preserved and
        take precedence on a clash."""
        model = _make_model(
            client_kwargs={
                "default_headers": {"X-Title": "Custom", "X-Own": "1"},
                "timeout": 5,
            },
        )
        headers = model.client_kwargs["default_headers"]
        self.assertEqual(headers["X-Title"], "Custom")
        self.assertEqual(headers["X-Own"], "1")
        self.assertEqual(
            headers["X-AIMLAPI-Partner-ID"],
            _AIMLAPI_ATTRIBUTION_HEADERS["X-AIMLAPI-Partner-ID"],
        )
        self.assertEqual(model.client_kwargs["timeout"], 5)

    def test_shared_constant_is_never_mutated(self) -> None:
        """Each instance builds its own dict."""
        before = dict(_AIMLAPI_ATTRIBUTION_HEADERS)
        first = _make_model()
        first.client_kwargs["default_headers"]["X-Title"] = "mutated"
        second = _make_model()

        self.assertEqual(_AIMLAPI_ATTRIBUTION_HEADERS, before)
        self.assertEqual(
            second.client_kwargs["default_headers"]["X-Title"],
            "AgentScope",
        )

    def test_headers_scoped_to_aimlapi_origin(self) -> None:
        """Attribution must not ride a request to another host — including a
        proxy that merely fronts the same API."""
        for base_url, expected in [
            ("https://api.aimlapi.com/v1", True),
            ("https://staging.aimlapi.com/v1", True),
            ("https://api.openai.com/v1", False),
            ("http://localhost:8000/v1", False),
            ("https://aimlapi.com.evil.example/v1", False),
            (None, False),
        ]:
            with self.subTest(base_url=base_url):
                kwargs = _build_client_kwargs(base_url, None)
                self.assertEqual("default_headers" in kwargs, expected)

    def test_headers_not_sent_to_a_custom_base_url(self) -> None:
        """End to end through the model class, not just the helper."""
        model = AIMLAPIChatModel(
            credential=AIMLAPICredential(
                api_key="test",
                base_url="https://proxy.example.com/v1",
            ),
            model="openai/gpt-5-5",
        )
        self.assertNotIn("default_headers", model.client_kwargs)


class TestAIMLAPIRegistration(unittest.TestCase):
    """Credential registration, display name and model cards."""

    def test_display_name(self) -> None:
        """The user-facing provider label."""
        self.assertEqual(
            AIMLAPICredential.model_json_schema()["title"],
            "aimlapi.com",
        )

    def test_registered_in_factory(self) -> None:
        """The credential deserializes through the discriminated union."""
        credential = CredentialFactory.from_dict(
            {"type": "aimlapi_credential", "api_key": "test"},
        )
        self.assertIsInstance(credential, AIMLAPICredential)
        self.assertIs(
            CredentialFactory.get_credential_class("aimlapi_credential"),
            AIMLAPICredential,
        )
        self.assertIn(
            "aimlapi.com",
            [schema["title"] for schema in CredentialFactory.list_schemas()],
        )

    def test_credential_resolves_to_the_chat_model(self) -> None:
        """The credential points back at its chat model class."""
        self.assertIs(
            AIMLAPICredential.get_chat_model_class(),
            AIMLAPIChatModel,
        )

    def test_default_base_url_is_the_chat_completions_root(self) -> None:
        """``/v1/completions`` does not exist on aimlapi.com; the base URL
        must be the ``/v1`` root that the OpenAI SDK appends
        ``/chat/completions`` to."""
        self.assertEqual(
            AIMLAPICredential(api_key="test").base_url,
            "https://api.aimlapi.com/v1",
        )

    def test_model_cards_load(self) -> None:
        """Every bundled card parses and carries a live catalog id."""
        cards = AIMLAPIChatModel.list_models()
        self.assertEqual(len(cards), 8)
        names = {card.name for card in cards}
        self.assertIn("openai/gpt-5-5", names)
        self.assertIn("anthropic/claude-sonnet-4.6", names)

    def test_model_card_output_size_fits_the_context(self) -> None:
        """The catalog's ``outputMax`` exceeds ``contextLength`` on some
        aimlapi.com models, so the cards are sanity-checked here rather than
        copied blindly."""
        for card in AIMLAPIChatModel.list_models():
            with self.subTest(model=card.name):
                self.assertLessEqual(card.output_size, card.context_size)

    def test_formatters(self) -> None:
        """Both formatter variants exist and the chat one is the default."""
        self.assertTrue(issubclass(AIMLAPIChatFormatter, FormatterBase))
        self.assertTrue(issubclass(AIMLAPIMultiAgentFormatter, FormatterBase))
        self.assertIsInstance(_make_model().formatter, AIMLAPIChatFormatter)


class TestAIMLAPIRequestParams(IsolatedAsyncioTestCase):
    """No request parameter may be sent as an explicit ``null``."""

    def setUp(self) -> None:
        self.model = _make_model(stream=False)
        self.mock_client = MagicMock()
        self.model.client = self.mock_client
        self.mock_create = AsyncMock(
            return_value=_mock_completion(text="ok"),
        )
        self.mock_client.chat.completions.create = self.mock_create

    async def test_unset_parameters_are_omitted_not_nulled(self) -> None:
        """A freshly built model sends no ``null`` values at all."""
        await self.model([])
        kwargs = self.mock_create.call_args.kwargs

        self.assertEqual(
            [key for key, value in kwargs.items() if value is None],
            [],
        )
        for key in [
            "temperature",
            "top_p",
            "seed",
            "tools",
            "tool_choice",
            "response_format",
            "parallel_tool_calls",
            "max_tokens",
            "max_completion_tokens",
            "reasoning_effort",
        ]:
            self.assertNotIn(key, kwargs)

    async def test_two_turn_tool_loop_drops_the_tools_key(self) -> None:
        """Turn 1 sends tools; turn 2 clears them. The strictest aimlapi.com
        models 400 on ``"tools": null``, so turn 2 must omit the key rather
        than null it — this is the failure mode that only shows up on the
        second call of a real agent loop."""
        await self.model(
            [],
            tools=_TOOLS,
            tool_choice=ToolChoice(mode="auto"),
        )
        turn_one = self.mock_create.call_args.kwargs
        self.assertEqual(len(turn_one["tools"]), 1)
        self.assertEqual(turn_one["tool_choice"], "auto")

        await self.model([], tools=None, tool_choice=None)
        turn_two = self.mock_create.call_args.kwargs

        self.assertNotIn("tools", turn_two)
        self.assertNotIn("tool_choice", turn_two)
        self.assertEqual(
            [key for key, value in turn_two.items() if value is None],
            [],
        )

    async def test_empty_tool_list_omits_the_key_too(self) -> None:
        """A toolkit that emptied itself must not send ``"tools": []``
        either — the key simply goes away."""
        await self.model([], tools=[], tool_choice=None)
        self.assertNotIn("tools", self.mock_create.call_args.kwargs)

    async def test_set_parameters_are_forwarded(self) -> None:
        """The omit-if-unset rule must not swallow values that were set."""
        model = _make_model(stream=False)
        model.client = self.mock_client
        model.parameters = AIMLAPIChatModel.Parameters(
            temperature=0.5,
            max_tokens=128,
        )
        await model([])
        kwargs = self.mock_create.call_args.kwargs
        self.assertEqual(kwargs["temperature"], 0.5)
        self.assertEqual(kwargs["max_completion_tokens"], 128)


class TestAIMLAPIResponses(IsolatedAsyncioTestCase):
    """Response parsing, streaming and non-streaming."""

    def setUp(self) -> None:
        self.mock_client = MagicMock()

    async def test_text_response(self) -> None:
        """A non-streaming text response becomes a single ChatResponse."""
        model = _make_model(stream=False)
        model.client = self.mock_client
        self.mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_completion(text="Hello!"),
        )

        result = await model([])

        self.assertEqual(
            (result.is_last, result.content),
            (
                True,
                [TextBlock.model_construct(id=A, created_at=A, text="Hello!")],
            ),
        )
        self.assertEqual(result.id, "aimlapi-1")

    async def test_tool_call_response(self) -> None:
        """A non-streaming tool call becomes a ToolCallBlock."""
        model = _make_model(stream=False)
        model.client = self.mock_client
        self.mock_client.chat.completions.create = AsyncMock(
            return_value=_mock_completion(
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "get_weather",
                        "arguments": '{"city":"Shanghai"}',
                    },
                ],
            ),
        )

        result = await model([], tools=_TOOLS)

        self.assertEqual(
            result.content,
            [
                ToolCallBlock.model_construct(
                    id="call-1",
                    created_at=A,
                    name="get_weather",
                    input='{"city":"Shanghai"}',
                ),
            ],
        )

    async def test_stream_response(self) -> None:
        """Streaming deltas accumulate and usage is requested."""
        model = _make_model(stream=True)
        model.client = self.mock_client
        self.mock_client.chat.completions.create = AsyncMock(
            return_value=_MockAsyncStream(
                [
                    _make_stream_chunk("He"),
                    _make_stream_chunk("llo"),
                ],
            ),
        )

        chunks = [chunk async for chunk in await model([])]

        self.assertEqual(chunks[-1].content[0].text, "Hello")
        self.assertTrue(chunks[-1].is_last)
        self.assertEqual(
            self.mock_client.chat.completions.create.call_args.kwargs[
                "stream_options"
            ],
            {"include_usage": True},
        )


if __name__ == "__main__":
    unittest.main()
