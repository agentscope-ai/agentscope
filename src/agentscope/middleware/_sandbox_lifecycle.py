# -*- coding: utf-8 -*-
"""Middleware that manages the sandbox session lifecycle around each agent call."""

import logging
import typing
from typing import AsyncGenerator, TYPE_CHECKING

from ._base import MiddlewareBase

if TYPE_CHECKING:
    from ..agent import Agent
from ..sandbox import (
    SandboxAcquireResult,
    SandboxContext,
    SandboxManager,
)

if typing.TYPE_CHECKING:
    from ..agent import Agent

logger = logging.getLogger(__name__)


class SandboxLifecycleMiddleware(MiddlewareBase):
    """Middleware that manages the sandbox session lifecycle around each reply.

    **Before** ``next_handler``:
    1. Read :class:`SandboxContext` from the middleware config.
    2. Acquire a sandbox via :class:`SandboxManager`.
    3. Start the sandbox.
    4. Optionally inject the live sandbox into the agent (e.g. by setting
       ``agent.workspace`` when an adapter is provided).

    **After** ``next_handler`` (in ``finally``):
    1. Persist sandbox session state via :class:`SandboxManager`.
    2. Release the sandbox (stop + optional shutdown).
    3. Close the execution-guard lease.
    4. Clear any injected workspace reference.

    Post-call failures (persist, release) are logged but do not propagate —
    this ensures the agent call result is always returned even if sandbox
    cleanup fails.
    """

    def __init__(
        self,
        sandbox_manager: SandboxManager,
        sandbox_context: SandboxContext | None = None,
        inject_workspace: bool = False,
    ) -> None:
        self.sandbox_manager = sandbox_manager
        self.sandbox_context = sandbox_context or SandboxContext()
        self.inject_workspace = inject_workspace
        self._current_result: SandboxAcquireResult | None = None

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler,
    ) -> AsyncGenerator:
        """Acquire sandbox before the call, release after."""
        session_id = getattr(agent.state, "session_id", None)
        user_id = getattr(agent.state, "user_id", None)

        result: SandboxAcquireResult | None = None
        previous_workspace = None

        try:
            result = await self.sandbox_manager.acquire(
                self.sandbox_context,
                session_id=session_id,
                user_id=user_id,
            )
            sandbox = result.sandbox

            try:
                await sandbox.start()
                if self.inject_workspace:
                    previous_workspace = getattr(agent, "workspace", None)
                    agent.workspace = sandbox
                self._current_result = result
                logger.debug(
                    "[sandbox-mw] Acquired sandbox %s",
                    getattr(sandbox.state, "session_id", "?"),
                )
            except Exception as exc:
                if self.inject_workspace:
                    agent.workspace = previous_workspace
                try:
                    await self.sandbox_manager.release(result)
                except Exception as release_err:
                    logger.warning(
                        "[sandbox-mw] Failed to release session after"
                        " pre-call failure: %s",
                        release_err,
                        exc_info=release_err,
                    )
                result.lease.close()
                raise exc

        except Exception as exc:
            logger.error("[sandbox-mw] Failed to acquire/start sandbox: %s", exc)
            raise

        try:
            async for item in next_handler():
                yield item
        finally:
            # Always run cleanup, even if the reply handler raises.
            self._current_result = None
            try:
                await self.sandbox_manager.persist_state(
                    result,
                    self.sandbox_context,
                    session_id=session_id,
                    user_id=user_id,
                )
            except Exception as exc:
                logger.warning(
                    "[sandbox-mw] Failed to persist sandbox state: %s",
                    exc,
                    exc_info=exc,
                )
            try:
                await self.sandbox_manager.release(result)
            except Exception as exc:
                logger.warning(
                    "[sandbox-mw] Failed to release sandbox session: %s",
                    exc,
                    exc_info=exc,
                )
            try:
                result.lease.close()
            except Exception as exc:
                logger.warning(
                    "[sandbox-mw] Failed to close guard lease: %s",
                    exc,
                    exc_info=exc,
                )
            if self.inject_workspace:
                agent.workspace = previous_workspace
