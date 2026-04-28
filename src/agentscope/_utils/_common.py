# -*- coding: utf-8 -*-
"""The common utilities for agentscope library."""
import asyncio
import base64
import functools
import inspect
import json
import os
import types
import uuid
from datetime import datetime
from typing import Any, Callable

import requests
from json_repair import repair_json

from .._logging import logger
from ..exception import ToolJSONDecodeError


def _json_loads_with_repair(
    json_str: str,
    schema: dict | None = None,
) -> dict:
    """The given json_str maybe incomplete, e.g. '{"key', so we need to
    repair and load it into a Python object.

    .. note::
        This function is currently only used for parsing the streaming output
        of the argument field in `tool_use`, so the parsed result must be a
        dict.

    Args:
        json_str (`str`):
            The JSON string to parse, which may be incomplete or malformed.
        schema (`dict`, optional):
            An optional JSON schema to guide the repair process.

    Returns:
        `dict`:
            A dictionary parsed from the JSON string after repair attempts.
            Returns an empty dict if all repair attempts fail.
    """
    try:
        # Loads directly
        res = json.loads(json_str)
        if isinstance(res, dict):
            return res

        error_message = (
            f"Error: Your argument string is decoded into a {type(res)} "
            f"object, but a dict object is expected!"
        )
    except json.JSONDecodeError as e:
        error_message = (
            f"Error: When decoding your tool arguments from JSON format "
            f"to a Python dictionary, a JSONDecodeError was raised with "
            f"message: {str(e)}."
        )

    try:
        # Try to repair with json_repair
        repaired = repair_json(json_str, stream_stable=True, schema=schema)
        res = json.loads(repaired)
        if isinstance(res, dict):
            return res

    except Exception:
        # Whatever the error is, we throw the original error message to the
        # agent, which is more helpful for debugging.
        pass

    # If still failed, we throw the original error message to the agent, rather
    # than the error from json_repair, which is less helpful for debugging.
    if len(json_str) > 200:
        error_json_str = json_str[:100] + "[TRUNCATE]" + json_str[-100:]
        ellipsis_hint = (
            "(Because the JSON string is too long, a truncated label "
            '"[TRUNCATE]" is used here to indicate the truncation)'
        )
    else:
        error_json_str = json_str
        ellipsis_hint = ""

    raise ToolJSONDecodeError(
        f"""<system-reminder>{error_message}

Your argument string is decoded by the following code snippet{ellipsis_hint}:
```python
import json

your_tool_arguments = {repr(error_json_str)}
json.loads(your_tool_arguments)
```

**You should recorrect the arguments in JSON format.**</system-reminder>""",
    )


def _get_timestamp(add_random_suffix: bool = False) -> str:
    """Get the current timestamp in the format YYYY-MM-DD HH:MM:SS.sss."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    if add_random_suffix:
        # Add a random suffix to the timestamp
        timestamp += f"_{os.urandom(3).hex()}"

    return timestamp


async def _is_async_func(func: Callable) -> bool:
    """Check if the given function is an async function, including
    coroutine functions, async generators, and coroutine objects.
    """

    return (
        inspect.iscoroutinefunction(func)
        or inspect.isasyncgenfunction(func)
        or isinstance(func, types.CoroutineType)
        or isinstance(func, types.GeneratorType)
        and asyncio.iscoroutine(func)
        or isinstance(func, functools.partial)
        and await _is_async_func(func.func)
    )


async def _execute_async_or_sync_func(
    func: Callable,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Execute an async or sync function based on its type.

    Args:
        func (`Callable`):
            The function to be executed, which can be either async or sync.
        *args (`Any`):
            Positional arguments to be passed to the function.
        **kwargs (`Any`):
            Keyword arguments to be passed to the function.

    Returns:
        `Any`:
            The result of the function execution.
    """

    if await _is_async_func(func):
        return await func(*args, **kwargs)

    return func(*args, **kwargs)


def _get_bytes_from_web_url(
    url: str,
    max_retries: int = 3,
) -> str:
    """Get the bytes from a given URL.

    Args:
        url (`str`):
            The URL to fetch the bytes from.
        max_retries (`int`, defaults to `3`):
            The maximum number of retries.
    """
    for _ in range(max_retries):
        try:
            response = requests.get(url)
            response.raise_for_status()
            return response.content.decode("utf-8")

        except UnicodeDecodeError:
            return base64.b64encode(response.content).decode("ascii")

        except Exception as e:
            logger.info(
                "Failed to fetch bytes from URL %s. Error %s. Retrying...",
                url,
                str(e),
            )

    raise RuntimeError(
        f"Failed to fetch bytes from URL `{url}` after {max_retries} retries.",
    )


def _map_text_to_uuid(text: str) -> str:
    """Map the given text to a deterministic UUID string.

    Args:
        text (`str`):
            The input text to be mapped to a UUID.

    Returns:
        `str`:
            A deterministic UUID string derived from the input text.
    """
    return str(uuid.uuid3(uuid.NAMESPACE_DNS, text))
