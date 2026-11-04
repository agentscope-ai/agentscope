# -*- coding: utf-8 -*-
"""The model related exceptions."""
from ._base import DeveloperOrientedException


class StructuredOutputError(DeveloperOrientedException):
    """Raised when the model fails to produce a valid structured output,
    e.g. it does not call the structured-output tool, returns an empty
    response, or the returned arguments fail JSON/schema validation."""
