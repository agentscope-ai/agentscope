# -*- coding: utf-8 -*-
"""The tracing interface class in agentscope."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.trace import Tracer
else:
    Tracer = "Tracer"


def _get_tracer() -> Tracer:
    """Get the tracer
    Returns:
        `Tracer`: The tracer with the name "agentscope" and version.
    """
    from opentelemetry import trace
    from ..._version import __version__

    return trace.get_tracer("agentscope", __version__)
