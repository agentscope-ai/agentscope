# -*- coding: utf-8 -*-
"""Cancellation while opening a SOP run must roll back its sessions."""

import asyncio
from contextlib import AsyncExitStack
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app._service import SOPService, SessionService
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    AsyncSQLAlchemyStorage,
    SessionSettings,
    SOPAgentRef,
    SOPData,
    SOPRecord,
    SOPRunRecord,
    SOPStepDataV1,
)


class SOPCreateCancellationTest(IsolatedAsyncioTestCase):
    """Use real storage so an orphaned session is observable."""

    async def asyncSetUp(self) -> None:
        """Create an agent and a one-step stored procedure."""
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(InMemoryMessageBus())
        self.storage = await self._stack.enter_async_context(
            AsyncSQLAlchemyStorage(
                "sqlite+aiosqlite:///:memory:",
                create_tables=True,
            ),
        )
        self.user_id = "user-1"
        self.agent_id = "agent-1"
        await self.storage.upsert_agent(
            self.user_id,
            AgentRecord(
                id=self.agent_id,
                user_id=self.user_id,
                data=AgentData(
                    name="executor",
                    context_config=ContextConfig(),
                    react_config=ReActConfig(),
                ),
            ),
        )
        self.sop = SOPRecord(
            user_id=self.user_id,
            data=SOPData(
                name="procedure",
                steps=[
                    SOPStepDataV1(
                        subject="step",
                        description="perform work",
                        executor=SOPAgentRef(
                            agent_id=self.agent_id,
                            session_key="executor",
                        ),
                    ),
                ],
                session_settings={
                    "executor": SessionSettings(
                        chat_model_config={
                            "type": "dashscope",
                            "credential_id": "credential-1",
                            "model": "qwen-max",
                            "parameters": {},
                        },
                    ),
                },
            ),
        )
        await self.storage.upsert_sop(self.user_id, self.sop)

    async def asyncTearDown(self) -> None:
        """Dispose the in-memory storage and bus."""
        await self._stack.aclose()

    async def test_cancelled_creation_removes_unreachable_sessions(
        self,
    ) -> None:
        """The run record never commits, so its sessions must not remain."""
        service = SOPService(
            self.storage,
            MagicMock(),
            self.bus,
            None,
            SessionService(self.storage, self.bus),
        )
        persisting = asyncio.Event()
        run_ids: list[str] = []

        async def pause_before_run_persists(
            user_id: str,
            run: SOPRunRecord,
        ) -> None:
            self.assertEqual(user_id, self.user_id)
            run_ids.append(run.id)
            persisting.set()
            await asyncio.Event().wait()

        with patch.object(
            self.storage,
            "upsert_sop_run",
            side_effect=pause_before_run_persists,
        ):
            task = asyncio.create_task(
                service.create_run(self.user_id, self.sop.id),
            )
            try:
                await asyncio.wait_for(persisting.wait(), timeout=5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        self.assertIsNone(
            await self.storage.get_sop_run(self.user_id, run_ids[0]),
        )
        self.assertEqual(
            await self.storage.list_sessions(self.user_id, self.agent_id),
            [],
        )
