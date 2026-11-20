# -*- coding: utf-8 -*-
"""Unit tests for non-finite floats in tracing span serialization."""
import json
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from agentscope.middleware._tracing._utils import _serialize_to_str


def _reject_constant(value: str) -> Any:
    """Reject the non-standard NaN/Infinity JSON constants."""
    raise ValueError(f"non-standard JSON constant: {value}")


def _is_standard_json(text: str) -> bool:
    """Return whether ``text`` is parseable by a strict JSON parser."""
    try:
        json.loads(text, parse_constant=_reject_constant)
    except ValueError:
        return False
    return True


class _Color(Enum):
    """Sample enum used to exercise the serialization fallback."""

    RED = "red"


def test_nonfinite_floats_never_emit_invalid_json() -> None:
    """NaN and both infinities must stay out of span attribute JSON."""
    for value in (float("nan"), float("inf"), float("-inf")):
        rendered = _serialize_to_str({"result": value})
        assert _is_standard_json(rendered), rendered


def test_bare_nonfinite_float_is_quoted() -> None:
    """A top-level non-finite float is rendered as a JSON string."""
    assert _serialize_to_str(float("nan")) == '"nan"'
    assert _serialize_to_str(float("inf")) == '"inf"'
    assert _serialize_to_str(float("-inf")) == '"-inf"'


def test_nested_nonfinite_floats_are_sanitized() -> None:
    """Non-finite floats inside nested containers are sanitized too."""
    payload: dict[str, Any] = {
        "scores": [1.5, float("nan"), {"max": float("inf")}],
    }
    rendered = _serialize_to_str(payload)
    assert _is_standard_json(rendered), rendered
    parsed = json.loads(rendered)
    assert parsed["scores"][0] == 1.5
    assert parsed["scores"][1] == "nan"
    assert parsed["scores"][2]["max"] == "inf"


def test_ordinary_values_are_unchanged() -> None:
    """Regression: ordinary JSON values serialize exactly as before."""
    assert (
        _serialize_to_str(
            {"a": 1, "b": "x", "c": [True, None]},
        )
        == '{"a": 1, "b": "x", "c": [true, null]}'
    )
    assert _serialize_to_str([1, 2.5, -3.0]) == "[1, 2.5, -3.0]"
    assert _serialize_to_str("hello") == '"hello"'


def test_fallback_for_non_serializable_objects_still_works() -> None:
    """Regression: the TypeError fallback still converts exotic types."""
    rendered = _serialize_to_str(
        {
            "when": datetime(2026, 1, 2, 3, 4, 5),
            "delta": timedelta(seconds=90),
            "color": _Color.RED,
        },
    )
    assert _is_standard_json(rendered), rendered
    parsed = json.loads(rendered)
    assert parsed["when"] == "2026-01-02T03:04:05"
    assert parsed["delta"] == 90.0
    assert parsed["color"] == "red"
