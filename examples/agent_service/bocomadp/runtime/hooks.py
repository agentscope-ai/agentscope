# -*- coding: utf-8 -*-
"""Lifecycle hook registry — pluggable extensions at 8 phase points.

Hooks are registered per-workspace and executed in priority order
(lower priority = runs first). Each hook receives a :class:`HookContext`
and returns a :class:`HookResult` that can short-circuit the request
or skip the agent build/execute step.

Usage::

    registry = HookRegistry()

    @registry.register(Phase.PRE_DISPATCH, priority=10)
    async def my_hook(ctx: HookContext) -> HookResult:
        # inspect / modify ctx before agent runs
        return HookResult()

This is distinct from ``agentscope.middleware.Middleware`` which wraps
a single agent's reply loop. Runtime hooks wrap the *request* — they
fire before/after the agent is built and executed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional
from collections import defaultdict

from .phases import Phase

logger = logging.getLogger(__name__)

# Type alias for a hook callable
HookCallable = Callable[["HookContext"], Awaitable["HookResult"]]


class HookAction:
    """Actions a hook can request."""

    CONTINUE = "continue"          # normal flow, keep going
    SHORT_CIRCUIT = "short_circuit"  # stop immediately, yield payload
    SKIP_AGENT = "skip_agent"      # skip build+execute, go to finalize


@dataclass
class HookResult:
    """Return value of a lifecycle hook."""

    action: str = HookAction.CONTINUE
    payload: Any = None  # used with SHORT_CIRCUIT


@dataclass
class HookContext:
    """Per-request context passed to every hook.

    Carries the raw request, the agent (after build), the envelope,
    and a free-form ``extras`` dict for inter-hook communication.
    """

    request: Any = None
    session_id: str = ""
    agent_id: str = ""
    workspace: Any = None
    app_services: Any = None
    input_msgs: list = field(default_factory=list)
    agent: Any = None  # populated after AgentBuilder.build
    agent_config: Any = None  # from MultiAgentManager, if available
    session_state: dict = field(default_factory=dict)
    extras: dict = field(default_factory=dict)
    _envelope: Any = None


@dataclass
class _HookEntry:
    callable: HookCallable
    priority: int
    name: str


class HookRegistry:
    """Registry of lifecycle hooks keyed by :class:`Phase`.

    Hooks within the same phase run in ascending priority order.
    """

    def __init__(self) -> None:
        self._hooks: dict[Phase, list[_HookEntry]] = defaultdict(list)

    def register(
        self,
        phase: Phase,
        *,
        priority: int = 100,
        name: str = "",
    ) -> Callable[[HookCallable], HookCallable]:
        """Decorator to register a hook for *phase*."""

        def decorator(fn: HookCallable) -> HookCallable:
            entry = _HookEntry(
                callable=fn,
                priority=priority,
                name=name or fn.__name__,
            )
            self._hooks[phase].append(entry)
            # Keep sorted by priority
            self._hooks[phase].sort(key=lambda e: e.priority)
            logger.debug(
                "hook registered: phase=%s name=%s priority=%d",
                phase.value,
                entry.name,
                priority,
            )
            return fn

        return decorator

    def unregister(self, phase: Phase, name: str) -> None:
        """Remove a hook by name."""
        self._hooks[phase] = [
            e for e in self._hooks[phase] if e.name != name
        ]

    async def run(self, phase: Phase, ctx: HookContext) -> HookResult:
        """Execute all hooks for *phase* in priority order.

        Stops at the first hook that returns a non-CONTINUE action.
        """
        for entry in self._hooks.get(phase, []):
            try:
                result = await entry.callable(ctx)
            except Exception:
                logger.exception(
                    "hook %s raised in phase %s",
                    entry.name,
                    phase.value,
                )
                continue
            if result.action != HookAction.CONTINUE:
                logger.info(
                    "hook %s short-circuited phase %s with action=%s",
                    entry.name,
                    phase.value,
                    result.action,
                )
                return result
        return HookResult()

    def list_hooks(self, phase: Optional[Phase] = None) -> list[str]:
        """Return hook names for debugging."""
        phases = [phase] if phase else list(self._hooks)
        names = []
        for p in phases:
            names.extend(e.name for e in self._hooks.get(p, []))
        return names


__all__ = [
    "HookRegistry",
    "HookAction",
    "HookContext",
    "HookResult",
    "HookCallable",
]
