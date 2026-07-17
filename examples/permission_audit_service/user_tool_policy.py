# -*- coding: utf-8 -*-
"""Application-level per-user tool policy for the permission hook example."""
from collections.abc import Collection, Mapping
from typing import Awaitable, Callable, TYPE_CHECKING

from agentscope.middleware import MiddlewareBase
from agentscope.permission import PermissionBehavior, PermissionDecision

if TYPE_CHECKING:
    from agentscope.agent import Agent


class UserToolPolicyMiddleware(MiddlewareBase):
    """Deny configured tools for the current application user."""

    def __init__(
        self,
        user_id: str,
        denied_tools_by_user: Mapping[str, Collection[str]],
    ) -> None:
        self.user_id = user_id
        self._denied_tools = frozenset(
            denied_tools_by_user.get(user_id, ()),
        )

    async def on_check_permission(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., Awaitable[PermissionDecision]],
    ) -> PermissionDecision:
        """Short-circuit calls denied by the application user policy."""
        del agent
        tool = input_kwargs["tool"]
        if tool.name in self._denied_tools:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=(
                    f"User {self.user_id!r} is not allowed to use "
                    f"{tool.name!r}."
                ),
                decision_reason="Application user policy",
            )

        return await next_handler(**input_kwargs)
