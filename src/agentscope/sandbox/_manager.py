# -*- coding: utf-8 -*-
"""Sandbox manager — manages the lifecycle of sandbox instances per call."""

import logging
from typing import Optional

from ._types import (
    SandboxAcquireResult,
    SandboxContext,
    SandboxExecutionGuard,
    SandboxIsolationKey,
    SandboxLease,
    SandboxState,
    noop_execution_guard,
)
from ._client import SandboxClient
from ._state_store import SandboxStateStore

logger = logging.getLogger(__name__)


class SandboxManager:
    """Manages the lifecycle of :class:`Sandbox` instances for the current call.

    Acquire priority:
    1. ``sandbox_context.external_sandbox`` — user-managed, guard does not apply.
    2. ``sandbox_context.external_state`` — resume from explicit state, guard
       does not apply.
    3. Persisted state in the store — harness-managed, guard applies.
    4. Fresh create — harness-managed, guard applies.

    When an execution guard is configured, the manager acquires a lease before
    sandbox resume/create for isolation keys that are present. The lease is
    carried by the :class:`SandboxAcquireResult` and must be closed by the
    caller after :meth:`release`, ensuring the full call window is covered.
    """

    def __init__(
        self,
        client: SandboxClient,
        state_store: SandboxStateStore,
        agent_id: str,
        execution_guard: SandboxExecutionGuard | None = None,
    ) -> None:
        self.client = client
        self.state_store = state_store
        self.agent_id = agent_id
        self.execution_guard = execution_guard or noop_execution_guard()

    async def acquire(
        self,
        sandbox_context: SandboxContext,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> SandboxAcquireResult:
        """Acquire a sandbox for the current call."""
        # Priority 1: user-supplied sandbox — guard does not apply
        if sandbox_context.external_sandbox is not None:
            external = sandbox_context.external_sandbox
            sid = "?"
            try:
                sid = external.state.session_id
            except Exception:
                pass
            logger.debug(
                "[sandbox] Priority 1: using user-managed sandbox: %s",
                sid,
            )
            return SandboxAcquireResult.user_managed(external)

        # Priority 2: user-supplied state — guard does not apply
        if sandbox_context.external_state is not None:
            sandbox = await self.client.resume(sandbox_context.external_state)
            logger.debug(
                "[sandbox] Priority 2: resuming from explicit state: %s",
                sandbox_context.external_state.session_id,
            )
            return SandboxAcquireResult.self_managed(sandbox)

        # Priority 3 / 4: harness-managed — apply guard when a scope key is present
        scope_key = SandboxIsolationKey.resolve(
            sandbox_context.isolation_scope,
            session_id,
            user_id,
            self.agent_id,
        )

        lease: SandboxLease = SandboxLease.noop()
        if scope_key is not None:
            logger.debug(
                "[sandbox] Acquiring execution guard for scope %s",
                scope_key,
            )
            lease = self.execution_guard(scope_key)

        try:
            if scope_key is not None:
                try:
                    state_json = await self.state_store.load(scope_key)
                    if state_json is not None:
                        logger.debug(
                            "[sandbox] Priority 3: resuming from persisted"
                            " state (scope=%s)",
                            scope_key,
                        )
                        state = self.client.deserialize_state(state_json)
                        sandbox = await self.client.resume(state)
                        return SandboxAcquireResult.self_managed(sandbox, lease)
                except Exception as exc:
                    logger.warning(
                        "[sandbox] Failed to load persisted state for scope"
                        " %s, falling through to fresh create: %s",
                        scope_key,
                        exc,
                        exc_info=exc,
                    )

            logger.debug("[sandbox] Priority 4: creating new sandbox")
            sandbox = await self.client.create(
                sandbox_context.workspace_spec,
                sandbox_context.snapshot_spec,
                sandbox_context.client_options,
            )
            return SandboxAcquireResult.self_managed(sandbox, lease)
        except Exception:
            # Guard must be released if acquire fails — the caller won't see
            # the result.
            lease.close()
            raise

    async def release(self, result: SandboxAcquireResult | None) -> None:
        """Release a sandbox after the current call."""
        if result is None:
            return
        sandbox = result.sandbox
        if sandbox is None:
            return

        # User-managed sandboxes are owned by the caller — the harness must
        # not stop/snapshot or shutdown them.
        if not result.self_managed:
            return

        try:
            await sandbox.stop()
        except Exception as exc:
            logger.warning("[sandbox] Sandbox stop failed: %s", exc, exc_info=exc)

        try:
            await sandbox.shutdown()
        except Exception as exc:
            logger.warning(
                "[sandbox] Sandbox shutdown failed: %s",
                exc,
                exc_info=exc,
            )

    async def persist_state(
        self,
        result: SandboxAcquireResult,
        sandbox_context: SandboxContext | None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Persist sandbox state for the current call."""
        if result is None or result.sandbox is None:
            return
        if not result.self_managed:
            return

        try:
            state = result.sandbox.state
        except Exception as exc:
            logger.warning(
                "[sandbox] Failed to read sandbox state: %s",
                exc,
                exc_info=exc,
            )
            return

        if state is None:
            return

        scope_key = SandboxIsolationKey.resolve(
            sandbox_context.isolation_scope if sandbox_context else None,
            session_id,
            user_id,
            self.agent_id,
        )
        if scope_key is None:
            logger.debug(
                "[sandbox] No scope key available, skipping state persistence",
            )
            return

        try:
            json_str = self.client.serialize_state(state)
            await self.state_store.save(scope_key, json_str)
            logger.debug(
                "[sandbox] Persisted sandbox state for scope %s: session_id=%s",
                scope_key,
                state.session_id,
            )
        except Exception as exc:
            logger.warning(
                "[sandbox] Failed to persist sandbox state: %s",
                exc,
                exc_info=exc,
            )

    async def clear_state(
        self,
        sandbox_context: SandboxContext | None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Clear persisted sandbox state."""
        scope_key = SandboxIsolationKey.resolve(
            sandbox_context.isolation_scope if sandbox_context else None,
            session_id,
            user_id,
            self.agent_id,
        )
        if scope_key is None:
            return

        try:
            await self.state_store.delete(scope_key)
        except Exception as exc:
            logger.warning(
                "[sandbox] Failed to clear sandbox state: %s",
                exc,
                exc_info=exc,
            )
