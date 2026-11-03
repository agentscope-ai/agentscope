# -*- coding: utf-8 -*-
"""Regression test for ``PATCH /sessions/{id}`` racing a chat run.

The handler reads the session record, validates the request (several
awaits), and then writes the record back. A chat run may start and
finish inside that window and persist state of its own; the handler's
opening snapshot would then overwrite it. The write therefore has to
happen under the session lock and build on a record re-read while the
lock is held.
"""

import asyncio
from unittest import IsolatedAsyncioTestCase

from agentscope.app._router._session import update_session
from agentscope.app._router._schema._session import UpdateSessionRequest
from agentscope.app.access import ResourceKind
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import (
    ChatModelConfig,
    SessionConfig,
    SessionRecord,
)
from agentscope.state import AgentState, Task


def _task(subject: str) -> Task:
    """Build a distinguishable task entry for the session state."""
    return Task(
        subject=subject,
        description="",
        metadata={},
        id=subject,
    )


class _Storage:
    """One mutable session record behind snapshot reads."""

    def __init__(self, session: SessionRecord) -> None:
        self.session = session

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """Return a detached snapshot when the triple matches."""
        if (
            user_id != self.session.user_id
            or agent_id != self.session.agent_id
            or session_id != self.session.id
        ):
            return None
        return self.session.model_copy(deep=True)

    async def upsert_session(
        self,
        user_id: str,
        agent_id: str,
        config: SessionConfig,
        state: AgentState | None = None,
        session_id: str | None = None,
        **_: object,
    ) -> SessionRecord:
        """Apply the handler's write to the stored record."""
        assert user_id == self.session.user_id
        assert agent_id == self.session.agent_id
        assert session_id == self.session.id
        self.session.config = config.model_copy(deep=True)
        if state is not None:
            self.session.state = state.model_copy(deep=True)
        return self.session.model_copy(deep=True)

    def persist_run_state(self) -> None:
        """Record the state a chat run would persist when it finishes."""
        self.session.state.tasks_context.tasks.append(
            _task("persisted by the run"),
        )


class _RacingAccess:
    """Start (and finish) a chat run during PATCH's validation awaits."""

    def __init__(self, bus: InMemoryMessageBus, storage: _Storage) -> None:
        self.bus = bus
        self.storage = storage
        self.ran = False

    async def get_resource(
        self,
        user_id: str,
        kind: ResourceKind,
        resource_id: str,
    ) -> object:
        """Simulate a run that starts, persists state, and releases."""
        del user_id, kind, resource_id
        if not self.ran:
            self.ran = True
            async with self.bus.acquire_lock(
                MessageBusKeys.session_lock(self.storage.session.id),
                ttl_secs=30,
            ):
                self.storage.persist_run_state()
        return object()


class SessionConfigPatchRaceTest(IsolatedAsyncioTestCase):
    """A run that lands mid-PATCH must keep the state it persisted."""

    async def test_run_state_persisted_during_validation_survives(
        self,
    ) -> None:
        """The handler must not write its stale snapshot over the run."""
        state = AgentState()
        state.tasks_context.tasks.append(_task("before"))
        session = SessionRecord(
            id="session-1",
            user_id="user-1",
            agent_id="agent-1",
            config=SessionConfig(workspace_id="workspace-1"),
            state=state,
        )
        storage = _Storage(session)
        bus = InMemoryMessageBus()
        access = _RacingAccess(bus, storage)

        await asyncio.wait_for(
            update_session(
                session_id=session.id,
                body=UpdateSessionRequest(
                    permission_mode="accept_edits",
                    chat_model_config=ChatModelConfig(
                        type="test",
                        credential_id="credential-1",
                        model="test-model",
                        parameters={},
                    ),
                ),
                agent_id=session.agent_id,
                user_id=session.user_id,
                storage=storage,
                access=access,
                message_bus=bus,
            ),
            timeout=5.0,
        )

        self.assertTrue(access.ran)
        self.assertEqual(
            [
                task.subject
                for task in storage.session.state.tasks_context.tasks
            ],
            ["before", "persisted by the run"],
        )
