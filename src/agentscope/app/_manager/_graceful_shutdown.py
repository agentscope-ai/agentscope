# -*- coding: utf-8 -*-
"""Global graceful shutdown manager for AgentScope.

Tracks in-flight agent requests and orchestrates a clean shutdown:

1. On shutdown signal (SIGTERM or explicit call), stop accepting new requests.
2. Wait for active requests to complete naturally (up to a configurable timeout).
3. If timeout expires, cancel remaining asyncio tasks to force termination.
4. Save agent state on interruption so work can be resumed.

This mirrors the Java ``GracefulShutdownManager`` but uses asyncio primitives
instead of threads and Reactor sinks.
"""
from __future__ import annotations

import asyncio
import enum
import signal
import sys
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

from ..._logging import logger

if TYPE_CHECKING:
    from ...agent import Agent
    from ...state import AgentState


class ShutdownState(enum.Enum):
    """Lifecycle states of the shutdown manager."""

    RUNNING = "running"
    SHUTTING_DOWN = "shutting_down"
    TERMINATED = "terminated"


@dataclass
class GracefulShutdownConfig:
    """Configuration for graceful shutdown behaviour."""

    shutdown_timeout_seconds: float | None = 30.0
    """Maximum seconds to wait for active requests before forcing cancellation.
    ``None`` means wait indefinitely."""

    save_state_on_interrupt: bool = True
    """Whether to persist agent state when a request is interrupted."""

    partial_reasoning_policy: str = "save_and_resume"
    """How to handle partial reasoning on shutdown.

    - ``"save_and_resume"``: save state and let the client retry.
    - ``"discard"``: drop partial work.
    """


@dataclass
class _ActiveRequestContext:
    """Internal tracking record for a single in-flight request."""

    request_id: str
    agent: Agent
    state: AgentState | None = None
    task: asyncio.Task | None = None
    interrupted: bool = False


class GracefulShutdownManager:
    """Singleton manager that orchestrates graceful shutdown.

    Usage::

        # In FastAPI lifespan (or test fixture)
        mgr = GracefulShutdownManager.get_instance()
        mgr.set_config(GracefulShutdownConfig(shutdown_timeout_seconds=60))
        mgr.install_signal_handlers()

        # Around each agent call
        request_id = mgr.register_request(agent)
        try:
            async for event in agent.reply_stream(...):
                yield event
        finally:
            mgr.unregister_request(request_id)
    """

    _instance: GracefulShutdownManager | None = None
    _lock: asyncio.Lock | None = None

    def __new__(cls) -> GracefulShutdownManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    @classmethod
    def get_instance(cls) -> GracefulShutdownManager:
        """Return the singleton instance."""
        return cls()

    def _initialize(self) -> None:
        if self._initialized:
            return
        self._state: ShutdownState = ShutdownState.RUNNING
        self._config: GracefulShutdownConfig = GracefulShutdownConfig()
        self._active_requests: dict[str, _ActiveRequestContext] = {}
        self._state_savers: dict[str, Callable[[AgentState], Awaitable[None]]] = {}
        self._monitor_task: asyncio.Task | None = None
        self._termination_event: asyncio.Event = asyncio.Event()
        self._initialized = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> ShutdownState:
        return self._state

    def set_config(self, config: GracefulShutdownConfig) -> None:
        self._initialize()
        self._config = config

    def get_config(self) -> GracefulShutdownConfig:
        self._initialize()
        return self._config

    def is_accepting_requests(self) -> bool:
        self._initialize()
        return self._state == ShutdownState.RUNNING

    def ensure_accepting_requests(self) -> None:
        if not self.is_accepting_requests():
            raise RuntimeError(
                "AgentScope is shutting down; no new requests are accepted.",
            )

    def install_signal_handlers(self) -> None:
        """Install SIGTERM / SIGINT handlers that trigger graceful shutdown.

        Safe to call multiple times; duplicate handlers are ignored by the
        asyncio event loop.
        """
        self._initialize()
        loop = asyncio.get_running_loop()

        def _on_signal(sig: int) -> None:
            logger.info(
                "Received signal %s, initiating graceful shutdown...",
                signal.Signals(sig).name,
            )
            asyncio.create_task(self.initiate_shutdown())

        try:
            loop.add_signal_handler(signal.SIGTERM, _on_signal, signal.SIGTERM)
        except (ValueError, OSError):
            pass  # Windows or restricted environment
        try:
            loop.add_signal_handler(signal.SIGINT, _on_signal, signal.SIGINT)
        except (ValueError, OSError):
            pass

    def register_request(self, agent: Agent) -> str:
        """Register a new in-flight request.

        Returns:
            A unique request id that must be passed to ``unregister_request``.
        """
        self._initialize()
        self.ensure_accepting_requests()
        request_id = uuid.uuid4().hex
        self._active_requests[request_id] = _ActiveRequestContext(
            request_id=request_id,
            agent=agent,
        )
        logger.debug(
            "Registered shutdown-tracked request %s for agent %s",
            request_id,
            agent.name,
        )
        return request_id

    def bind_request_state(self, request_id: str, state: AgentState) -> None:
        """Bind the resolved per-call state to the tracked request."""
        self._initialize()
        ctx = self._active_requests.get(request_id)
        if ctx is not None:
            ctx.state = state

    def bind_state_saver(
        self,
        agent_name: str,
        saver: Callable[[AgentState], Awaitable[None]],
    ) -> None:
        """Register an async callback that persists agent state on interrupt."""
        self._initialize()
        self._state_savers[agent_name] = saver

    def unregister_request(self, request_id: str) -> None:
        """Mark a request as finished."""
        self._initialize()
        if not request_id:
            return
        removed = self._active_requests.pop(request_id, None)
        if removed:
            logger.debug(
                "Unregistered shutdown-tracked request %s",
                request_id,
            )
        self._maybe_set_terminated()

    def register_task(self, request_id: str, task: asyncio.Task) -> None:
        """Associate the asyncio task driving the request with its context."""
        self._initialize()
        ctx = self._active_requests.get(request_id)
        if ctx is not None:
            ctx.task = task

    def check_and_clear_shutdown_interrupted(self, state: AgentState) -> bool:
        """Return *True* if the state was marked interrupted by shutdown.

        Clears the flag so a retry is treated as a resume rather than a fresh
        request.
        """
        if state.shutdown_interrupted:
            state.shutdown_interrupted = False
            return True
        return False

    # ------------------------------------------------------------------
    # Shutdown orchestration
    # ------------------------------------------------------------------

    async def initiate_shutdown(self) -> bool:
        """Initiate graceful shutdown.

        Returns:
            ``True`` if this call actually started the shutdown sequence,
            ``False`` if already shutting down or terminated.
        """
        self._initialize()
        if self._state == ShutdownState.TERMINATED:
            return False
        if self._state == ShutdownState.RUNNING:
            self._state = ShutdownState.SHUTTING_DOWN
            logger.info(
                "Graceful shutdown initiated, %d active request(s), timeout=%s",
                len(self._active_requests),
                self._config.shutdown_timeout_seconds,
            )
            self._start_monitor()
        return True

    async def wait_for_termination(self, timeout: float | None = None) -> bool:
        """Block until shutdown reaches ``TERMINATED``.

        Args:
            timeout: Max seconds to wait. ``None`` means wait indefinitely.

        Returns:
            ``True`` if terminated, ``False`` if timed out.
        """
        self._initialize()
        self._maybe_set_terminated()
        if self._state == ShutdownState.TERMINATED:
            return True
        try:
            await asyncio.wait_for(
                self._termination_event.wait(),
                timeout=timeout,
            )
            return True
        except asyncio.TimeoutError:
            return False

    def reset_for_testing(self) -> None:
        """Reset internal state. Intended for tests only."""
        self._initialize()
        self._state = ShutdownState.RUNNING
        self._active_requests.clear()
        self._state_savers.clear()
        self._termination_event.clear()
        if self._monitor_task is not None and not self._monitor_task.done():
            self._monitor_task.cancel()
        self._monitor_task = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _start_monitor(self) -> None:
        if self._monitor_task is not None:
            return
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(),
            name="agentscope-shutdown-monitor",
        )

    async def _monitor_loop(self) -> None:
        try:
            while self._state == ShutdownState.SHUTTING_DOWN:
                if self._active_requests:
                    timeout = self._config.shutdown_timeout_seconds
                    if timeout is not None:
                        await asyncio.sleep(timeout)
                        if self._state != ShutdownState.SHUTTING_DOWN:
                            break
                        logger.info(
                            "Shutdown timeout reached, force-cancelling %d "
                            "active request(s)",
                            len(self._active_requests),
                        )
                        await self._force_cancel_all()
                    else:
                        await asyncio.sleep(1.0)
                else:
                    self._state = ShutdownState.TERMINATED
                    self._termination_event.set()
                    logger.info("Graceful shutdown complete.")
                    break
        except asyncio.CancelledError:
            pass

    async def _force_cancel_all(self) -> None:
        for ctx in list(self._active_requests.values()):
            if ctx.task is not None and not ctx.task.done():
                logger.info(
                    "Force-cancelling request %s for agent %s",
                    ctx.request_id,
                    ctx.agent.name,
                )
                ctx.task.cancel()
            if self._config.save_state_on_interrupt and ctx.state is not None:
                ctx.state.shutdown_interrupted = True
                await self._save_state(ctx.agent.name, ctx.state)
        self._state = ShutdownState.TERMINATED
        self._termination_event.set()

    async def _save_state(self, agent_name: str, state: AgentState) -> None:
        saver = self._state_savers.get(agent_name)
        if saver is not None:
            try:
                await saver(state)
                logger.debug("Saved state for agent %s on shutdown", agent_name)
            except Exception as e:
                logger.warning(
                    "Failed to save state for agent %s on shutdown: %s",
                    agent_name,
                    e,
                )

    def _maybe_set_terminated(self) -> None:
        if (
            self._state == ShutdownState.SHUTTING_DOWN
            and not self._active_requests
        ):
            self._state = ShutdownState.TERMINATED
            self._termination_event.set()
            logger.info("Graceful shutdown complete (all requests finished).")


# Convenience re-export at module level for cleaner imports
__all__ = [
    "GracefulShutdownConfig",
    "GracefulShutdownManager",
    "ShutdownState",
]
