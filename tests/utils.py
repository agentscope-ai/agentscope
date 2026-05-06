# -*- coding: utf-8 -*-
"""The utility module for unit tests in agentscope."""
from typing import Any, AsyncGenerator, Type

from pydantic import BaseModel

from agentscope.formatter import DashScopeChatFormatter
from agentscope.message import Msg
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.model._model_response import StructuredResponse


class AnyString:
    """A helper class for asserting any string value in unit tests."""

    def __eq__(self, other: object) -> bool:
        """Override equality check to match any string."""
        return isinstance(other, str)

    def __repr__(self) -> str:
        """Return a string representation for debugging purposes."""
        return "<AnyString>"


class MockModel(ChatModelBase):
    """A mock model for testing."""

    def __init__(self, context_length: int = 10000) -> None:
        """Initialize the mock model."""
        super().__init__(
            model_name="mock-model",
            stream=True,
            context_length=context_length,
            max_retries=0,
            fallback_model_name=None,
            formatter=DashScopeChatFormatter(),
        )
        self.mock_chat_responses = []
        self.mock_structured_response = None
        self.cnt = 0

    def set_responses(
        self,
        mock_responses: list[ChatResponse | list[ChatResponse]],
    ) -> None:
        """Set the mock responses."""
        self.mock_chat_responses = mock_responses
        if all(isinstance(_, ChatResponse) for _ in mock_responses):
            self.stream = False
        else:
            self.stream = True
        self.cnt = 0

    async def _call_api(
        self,  # pylint: disable=unused-argument
        *args: Any,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Mock the API call."""
        mock_responses = self.mock_chat_responses[self.cnt]
        self.cnt += 1
        if isinstance(mock_responses, list):

            async def _stream() -> AsyncGenerator[ChatResponse, None]:
                for response in mock_responses:
                    yield response

            return _stream()

        if isinstance(mock_responses, ChatResponse):
            return mock_responses

        raise AssertionError

    def set_structured_response(
        self,
        mock_response: StructuredResponse,
    ) -> None:
        """Set the mock structured responses."""
        self.mock_structured_response = mock_response

    async def _call_api_with_structured_output(
        self,
        model_name: str,
        messages: list[Msg],
        structured_model: Type[BaseModel] | dict,
        **kwargs: Any,
    ) -> StructuredResponse:
        """Mock the API call with structured output."""
        return self.mock_structured_response
