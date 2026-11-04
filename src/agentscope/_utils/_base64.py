# -*- coding: utf-8 -*-
"""Internal helpers for incrementally merging Base64 data."""
import base64


_BASE64_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/",
)


def _is_base64_shape(value: str) -> bool:
    """Return whether a string has the shape of standard Base64."""
    if len(value) % 4:
        return False

    padding = len(value) - len(value.rstrip("="))
    if padding > 2:
        return False

    return all(char in _BASE64_CHARS for char in value.rstrip("="))


class _Base64MergeState:
    """O(1) bookkeeping that keeps appending a chunk cheap.

    Validating the accumulated value costs ``O(payload)``, so validating it on
    every append makes a stream of ``n`` chunks cost ``O(n^2)``. The only fact
    worth carrying across appends is whether the current value is the one
    returned by :func:`_append_base64_chunk`, because every value it returns
    is already known to be well-formed Base64.

    ``produced`` therefore aliases the string stored in ``Base64Source.data``
    instead of copying it: strings are immutable, so the accumulator holds a
    reference to the very same object and never keeps a second payload. The
    reference is replaced on every append, and it simply stops matching once
    the caller assigns a different value to ``source.data`` -- which is what
    makes an external mutation fall back to full validation.
    """

    __slots__ = ("produced",)

    def __init__(self) -> None:
        self.produced: str | None = None

    def __eq__(self, other: object) -> bool:
        # Pydantic compares ``__pydantic_private__`` when models are compared,
        # so this cache must not make two identical sources unequal just
        # because one of them has been appended to.
        return isinstance(other, _Base64MergeState)

    def __hash__(self) -> int:
        return hash(_Base64MergeState)


def _remember(state: _Base64MergeState | None, value: str) -> str:
    """Record the value produced here and hand it back to the caller."""
    if state is not None:
        state.produced = value
    return value


def _append_base64_chunk(
    existing: str,
    incoming: str,
    *,
    validate: bool = False,
    state: _Base64MergeState | None = None,
) -> str:
    """Append an independently encoded Base64 chunk efficiently.

    Valid, unpadded Base64 can be concatenated with a newly encoded chunk.
    When the existing value has padding, only its final quantum needs to be
    decoded and re-encoded with the incoming bytes.

    ``state`` lets repeated appends skip re-validating the accumulated value.
    Without it the function stays stateless and validates on every call.
    """
    verified = state is not None and existing is state.produced

    if existing and not verified:
        if not _is_base64_shape(existing):
            return _remember(
                state,
                base64.b64encode(
                    base64.b64decode(existing, validate=validate)
                    + base64.b64decode(incoming, validate=validate),
                ).decode("ascii"),
            )

    incoming_bytes = base64.b64decode(incoming, validate=validate)
    encoded_incoming = base64.b64encode(incoming_bytes).decode("ascii")

    if not existing:
        return _remember(state, encoded_incoming)

    if not existing.endswith("="):
        return _remember(state, existing + encoded_incoming)

    tail = base64.b64decode(existing[-4:], validate=validate)
    return _remember(
        state,
        existing[:-4]
        + base64.b64encode(tail + incoming_bytes).decode("ascii"),
    )
