# -*- coding: utf-8 -*-
"""Serialization utilities for protocol events.

Provides JSON serialization/deserialization for client and server events.
"""

import json
import time
from dataclasses import asdict, is_dataclass
from typing import Any

from .client_events import (
    ClientEvent,
    CLIENT_EVENT_TYPES,
)
from .server_events import (
    ServerEvent,
    SERVER_EVENT_TYPES,
)


def _generate_event_id() -> str:
    """Generate a unique event ID.

    Returns:
        `str`:
            A unique event ID in format "evt_{12-char-hex}".
    """
    import uuid

    return f"evt_{uuid.uuid4().hex[:12]}"


def _get_timestamp_ms() -> int:
    """Get current timestamp in milliseconds.

    Returns:
        `int`:
            Current Unix timestamp in milliseconds.
    """
    return int(time.time() * 1000)


def _clean_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Remove None values from dictionary recursively.

    Args:
        d (`dict[str, Any]`):
            The dictionary to clean.

    Returns:
        `dict[str, Any]`:
            A new dictionary with None values removed.
    """
    result = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, dict):
            cleaned = _clean_dict(v)
            if cleaned:  # Only include non-empty dicts
                result[k] = cleaned
        elif is_dataclass(v) and not isinstance(v, type):
            cleaned = _clean_dict(asdict(v))
            if cleaned:
                result[k] = cleaned
        else:
            result[k] = v
    return result


def serialize_event(
    event: ClientEvent | ServerEvent,
    include_id: bool = True,
    include_timestamp: bool = True,
) -> str:
    """Serialize an event to JSON string.

    Args:
        event (`ClientEvent | ServerEvent`):
            The event to serialize.
        include_id (`bool`, optional):
            Whether to include event_id if not set. Defaults to True.
        include_timestamp (`bool`, optional):
            Whether to include timestamp if not set. Defaults to True.

    Returns:
        `str`:
            JSON string representation of the event.

    Example:
        .. code-block:: python

            from protocol.server_events import ServerSessionCreated
            event = ServerSessionCreated(
                session_id="sess_123",
                agent_name="assistant",
            )
            json_str = serialize_event(event)
    """
    if is_dataclass(event) and not isinstance(event, type):
        data = asdict(event)
    else:
        raise TypeError(f"Expected dataclass event, got {type(event)}")

    # Add event_id if not set
    if include_id and not data.get("event_id"):
        data["event_id"] = _generate_event_id()

    # Add timestamp if not set
    if include_timestamp and not data.get("timestamp"):
        data["timestamp"] = _get_timestamp_ms()

    # Clean None values
    data = _clean_dict(data)

    return json.dumps(data, ensure_ascii=False)


def _get_dataclass_fields(cls: type) -> set[str]:
    """Get all field names from a dataclass including inherited fields.

    Args:
        cls (`type`):
            The dataclass type to inspect.

    Returns:
        `set[str]`:
            Set of all field names from the dataclass and its parents.
    """
    import dataclasses

    fields = set()
    for klass in cls.__mro__:
        if dataclasses.is_dataclass(klass):
            for f in dataclasses.fields(klass):
                fields.add(f.name)
    return fields


def deserialize_client_event(json_str: str) -> ClientEvent:
    """Deserialize JSON string to a client event.

    Args:
        json_str (`str`):
            JSON string to deserialize.

    Returns:
        `ClientEvent`:
            Appropriate ClientEvent subclass instance.

    Raises:
        ValueError:
            If event type is unknown or JSON is invalid.

    Example:
        .. code-block:: python

            json_str = '{"type": "client.audio.append", "data": "base64..."}'
            event = deserialize_client_event(json_str)
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    event_type = data.get("type")
    if not event_type:
        raise ValueError("Missing 'type' field")

    if event_type not in CLIENT_EVENT_TYPES:
        raise ValueError(f"Unknown client event type: {event_type}")

    event_class = CLIENT_EVENT_TYPES[event_type]

    # Get valid field names for the event class
    valid_fields = _get_dataclass_fields(event_class)

    # Filter out 'type' and unknown fields
    filtered_data = {
        k: v for k, v in data.items() if k != "type" and k in valid_fields
    }

    try:
        return event_class(**filtered_data)
    except TypeError as e:
        raise ValueError(
            f"Failed to create {event_class.__name__}: {e}",
        ) from e


def deserialize_server_event(json_str: str) -> ServerEvent:
    """Deserialize JSON string to a server event.

    Args:
        json_str (`str`):
            JSON string to deserialize.

    Returns:
        `ServerEvent`:
            Appropriate ServerEvent subclass instance.

    Raises:
        ValueError:
            If event type is unknown or JSON is invalid.

    Example:
        .. code-block:: python

            json_str = '{"type": "server.session.created", "session_id":
            "s123"}'
            event = deserialize_server_event(json_str)
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    event_type = data.get("type")
    if not event_type:
        raise ValueError("Missing 'type' field")

    if event_type not in SERVER_EVENT_TYPES:
        raise ValueError(f"Unknown server event type: {event_type}")

    event_class = SERVER_EVENT_TYPES[event_type]

    # Get valid field names for the event class
    valid_fields = _get_dataclass_fields(event_class)

    # Filter out 'type' and unknown fields
    filtered_data = {
        k: v for k, v in data.items() if k != "type" and k in valid_fields
    }

    try:
        return event_class(**filtered_data)
    except TypeError as e:
        raise ValueError(
            f"Failed to create {event_class.__name__}: {e}",
        ) from e


def event_to_dict(
    event: ClientEvent | ServerEvent,
    include_id: bool = True,
    include_timestamp: bool = True,
) -> dict[str, Any]:
    """Convert event to dictionary.

    Args:
        event (`ClientEvent | ServerEvent`):
            The event to convert.
        include_id (`bool`, optional):
            Whether to include event_id if not set. Defaults to True.
        include_timestamp (`bool`, optional):
            Whether to include timestamp if not set. Defaults to True.

    Returns:
        `dict[str, Any]`:
            Dictionary representation of the event.
    """
    if is_dataclass(event) and not isinstance(event, type):
        data = asdict(event)
    else:
        raise TypeError(f"Expected dataclass event, got {type(event)}")

    # Add event_id if not set
    if include_id and not data.get("event_id"):
        data["event_id"] = _generate_event_id()

    # Add timestamp if not set
    if include_timestamp and not data.get("timestamp"):
        data["timestamp"] = _get_timestamp_ms()

    # Clean None values
    return _clean_dict(data)
