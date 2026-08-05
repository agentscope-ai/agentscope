# -*- coding: utf-8 -*-
"""8-phase request orchestration.

Delegates to:

* :class:`Envelope`       — SSE state machine
* :class:`AgentBuilder`   — per-request agent assembly
* :class:`AgentExecutor`  — heartbeat-wrapped reply stream

All insertable features live in :class:`HookRegistry` instances.
The two fixed steps (build + execute) are the only agent-touching
code.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncGenerator

from .builder import AgentBuilder
from .envelope import Envelope
from .executor import AgentExecutor
from .hooks import HookAction, HookContext, HookRegistry
from .phases import Phase

logger = logging.getLogger(__name__)


class Runtime:
    """Per-workspace request orchestrator.

    One ``Runtime`` instance per workspace. ``run()`` is called once
    per request and yields SSE envelope dicts.

    Usage::

        runtime = Runtime(
            workspace=ws,
            app_services=svc,
            hook_registry=hooks,
            tool_registry=tools,
            middleware_registry=mws,
            provider_manager=pm,
        )
        async for envelope in runtime.run(request):
            # yield to SSE client
            ...
    """

    def __init__(
        self,
        *,
        workspace: Any = None,
        app_services: Any = None,
        hook_registry: HookRegistry | None = None,
        tool_registry: Any = None,
        middleware_registry: Any = None,
        provider_manager: Any = None,
        multi_agent_manager: Any = None,
        heartbeat_interval: float = 15.0,
    ) -> None:
        self.workspace = workspace
        self.app_services = app_services
        self.hook_registry = hook_registry or HookRegistry()
        self.tool_registry = tool_registry
        self.middleware_registry = middleware_registry
        self.provider_manager = provider_manager
        self.multi_agent_manager = multi_agent_manager
        self._heartbeat_interval = heartbeat_interval
        # Track active executors for cancel support
        self._active_executors: dict[str, AgentExecutor] = {}

    async def run(  # pylint: disable=too-many-branches
        self,
        request: Any,
    ) -> AsyncGenerator[dict, None]:
        """8-phase lifecycle orchestration."""
        request = self._normalize(request)
        ctx = self._build_context(request)
        hooks = self.hook_registry

        envelope = Envelope(session_id=ctx.session_id)
        ctx._envelope = envelope  # pylint: disable=protected-access
        skip_agent = False
        executor: AgentExecutor | None = None

        try:
            # --- [phase 1] PRE_DISPATCH ---
            r = await hooks.run(Phase.PRE_DISPATCH, ctx)
            if r.action == HookAction.SHORT_CIRCUIT:
                async for ev in envelope.from_text(
                    str(r.payload or ""),
                ):
                    yield ev
                return
            if r.action == HookAction.SKIP_AGENT:
                skip_agent = True

            if not skip_agent:
                # --- [fixed 1] slash command dispatch (placeholder) ---
                # TODO: wire slash command registry here
                pass

            if not skip_agent:
                # --- [phase 2] POST_DISPATCH ---
                r = await hooks.run(Phase.POST_DISPATCH, ctx)
                if r.action == HookAction.SHORT_CIRCUIT:
                    async for ev in envelope.from_text(
                        str(r.payload or ""),
                    ):
                        yield ev
                    skip_agent = True
                elif r.action == HookAction.SKIP_AGENT:
                    skip_agent = True

            if not skip_agent:
                # --- [phase 3] PRE_AGENT_BUILD ---
                r = await hooks.run(Phase.PRE_AGENT_BUILD, ctx)
                if r.action == HookAction.SHORT_CIRCUIT:
                    async for ev in envelope.from_text(
                        str(r.payload or ""),
                    ):
                        yield ev
                    skip_agent = True
                elif r.action == HookAction.SKIP_AGENT:
                    skip_agent = True

            if not skip_agent:
                # --- [fixed 2] build agent ---
                async for ev in envelope.emit_response_created():
                    yield ev

                builder = AgentBuilder(
                    tool_registry=self.tool_registry,
                    middleware_registry=self.middleware_registry,
                    provider_manager=self.provider_manager,
                )
                ctx.agent = await builder.build(ctx)

                # --- [phase 4] POST_AGENT_BUILD ---
                await hooks.run(Phase.POST_AGENT_BUILD, ctx)

                # --- [phase 5] PRE_EXECUTE ---
                r = await hooks.run(Phase.PRE_EXECUTE, ctx)
                if r.action == HookAction.SHORT_CIRCUIT:
                    async for ev in envelope.from_text(
                        str(r.payload or ""),
                    ):
                        yield ev
                    skip_agent = True
                elif r.action == HookAction.SKIP_AGENT:
                    skip_agent = True

            if not skip_agent:
                # --- [fixed 3] execute agent ---
                executor = AgentExecutor(
                    agent=ctx.agent,
                    envelope=envelope,
                    heartbeat_interval=self._heartbeat_interval,
                )
                self._active_executors[ctx.session_id] = executor
                async for obj in executor.run(ctx.input_msgs):
                    yield obj

                # --- [phase 6] POST_RESPONSE ---
                await hooks.run(Phase.POST_RESPONSE, ctx)

            # --- [finalize] ---
            async for obj in envelope.finalize():
                yield obj

        except Exception as exc:
            logger.exception(
                "runtime: error in run session=%s: %s",
                ctx.session_id,
                exc,
            )
            # --- [phase 7] ON_ERROR ---
            await hooks.run(Phase.ON_ERROR, ctx)
            async for obj in envelope.error_envelope(str(exc)):
                yield obj
        finally:
            # --- [phase 8] FINALLY ---
            await hooks.run(Phase.FINALLY, ctx)
            if ctx.session_id in self._active_executors:
                del self._active_executors[ctx.session_id]

    def cancel(self, session_id: str) -> bool:
        """Request cancellation of an active run.

        Returns True if a run was found and cancelled.
        """
        executor = self._active_executors.get(session_id)
        if executor is not None:
            executor.cancel()
            return True
        return False

    def _normalize(self, request: Any) -> Any:
        """Normalize the incoming request into a standard shape."""
        if isinstance(request, dict):
            return _RequestDict(request)
        return request

    def _build_context(self, request: Any) -> HookContext:
        """Build the per-request HookContext from the normalized request."""
        session_id = getattr(request, "session_id", "") or ""
        agent_id = getattr(request, "agent_id", "") or "default"
        input_msgs = getattr(request, "input_msgs", []) or []
        if not input_msgs:
            input_msgs = getattr(request, "input", []) or []

        # Look up agent config from MultiAgentManager if wired
        agent_config = None
        if self.multi_agent_manager is not None:
            agent_config = self.multi_agent_manager.get_agent(agent_id)

        return HookContext(
            request=request,
            session_id=session_id,
            agent_id=agent_id,
            workspace=self.workspace,
            app_services=self.app_services,
            input_msgs=list(input_msgs),
            agent_config=agent_config,
        )


class _RequestDict:
    """Adapter to allow dict requests to be used with getattr."""

    __slots__ = ("_data",)

    def __init__(self, data: dict) -> None:
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name: str) -> Any:
        data = object.__getattribute__(self, "_data")
        if name not in data:
            raise AttributeError(name)
        return data[name]


__all__ = ["Runtime"]
