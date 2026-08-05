"""Runtime layer — 8-phase request orchestration + SSE envelope + executor.

This package wraps AgentScope's basic ``Agent.reply()`` into a
production-grade request lifecycle:

* :class:`Runtime`       — 8-phase orchestrator (one per workspace)
* :class:`Envelope`      — SSE event → frontend envelope state machine
* :class:`AgentExecutor` — heartbeat-wrapped reply stream driver
* :class:`AgentBuilder`   — per-request agent assembly
* :class:`HookRegistry`   — pluggable lifecycle hooks at 8 phase points

These are the QwenPaw-style service-layer additions on top of
AgentScope's atomic ``ReActAgent.reply()``.
"""

from .phases import Phase
from .hooks import HookRegistry, HookAction, HookContext, HookResult
from .envelope import Envelope
from .executor import AgentExecutor
from .builder import AgentBuilder
from .runtime import Runtime

__all__ = [
    "Phase",
    "HookRegistry",
    "HookAction",
    "HookContext",
    "HookResult",
    "Envelope",
    "AgentExecutor",
    "AgentBuilder",
    "Runtime",
]
