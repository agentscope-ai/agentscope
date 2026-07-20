# -*- coding: utf-8 -*-
"""Classify a fatal reply exception into a UI-facing :class:`ErrorInfo`.

Provider-agnostic: classification keys off HTTP status codes and exception
class names, walking the ``__cause__`` / ``__context__`` chain (AgentScope
often wraps a provider error one layer deep). No provider SDK is imported;
matching is by class name so httpx / openai / anthropic / aiohttp are all
covered whether or not they are installed."""
from typing import Iterator

from ...exception import DeveloperOrientedException
from ...types import ErrorType, ErrorInfo


_STATUS_MAP: dict[int, ErrorType] = {
    401: ErrorType.AUTHENTICATION,
    403: ErrorType.PERMISSION,
    429: ErrorType.RATE_LIMIT,
    400: ErrorType.INVALID_REQUEST,
    404: ErrorType.INVALID_REQUEST,
    422: ErrorType.INVALID_REQUEST,
}

# Connection/timeout failures identified by MRO class name, so we catch
# httpx.TransportError subclasses and provider connection wrappers without
# importing (and thus requiring) those packages.
_NETWORK_EXC_NAMES: frozenset[str] = frozenset(
    {
        "TransportError",  # httpx base for Connect/Read/Write/Pool errors
        "APIConnectionError",  # openai / anthropic
        "APITimeoutError",  # openai / anthropic
        "ClientConnectionError",  # aiohttp
        "ClientConnectorError",  # aiohttp
    },
)

_GENERIC_MESSAGE: dict[ErrorType, str] = {
    ErrorType.AUTHENTICATION: (
        "Authentication failed — check the model's API key / credential."
    ),
    ErrorType.PERMISSION: (
        "Request not allowed — the credential lacks permission for this "
        "model or endpoint."
    ),
    ErrorType.RATE_LIMIT: "Rate limit or quota exceeded — try again later.",
    ErrorType.INVALID_REQUEST: (
        "The request to the model was rejected as invalid."
    ),
    ErrorType.UPSTREAM: "The upstream model service returned an error.",
    ErrorType.CONNECTION: (
        "Could not reach the model service — network error or timeout."
    ),
    ErrorType.INTERNAL: "An unexpected internal error occurred.",
    ErrorType.UNKNOWN: "The reply failed with an unknown error.",
}


def _causes(e: BaseException) -> Iterator[BaseException]:
    """Yield ``e`` then its ``__cause__`` / ``__context__`` chain, guarding
    against cycles."""
    seen: set[int] = set()
    exc: BaseException | None = e
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def _extract_status(e: BaseException) -> int | None:
    """Pull an HTTP status code from anywhere in the exception chain.

    Different clients expose it differently: openai/anthropic promote
    ``status_code`` onto the exception, aiohttp uses ``status``, and raw
    httpx keeps it on ``response.status_code``.
    """
    for exc in _causes(e):
        for src in (
            getattr(exc, "status_code", None),
            getattr(exc, "status", None),
            getattr(getattr(exc, "response", None), "status_code", None),
        ):
            if isinstance(src, int):
                return src
    return None


def _is_network_error(e: BaseException) -> bool:
    """Detect a connection/timeout failure anywhere in the chain, by
    builtin type or by provider class name."""
    for exc in _causes(e):
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return True
        if any(c.__name__ in _NETWORK_EXC_NAMES for c in type(exc).__mro__):
            return True
    return False


def _classify_type(e: Exception) -> ErrorType:
    """Map an exception to an :class:`ErrorType` without importing any
    provider SDK."""
    status = _extract_status(e)
    if status is not None:
        if status in _STATUS_MAP:
            return _STATUS_MAP[status]
        if status >= 500:
            return ErrorType.UPSTREAM
        return ErrorType.INVALID_REQUEST

    if _is_network_error(e):
        return ErrorType.CONNECTION
    if isinstance(e, DeveloperOrientedException):
        return ErrorType.INTERNAL
    return ErrorType.UNKNOWN


def _classify_error(e: Exception) -> ErrorInfo:
    """Classify a fatal reply exception into a structured
    :class:`ErrorInfo` for the frontend.

    The ``message`` is a generic per-type string (not the raw exception
    text) so no provider-internal details or credentials leak to the UI;
    the frontend localizes off the stable ``type`` key.

    Args:
        e (`Exception`):
            The exception that terminated the reply.

    Returns:
        `ErrorInfo`:
            The structured, UI-facing error description.
    """
    error_type = _classify_type(e)
    return ErrorInfo(
        type=error_type,
        message=_GENERIC_MESSAGE[error_type],
    )
