# -*- coding: utf-8 -*-
"""Validate final tool arguments before invoking the tool."""
from copy import deepcopy
import inspect
from typing import Any, AsyncGenerator, Callable

import jsonschema

from ._base import ToolBase, ToolMiddlewareBase
from ._response import ToolChunk
from ..message import TextBlock, ToolResultState


def _unchanged(left: Any, right: Any) -> bool:
    """Compare argument snapshots without equating bools and numbers."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return len(left) == len(right) and all(
            _unchanged(left_key, right_key) and _unchanged(left_val, right_val)
            for (left_key, left_val), (right_key, right_val) in zip(
                left.items(),
                right.items(),
            )
        )
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(
            _unchanged(left_val, right_val)
            for left_val, right_val in zip(left, right)
        )
    return left == right


class ToolValidationMiddleware(ToolMiddlewareBase):
    """Check the final schema and an application policy before tool execution.

    Register this middleware last, after all input-rewriting middleware.
    Business arguments are copied so caller-owned nested values cannot change
    after validation. The validator receives a separate copy and must not
    modify it. AgentScope's injected ``_agent_state`` is forwarded unchanged,
    outside business-argument validation; it is not an authentication source.

    This wraps selected tool invocations, not direct ``tool.call`` calls or
    externally executed tools. Validation is admission-time only: it cannot
    revoke an action already running or guarantee a real-time deadline.
    """

    def __init__(
        self,
        validator: Callable[[ToolBase, dict[str, Any]], str | None],
    ) -> None:
        """Bind a synchronous application validator.

        Args:
            validator (`Callable[[ToolBase, dict[str, Any]], str | None]`):
                Receives the tool and its final business arguments. Return
                ``None`` to allow execution, or a string (including an empty
                string) to deny it. Bind trusted identity, context and
                validity rules in the host, for example using a closure.
                Validators must be synchronous and must not rewrite inputs.
                Exceptions propagate without executing the tool.

        Raises:
            `TypeError`:
                If the validator is not callable or is asynchronous.
        """
        if (
            not callable(validator)
            or inspect.iscoroutinefunction(
                validator,
            )
            or inspect.iscoroutinefunction(
                getattr(validator, "__call__", None),
            )
        ):
            raise TypeError("validator must be a synchronous callable.")
        self._validator = validator

    async def on_tool_call(
        self,
        tool: ToolBase,
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[ToolChunk, None]],
    ) -> AsyncGenerator[ToolChunk, None]:
        """Deny invalid inputs or forward the checked invocation.

        Argument-schema violations yield a ``DENIED`` chunk. Invalid schemas,
        validator exceptions, invalid return types and input rewrites raise
        without invoking ``next_handler``. When used via ``Toolkit``, ordinary
        exceptions follow its existing tool-error handling.
        """
        state_kwargs = {}
        business_kwargs = dict(input_kwargs)
        if (
            tool.is_state_injected
            and not tool.is_mcp
            and not tool.is_external_tool
            and "_agent_state" in business_kwargs
        ):
            state_kwargs["_agent_state"] = business_kwargs.pop("_agent_state")
        checked_kwargs = deepcopy(business_kwargs)

        try:
            jsonschema.validate(checked_kwargs, tool.input_schema)
        except jsonschema.ValidationError:
            reason = "The final tool arguments do not match the schema."
        else:
            policy_kwargs = deepcopy(checked_kwargs)
            reason = self._validator(tool, policy_kwargs)
            if reason is not None and not isinstance(reason, str):
                if inspect.iscoroutine(reason):
                    reason.close()
                raise TypeError("validator must return None or a string.")
            if not _unchanged(policy_kwargs, checked_kwargs):
                raise ValueError("validator must not modify tool arguments.")

        if reason is not None:
            yield ToolChunk(
                content=[TextBlock(text=reason)],
                state=ToolResultState.DENIED,
            )
            return

        async for chunk in next_handler(**checked_kwargs, **state_kwargs):
            yield chunk
