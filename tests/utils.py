# -*- coding: utf-8 -*-
"""The utility module for unit tests in agentscope."""
from typing import Any, AsyncGenerator, Type

from pydantic import BaseModel, Field

from agentscope.message import Msg
from agentscope.model import ChatModelBase, ChatResponse, StructuredResponse


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

    model: str = "mock-model"
    context_size: int | None = 10000
    output_size: int | None = 8192
    mock_chat_responses: Any = Field(default_factory=list)
    mock_structured_response: Any = None
    cnt: int = 0

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
