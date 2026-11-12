# -*- coding: utf-8 -*-
"""Shared ownership of Code Mode scripts and their unfinished host calls."""

import uuid

from ..message_bus import MessageBus, MessageBusKeys


class CodeModeRegistryGuard:
    """Keep execution markers until their owner confirms cleanup ended.

    No TTL is used. After host loss or an ambiguous storage failure, an
    operator must verify termination before conditionally removing the token.
    Cross-replica protection requires a shared, durable MessageBus backend.
    """

    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus

    async def acquire(self, session_id: str) -> str:
        """Atomically claim a session before any script code can run."""
        token = uuid.uuid4().hex
        acquired = await self._bus.registry_set_if(
            MessageBusKeys.code_mode_execution(session_id),
            "owner",
            token,
            expected=None,
        )
        if not acquired:
            raise RuntimeError(
                "Previous Code Mode execution is running or still cleaning "
                "up; its shared ownership must be released before retrying.",
            )
        return token

    async def release(self, session_id: str, token: str) -> None:
        """Remove only the marker belonging to this execution."""
        await self._bus.registry_del_if(
            MessageBusKeys.code_mode_execution(session_id),
            "owner",
            expected=token,
        )
