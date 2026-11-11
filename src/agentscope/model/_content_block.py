# -*- coding: utf-8 -*-
"""Helpers for normalizing provider content-block errors."""
from collections.abc import Mapping
from typing import Any

from ..exception import ContentBlockedError, ContentBlockedPhase


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    """Convert SDK response objects to a mapping when possible."""
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dumped
    return None


def _get_error_field(error: Exception, field: str) -> Any:
    """Read an error field from common OpenAI-compatible SDK shapes."""
    body = _as_mapping(getattr(error, "body", None))
    if body is not None:
        nested = _as_mapping(body.get("error"))
        if nested is not None and nested.get(field) is not None:
            return nested[field]
        if body.get(field) is not None:
            return body[field]
    return getattr(error, field, None)


def _get_request_id(error: Exception) -> str | None:
    """Extract a request identifier from an SDK exception."""
    request_id = getattr(error, "request_id", None)
    if request_id is not None:
        return str(request_id)

    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    for header in ("x-request-id", "request-id"):
        value = headers.get(header)
        if value is not None:
            return str(value)
    return None


def _normalize_openai_compatible_content_error(
    error: Exception,
    *,
    provider: str,
    blocked_codes: set[str],
    phase: ContentBlockedPhase,
    code_field: str = "code",
) -> ContentBlockedError | None:
    """Build an error from an explicitly recognized provider code.

    Args:
        error (`Exception`):
            The SDK exception. Fields are read from nested ``body.error``,
            then ``body``, then exception attributes, in that order.
        provider (`str`):
            Provider identifier to include in the normalized error.
        blocked_codes (`set[str]`):
            Recognized codes, already case-folded for comparison.
        phase (`ContentBlockedPhase`):
            The phase assigned by the adapter, or UNKNOWN if ambiguous.
        code_field (`str`, defaults to `"code"`):
            The structured field identifying the error. Use ``"type"``
            only for providers whose payload uses that field.

    Returns:
        `ContentBlockedError | None`:
            A new error retaining the original code's casing and available
            request ID, or ``None`` for absent or unrecognized codes. Error
            text and HTTP status alone never determine a match.

    This helper returns rather than raises the error. The calling model
    layer is responsible for chaining it to the original SDK exception.
    """
    code = _get_error_field(error, code_field)
    if code is None:
        return None
    original_code = str(code)
    if original_code.casefold() not in blocked_codes:
        return None

    message = _get_error_field(error, "message")
    return ContentBlockedError(
        str(message) if message is not None else str(error),
        phase=phase,
        provider=provider,
        original_code=original_code,
        request_id=_get_request_id(error),
    )


def _enum_name(value: Any) -> str | None:
    """Return a stable uppercase name for SDK enum-like values."""
    if value is None:
        return None
    raw_value = getattr(value, "name", None)
    if raw_value is None:
        raw_value = getattr(value, "value", value)
    return str(raw_value).rsplit(".", maxsplit=1)[-1].upper()


def _openai_compatible_response_block_error(
    choice: Any,
    *,
    provider: str,
    request_id: str | None,
    partial_output: bool = False,
    include_refusal: bool = False,
    include_content_filter: bool = True,
) -> ContentBlockedError | None:
    """Build an output-block error from one Chat Completions choice.

    Args:
        choice (`Any`):
            An SDK response or stream choice exposing ``finish_reason`` and
            optionally ``message`` or ``delta`` attributes.
        provider (`str`):
            Provider identifier for the normalized error.
        request_id (`str | None`):
            The identifier obtained by the caller from the response.
        partial_output (`bool`, defaults to `False`):
            Whether output preceded this signal. Copied to the error;
            this helper does not track stream consumption itself.
        include_refusal (`bool`, defaults to `False`):
            Also recognize a non-empty explicit refusal string in ``delta``
            or, if absent, ``message``. Content-filter finish reasons are
            controlled separately by ``include_content_filter``.
        include_content_filter (`bool`, defaults to `True`):
            Whether to recognize a content-filter finish reason. Adapters
            pass their explicit opt-in; refusal handling is independent.

    Returns:
        `ContentBlockedError | None`:
            An OUTPUT-phase error, or ``None`` when no supported signal is
            found. Ordinary response text is never scanned for refusals.
    """
    finish_reason = _enum_name(getattr(choice, "finish_reason", None))
    if include_content_filter and finish_reason == "CONTENT_FILTER":
        return ContentBlockedError(
            f"{provider} blocked the generated content.",
            phase=ContentBlockedPhase.OUTPUT,
            provider=provider,
            original_code="content_filter",
            request_id=request_id,
            partial_output=partial_output,
        )

    if include_refusal:
        content = getattr(choice, "delta", None)
        if content is None:
            content = getattr(choice, "message", None)
        refusal = getattr(content, "refusal", None)
        if isinstance(refusal, str) and refusal:
            return ContentBlockedError(
                (
                    f"{provider} refused the response."
                    if getattr(choice, "delta", None) is not None
                    else refusal
                ),
                phase=ContentBlockedPhase.OUTPUT,
                provider=provider,
                original_code="refusal",
                request_id=request_id,
                partial_output=partial_output,
            )
    return None
