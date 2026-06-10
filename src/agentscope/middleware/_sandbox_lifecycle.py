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
    4. Optionally inject MCP tools from the sandbox workspace into the agent's
       toolkit (direction B — uses the in-container MCP gateway).
    5. Optionally inject the live sandbox into the agent (legacy mode).

    **After** ``next_handler`` (in ``finally``):
    1. Persist sandbox session state via :class:`SandboxManager`.
    2. Release the sandbox (stop + optional shutdown).
    3. Close the execution-guard lease.
    4. Eject injected MCP tools from the toolkit.
    5. Clear any injected workspace reference.

    Post-call failures (persist, release) are logged but do not propagate —
    this ensures the agent call result is always returned even if sandbox
    cleanup fails.
    """

    def __init__(
        self,
        sandbox_manager: SandboxManager,
        sandbox_context: SandboxContext | None = None,
        inject_workspace: bool = False,
        inject_mcp_tools: bool = True,
        tool_group_name: str = "sandbox",
    ) -> None:
        self.sandbox_manager = sandbox_manager
        self.sandbox_context = sandbox_context or SandboxContext()
        self.inject_workspace = inject_workspace
        self.inject_mcp_tools = inject_mcp_tools
        self.tool_group_name = tool_group_name
        self._current_result: SandboxAcquireResult | None = None
        self._injected_group = None

    async def _inject_tools(self, agent: "Agent", sandbox) -> None:
        """Inject MCP tools from the sandbox workspace into the agent toolkit.

        Direction B: if the sandbox wraps a WorkspaceBase (e.g. DockerWorkspace),
        fetch its MCP clients (which talk to the in-container gateway) and
        register them as a transient tool group on the agent's toolkit.
        """
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
        """Remove the transient sandbox tool group from the agent toolkit."""
        if self._injected_group is None:
            return

        # Deactivate the group so the agent no longer sees its tools
        if self.tool_group_name in agent.state.tool_context.activated_groups:
            agent.state.tool_context.activated_groups.remove(
                self.tool_group_name,
            )

        # Remove the group from the toolkit (identity check to avoid removing
        # a coincidentally same-named group added by something else).
        agent.toolkit.tool_groups = [
            g for g in agent.toolkit.tool_groups if g is not self._injected_group
        ]

        # Close the MCP clients (stateful ones hold open connections).
        for client in self._injected_group.mcps:
            try:
                if hasattr(client, "close") and callable(client.close):
                    await client.close()
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
                await self._inject_tools(agent, sandbox)
                if self.inject_workspace:
                    previous_workspace = getattr(agent, "workspace", None)
                    agent.workspace = sandbox
                self._current_result = result
                logger.debug(
                    "[sandbox-mw] Acquired sandbox %s",
                    getattr(sandbox.state, "session_id", "?"),
                )
            except Exception as exc:
                await self._eject_tools(agent)
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
            await self._eject_tools(agent)
            if self.inject_workspace:
                agent.workspace = previous_workspace
