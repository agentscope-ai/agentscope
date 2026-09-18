# -*- coding: utf-8 -*-
"""The model related exceptions."""
from ._base import DeveloperOrientedException


class StructuredOutputError(DeveloperOrientedException):
    """Raised when the model fails to produce a valid structured output,
    e.g. it does not call the structured-output tool, returns an empty
    response, or the returned arguments fail JSON/schema validation."""


class ModelFirstChunkTimeoutError(TimeoutError):
    """Raised when a streaming model does not produce initial content."""

    def __init__(self, model: str, timeout: float) -> None:
        """Initialize the exception."""
        self.model = model
        self.timeout = timeout
        super().__init__(
            f"Model '{model}' produced no initial stream content within "
            f"{timeout:g} seconds.",
        )


class ModelStreamIdleTimeoutError(TimeoutError):
    """Raised when a model stream stops producing meaningful content."""

    def __init__(self, model: str, timeout: float) -> None:
        """Initialize the exception."""
        self.model = model
        self.timeout = timeout
        super().__init__(
            f"Model '{model}' stream produced no content for "
            f"{timeout:g} seconds.",
        )
