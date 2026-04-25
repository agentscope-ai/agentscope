# -*- coding: utf-8 -*-
"""The utility module for unit tests in agentscope."""
from typing import Any, AsyncGenerator

from agentscope.model import ChatModelBase, ChatResponse


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

    def __init__(self) -> None:
        """Initialize the mock model."""
        super().__init__(
            model_name="mock-model",
            stream=True,
            max_retries=0,
            fallback_model_name=None,
            formatter=None,
        )
        self.mock_responses = []
        self.cnt = 0

    def set_responses(
        self,
        mock_responses: list[ChatResponse | list[ChatResponse]],
    ) -> None:
        """Set the mock responses."""
        self.mock_responses = mock_responses
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
        mock_responses = self.mock_responses[self.cnt]
        self.cnt += 1
        if isinstance(mock_responses, list):

            async def _stream() -> AsyncGenerator[ChatResponse, None]:
                for response in mock_responses:
                    yield response

            return _stream()

        if isinstance(mock_responses, ChatResponse):
            return mock_responses

        raise AssertionError
