# -*- coding: utf-8 -*-
"""Classify a fatal reply exception into a UI-facing :class:`ErrorInfo`.

Provider-agnostic: classification keys off the exception's HTTP
``status_code`` (carried by anthropic/openai API errors and any other
httpx-based upstream), so no provider package is imported here."""
from ...exception import DeveloperOrientedException
from ...types import ErrorType, ErrorInfo


_STATUS_MAP: dict[int, ErrorType] = {
    401: ErrorType.AUTHENTICATION,
    403: ErrorType.PERMISSION,
    429: ErrorType.RATE_LIMIT,
    400: ErrorType.INVALID_REQUEST,
    422: ErrorType.INVALID_REQUEST,
}

_GENERIC_MESSAGE: dict[ErrorType, str] = {
    ErrorType.AUTHENTICATION: (
        "Authentication failed — check the model's API key / credential."
    ),
    ErrorType.PERMISSION: (
        "Request not allowed — the credential lacks permission for this "
        "model or endpoint."
    ),
    ErrorType.RATE_LIMIT: "Rate limit or quota exceeded — try again later.",
    ErrorType.INVALID_REQUEST: "The request to the model was rejected as invalid.",
    ErrorType.UPSTREAM: "The upstream model service returned an error.",
    ErrorType.CONNECTION: (
        "Could not reach the model service — network error or timeout."
    ),
    ErrorType.INTERNAL: "An unexpected internal error occurred.",
    ErrorType.UNKNOWN: "The reply failed with an unknown error.",
}


def _classify_type(e: Exception) -> ErrorType:
    """Map an exception to an :class:`ErrorType` without importing any
    provider SDK."""
    status = getattr(e, "status_code", None)
    if isinstance(status, int):
        if status in _STATUS_MAP:
            return _STATUS_MAP[status]
        if status >= 500:
            return ErrorType.UPSTREAM
        return ErrorType.UNKNOWN

    # No HTTP status: framework-internal vs a network-ish failure.
    if isinstance(e, (TimeoutError, ConnectionError)):
        return ErrorType.CONNECTION
    if isinstance(e, DeveloperOrientedException):
        return ErrorType.INTERNAL
    return ErrorType.INTERNAL


def classify_error(e: Exception) -> ErrorInfo:
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
