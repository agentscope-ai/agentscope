# -*- coding: utf-8 -*-
"""A minimal scripted chat model for the example tests.

Mirrors the pattern of the repository's own ``tests/utils.py`` MockModel
but stays self-contained so the example tests do not depend on repo test
internals.
"""
from typing import Any, AsyncGenerator, Type

from pydantic import BaseModel

from agentscope.credential import CredentialBase
from agentscope.formatter import OpenAIChatFormatter
from agentscope.model import ChatModelBase, ChatResponse


class MockCredential(CredentialBase):
    """A do-nothing credential."""

    @classmethod
    def get_chat_model_class(cls) -> Type["ChatModelBase"]:
        """Return the mock model class."""
        return MockModel


class MockModel(ChatModelBase):
    """Replays scripted responses; each entry answers one model call.

    An entry that is a list is served as a streaming call (each element
    yielded, or raised if it is an exception); a bare ``ChatResponse``
    is served as a non-stream call.
    """

    class Parameters(BaseModel):
        """No parameters."""

    def __init__(self) -> None:
        super().__init__(
            credential=MockCredential(),
            model="mock-model",
            stream=True,
            parameters=MockModel.Parameters(),
            context_size=100000,
        )
        self.formatter = OpenAIChatFormatter()
        self.responses: list = []
        self.cnt = 0
        # Re-declared here (the base __init__ also sets it) so that
        # set_responses' toggling is visibly an attribute update.
        self.stream = True

    def set_responses(self, responses: list) -> None:
        """Script the upcoming model calls."""
        self.responses = responses
        self.cnt = 0
        self.stream = all(isinstance(r, list) for r in responses)

    async def _call_api(  # pylint: disable=unused-argument
        self,
        *args: Any,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Serve the next scripted response."""
        scripted = self.responses[self.cnt]
        self.cnt += 1
        if isinstance(scripted, BaseException):
            raise scripted
        if isinstance(scripted, list):

            async def _stream() -> AsyncGenerator[ChatResponse, None]:
                for item in scripted:
                    if isinstance(item, BaseException):
                        raise item
                    yield item

            return _stream()
        return scripted
