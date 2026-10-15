# -*- coding: utf-8 -*-
"""The model related exceptions."""
from enum import StrEnum

from ._base import DeveloperOrientedException


class ContentBlockedPhase(StrEnum):
    """The phase in which a model provider blocked content."""

    INPUT = "input"
    """The provider rejected the model input."""

    OUTPUT = "output"
    """The provider blocked the generated model output."""

    UNKNOWN = "unknown"
    """The provider did not identify the blocked phase."""


class ContentBlockedError(DeveloperOrientedException):
    """Raised when a model provider blocks input or output content.

    The normalized fields let agent-level recovery logic handle different
    provider protocols without depending on their SDK exception classes.
    """

    def __init__(
        self,
        message: str,
        *,
        phase: ContentBlockedPhase = ContentBlockedPhase.UNKNOWN,
        provider: str | None = None,
        original_code: str | None = None,
        request_id: str | None = None,
        partial_output: bool = False,
    ) -> None:
        """Initialize a normalized content-blocked error.

        Args:
            message (`str`):
                The provider error message.
            phase (`ContentBlockedPhase`):
                The request phase in which content was blocked.
            provider (`str | None`):
                The provider identifier.
            original_code (`str | None`):
                The original provider error or finish code.
            request_id (`str | None`):
                The provider request identifier, when available.
            partial_output (`bool`):
                Whether model output was emitted before the block signal.
        """
        super().__init__(message)
        self.phase = phase
        self.provider = provider
        self.original_code = original_code
        self.request_id = request_id
        self.partial_output = partial_output


class StructuredOutputError(DeveloperOrientedException):
    """Raised when the model fails to produce a valid structured output,
    e.g. it does not call the structured-output tool, returns an empty
    response, or the returned arguments fail JSON/schema validation."""
