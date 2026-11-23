# -*- coding: utf-8 -*-
"""Serialize objects to JSON string."""
import datetime
import enum
import inspect
import json
import re
from dataclasses import is_dataclass
from typing import Any

from pydantic import BaseModel

from ...message import Msg


# Match entire JSON strings so keys and text containing NaN stay untouched.
_STRING_OR_NONFINITE = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|NaN|-?Infinity')
_QUOTED_NONFINITE = {
    "NaN": '"nan"',
    "Infinity": '"inf"',
    "-Infinity": '"-inf"',
}


def _to_serializable(
    obj: Any,
) -> Any:
    """Convert an object to a JSON serializable type.

    Args:
        obj (`Any`):
            The object to be converted to JSON serializable.

    Returns:
        `Any`:
            The converted JSON serializable object
    """

    # Handle primitive types first
    if isinstance(obj, (str, int, bool, float, type(None))):
        res = obj

    elif isinstance(obj, (list, tuple, set, frozenset)):
        res = [_to_serializable(x) for x in obj]

    elif isinstance(obj, dict):
        res = {str(key): _to_serializable(val) for (key, val) in obj.items()}

    elif isinstance(obj, (Msg, BaseModel)):
        # Dump the model's fields, so the span carries readable JSON
        # rather than a Python repr string.
        try:
            res = _to_serializable(obj.model_dump())
        except Exception:
            # Tracing must not fail because model serialization fails.
            res = repr(obj)

    elif is_dataclass(obj):
        res = repr(obj)

    elif inspect.isclass(obj) and issubclass(obj, BaseModel):
        res = repr(obj)

    elif isinstance(obj, (datetime.date, datetime.datetime, datetime.time)):
        res = obj.isoformat()

    elif isinstance(obj, datetime.timedelta):
        res = obj.total_seconds()

    elif isinstance(obj, enum.Enum):
        res = _to_serializable(obj.value)

    else:
        res = str(obj)

    return res


def _serialize_to_str(value: Any) -> str:
    """Serialize input value to JSON string.

    Args:
        value (`Any`):
            The input value

    Returns:
        `str`:
            JSON serialized string of the input value
    """
    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except TypeError:
        encoded = json.dumps(_to_serializable(value), ensure_ascii=False)

    if "NaN" in encoded or "Infinity" in encoded:
        return _STRING_OR_NONFINITE.sub(
            lambda token: _QUOTED_NONFINITE.get(token[0], token[0]),
            encoded,
        )
    return encoded
