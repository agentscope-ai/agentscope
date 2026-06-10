# -*- coding: utf-8 -*-
"""Middleware that wires a sandbox workspace into an agent's toolkit.

Python-native style (direction B):
- **Lazy acquire** on first ``on_reply`` — no per-call container churn.
- **Persist state** after the reply so sandbox resume works next time.
- **Eject MCP tools** so the agent doesn't carry stale references.
- **Do NOT stop/shutdown the container** — that is the workspace manager's
  job (TTL sweeper).  The sandbox stays alive across runs.

This mirrors how ``ChatService`` already works: the workspace is resolved
once per run, injected into the toolkit, and the container is torn down
later by the manager's background sweeper, not by the middleware.
"""

import inspect
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
    """Wires a sandbox into the agent toolkit for the duration of one run.

    - **Acquire** on first ``on_reply`` (lazy).
    - **Inject** MCP tools from the sandbox workspace into ``agent.toolkit``.
    - **Persist** sandbox state after the reply for next-run resume.
    - **Eject** MCP tools before returning so the agent is clean.
    - **Never stop/shutdown** the container — leave it running for the
      workspace manager to recycle.
    """

    def __init__(
        self,
        sandbox_manager: SandboxManager,
        sandbox_context: SandboxContext | None = None,
        inject_mcp_tools: bool = True,
        tool_group_name: str = "sandbox",
    ) -> None:
        self.sandbox_manager = sandbox_manager
        self.sandbox_context = sandbox_context or SandboxContext()
        self.inject_mcp_tools = inject_mcp_tools
        self.tool_group_name = tool_group_name
        self._result: SandboxAcquireResult | None = None
        self._injected_group = None

    async def _ensure_acquired(
        self,
        agent: "Agent",
        session_id: str | None,
        user_id: str | None,
    ) -> SandboxAcquireResult:
        """Lazy acquire: reuse if already held for this middleware instance."""
        if self._result is not None:
            return self._result

        result = await self.sandbox_manager.acquire(
            self.sandbox_context,
            session_id=session_id,
            user_id=user_id,
        )
        sandbox = result.sandbox
        await sandbox.start()
        await self._inject_tools(agent, sandbox)
        self._result = result
        logger.debug(
            "[sandbox-mw] Acquired sandbox %s",
            getattr(sandbox.state, "session_id", "?"),
        )
        return result

    async def _inject_tools(self, agent: "Agent", sandbox) -> None:
        """Inject MCP tools from the sandbox workspace into the toolkit."""
        if not self.inject_mcp_tools:
            return

        workspace = getattr(sandbox, "workspace", None)
        if workspace is None:
            logger.debug(
                "[sandbox-mw] Sandbox has no workspace attribute, skipping"
                " MCP tool injection",
            )
            return

        try:
            mcps = await workspace.list_mcps()
        except Exception as exc:
            logger.warning(
                "[sandbox-mw] Failed to list MCPs from workspace: %s",
                exc,
                exc_info=exc,
            )
            return

        if not mcps:
            logger.debug(
                "[sandbox-mw] Workspace returned no MCPs, skipping injection",
            )
            return

        from ..tool import ToolGroup

        group = ToolGroup(
            name=self.tool_group_name,
            description=f"Sandbox tools from {type(workspace).__name__}",
            mcps=list(mcps),
        )

        agent.toolkit.tool_groups.append(group)
        self._injected_group = group

        if self.tool_group_name not in agent.state.tool_context.activated_groups:
            agent.state.tool_context.activated_groups.append(
                self.tool_group_name,
            )

        logger.debug(
            "[sandbox-mw] Injected %d MCP client(s) as tool group '%s'",
            len(mcps),
            self.tool_group_name,
        )

    async def _eject_tools(self, agent: "Agent") -> None:
        """Remove the transient sandbox tool group from the toolkit."""
        if self._injected_group is None:
            return

        if self.tool_group_name in agent.state.tool_context.activated_groups:
            agent.state.tool_context.activated_groups.remove(
                self.tool_group_name,
            )

        agent.toolkit.tool_groups = [
            g for g in agent.toolkit.tool_groups if g is not self._injected_group
        ]

        for client in self._injected_group.mcps:
            try:
                close_fn = getattr(client, "close", None)
                if close_fn is not None and callable(close_fn):
                    if inspect.iscoroutinefunction(close_fn):
                        await close_fn()
                    else:
                        close_fn()
            except Exception as exc:
                logger.warning(
                    "[sandbox-mw] Failed to close MCP client '%s': %s",
                    getattr(client, "name", "?"),
                    exc,
                    exc_info=exc,
                )

        logger.debug(
            "[sandbox-mw] Ejected tool group '%s'",
            self.tool_group_name,
        )
        self._injected_group = None

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler,
    ) -> AsyncGenerator:
        """Acquire sandbox on first call, persist state after, never shutdown."""
        session_id = getattr(agent.state, "session_id", None)
        user_id = getattr(agent.state, "user_id", None)

        try:
            result = await self._ensure_acquired(agent, session_id, user_id)
        except Exception as exc:
            logger.error("[sandbox-mw] Failed to acquire/start sandbox: %s", exc)
            raise

        try:
            async for item in next_handler():
                yield item
        finally:
            # Persist state so next run can resume
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

            # Eject tools so the agent doesn't carry stale references
            await self._eject_tools(agent)

            # Release the execution-guard lease (NOT the container)
            try:
                result.lease.close()
            except Exception as exc:
                logger.warning(
                    "[sandbox-mw] Failed to close guard lease: %s",
                    exc,
                    exc_info=exc,
                )

            # Reset so the next run starts fresh (important when the
            # middleware instance is reused across runs).
            self._result = None

            # IMPORTANT: we do NOT call sandbox.stop() or sandbox.shutdown()
            # here.  The container stays alive for the workspace manager's
            # TTL sweeper to recycle it later.
