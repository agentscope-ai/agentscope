# -*- coding: utf-8 -*-
"""Tests for :class:`SOPService` and the step that drives it.

The chat service is replaced by a script: each entry says what the
session it is handed does with its turn, written through the same
submit tools a real agent would call. What is under test is the
service's own reasoning — which session gets the turn, what it reads
back, and where the run stops.
"""
from contextlib import AsyncExitStack
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.app._service import SOPService
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app._tool import SubmitHandover, SubmitVerdict
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    AgentVerifier,
    AsyncSQLAlchemyStorage,
    HumanVerifier,
    SessionSettings,
    SOPAgentRef,
    SOPData,
    SOPRecord,
    SOPStepDataV1,
)
from agentscope.agent import ContextConfig, ReActConfig
from agentscope.message import UserMsg
from agentscope.sop import SOPPhase


class _Workspaces:
    """A workspace manager that only has to mint ids."""

    async def assign_workspace_id(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> str:
        """Mint one id per session id, so grains are distinguishable."""
        _ = user_id, agent_id
        return f"ws-{session_id}"


class _ScriptedChat:
    """A chat service that replays one scripted turn per call.

    A turn writes through the same submit tool a real agent would call,
    and finds its step the way ``get_toolkit`` does — the one the stored
    run says is running.
    """

    def __init__(self, storage: Any, script: list[Any]) -> None:
        """Remember the script, and what it gets asked."""
        self._storage = storage
        self.script = list(script)
        self.asked: list[tuple[str, Any]] = []
        self.sop_run_id = ""

    async def run(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg: Any = None,
    ) -> None:
        """Take one turn, doing whatever the script says it does."""
        _ = agent_id
        self.asked.append((session_id, input_msg))
        action = self.script.pop(0)
        if action is None:
            return

        record = await self._storage.get_sop_run(user_id, self.sop_run_id)
        index = next(
            i
            for i, step in enumerate(record.state.steps)
            if step.phase is SOPPhase.RUNNING
        )
        tool_cls, kwargs = action
        tool = tool_cls(
            storage=self._storage,
            user_id=user_id,
            sop_run_id=self.sop_run_id,
            step_index=index,
            **({"verifier": "ve"} if tool_cls is SubmitVerdict else {}),
        )
        await tool(**kwargs)


def _sop(user_id: str, verifier: Any, keys: tuple[str, str]) -> SOPRecord:
    """A one-step procedure with the given verifier and session keys."""
    return SOPRecord(
        user_id=user_id,
        data=SOPData(
            name="ship",
            steps=[
                SOPStepDataV1(
                    subject="model",
                    description="make the hull",
                    executor=SOPAgentRef(agent_id="a-1", session_key=keys[0]),
                    verifier=verifier,
                ),
            ],
            session_settings={
                key: SessionSettings(
                    chat_model_config={
                        "type": "dashscope",
                        "credential_id": "c-1",
                        "model": "qwen-max",
                        "parameters": {},
                    },
                )
                for key in set(keys)
            },
        ),
    )


class SOPServiceTest(IsolatedAsyncioTestCase):
    """End-to-end tests over in-memory SQLite."""

    async def asyncSetUp(self) -> None:
        """Open storage and register the agents a procedure refers to."""
        self._stack = AsyncExitStack()
        self.bus = await self._stack.enter_async_context(
            InMemoryMessageBus(),
        )
        self.storage = await self._stack.enter_async_context(
            AsyncSQLAlchemyStorage(
                "sqlite+aiosqlite:///:memory:",
                create_tables=True,
            ),
        )
        for agent_id, name in (("a-1", "ex"), ("a-2", "ve")):
            await self.storage.upsert_agent(
                "user-1",
                AgentRecord(
                    id=agent_id,
                    user_id="user-1",
                    data=AgentData(
                        name=name,
                        context_config=ContextConfig(),
                        react_config=ReActConfig(),
                    ),
                ),
            )

    async def asyncTearDown(self) -> None:
        """Close storage."""
        await self._stack.aclose()

    async def _drive(self, chat: _ScriptedChat, run_id: str) -> None:
        """Run the service with the scripted chat bound to this run."""
        chat.sop_run_id = run_id
        await SOPService(self.storage, _Workspaces(), self.bus, chat).run(
            "user-1",
            run_id,
        )

    async def test_a_run_walks_its_step_and_finishes(self) -> None:
        """Work, then judgement, then the run is done."""
        sop = _sop(
            "user-1",
            AgentVerifier(
                agent=SOPAgentRef(agent_id="a-2", session_key="reviewer"),
            ),
            ("modeller", "reviewer"),
        )
        await self.storage.upsert_sop("user-1", sop)
        service = SOPService(self.storage, _Workspaces(), self.bus, None)
        run = await service.create_run(
            "user-1",
            sop,
            [UserMsg(name="user", content="go")],
        )

        chat = _ScriptedChat(
            self.storage,
            [
                (SubmitHandover, {"handover": "a hull"}),
                (SubmitVerdict, {"passed": True}),
            ],
        )
        await self._drive(chat, run.id)

        stored = await self.storage.get_sop_run("user-1", run.id)
        self.assertEqual(stored.state.phase, SOPPhase.COMPLETED)
        self.maxDiff = None
        self.assertListEqual(
            [_.model_dump(mode="json") for _ in stored.state.steps],
            [
                {
                    "phase": "completed",
                    "given": [
                        {
                            "name": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "go",
                                    "id": AnyString(),
                                    "created_at": AnyString(),
                                    "finished_at": None,
                                },
                            ],
                            "role": "user",
                            "id": AnyString(),
                            "metadata": {},
                            "created_at": AnyString(),
                            "usage": None,
                            "finished_at": AnyString(),
                            "finished_reason": None,
                            "structured_output": None,
                            "error": None,
                        },
                    ],
                    "submission": [
                        {
                            "type": "text",
                            "text": "a hull",
                            "id": AnyString(),
                            "created_at": AnyString(),
                            "finished_at": None,
                        },
                    ],
                    "verifications": [
                        {
                            "passed": True,
                            "message": "",
                            "verifier": "ve",
                            "created_at": AnyString(),
                        },
                    ],
                },
            ],
        )
        # The work went to one session and the judging to the other.
        self.assertListEqual(
            [session for session, _ in chat.asked],
            [run.sessions["modeller"], run.sessions["reviewer"]],
        )

    async def test_a_refusal_sends_the_step_back(self) -> None:
        """A refused attempt is retried, and the critique comes with it."""
        sop = _sop(
            "user-1",
            AgentVerifier(
                agent=SOPAgentRef(agent_id="a-2", session_key="reviewer"),
            ),
            ("modeller", "reviewer"),
        )
        await self.storage.upsert_sop("user-1", sop)
        service = SOPService(self.storage, _Workspaces(), self.bus, None)
        run = await service.create_run("user-1", sop)

        chat = _ScriptedChat(
            self.storage,
            [
                (SubmitHandover, {"handover": "a leaky hull"}),
                (SubmitVerdict, {"passed": False, "message": "panel 3 leaks"}),
                (SubmitHandover, {"handover": "a sealed hull"}),
                (SubmitVerdict, {"passed": True}),
            ],
        )
        await self._drive(chat, run.id)

        stored = await self.storage.get_sop_run("user-1", run.id)
        self.assertEqual(stored.state.phase, SOPPhase.COMPLETED)
        self.assertEqual(len(stored.state.steps[0].verifications), 2)
        self.assertIn("panel 3 leaks", str(chat.asked[2][1]))

    async def test_the_run_gives_up_once_the_attempts_do(self) -> None:
        """Refusals are spent from the step's budget, not forgiven."""
        sop = _sop(
            "user-1",
            AgentVerifier(
                agent=SOPAgentRef(agent_id="a-2", session_key="reviewer"),
            ),
            ("modeller", "reviewer"),
        )
        sop.data.steps[0].max_attempts = 2
        await self.storage.upsert_sop("user-1", sop)
        service = SOPService(self.storage, _Workspaces(), self.bus, None)
        run = await service.create_run("user-1", sop)

        chat = _ScriptedChat(
            self.storage,
            [
                (SubmitHandover, {"handover": "draft"}),
                (SubmitVerdict, {"passed": False, "message": "no"}),
            ]
            * 2,
        )
        await self._drive(chat, run.id)

        stored = await self.storage.get_sop_run("user-1", run.id)
        self.assertEqual(stored.state.phase, SOPPhase.FAILED)

    async def test_a_turn_that_submits_nothing_costs_an_attempt(self) -> None:
        """An agent that just stops has produced nothing to go on."""
        sop = _sop("user-1", None, ("modeller", "modeller"))
        sop.data.steps[0].max_attempts = 1
        await self.storage.upsert_sop("user-1", sop)
        service = SOPService(self.storage, _Workspaces(), self.bus, None)
        run = await service.create_run("user-1", sop)

        await self._drive(_ScriptedChat(self.storage, [None]), run.id)

        stored = await self.storage.get_sop_run("user-1", run.id)
        self.assertEqual(stored.state.phase, SOPPhase.FAILED)
        self.assertListEqual(
            [
                _.model_dump(mode="json")
                for _ in stored.state.steps[0].verifications
            ],
            [
                {
                    "passed": False,
                    "message": (
                        "Your turn ended without submitting anything, so "
                        "the step has nothing to show for it."
                    ),
                    "verifier": "sop",
                    "created_at": AnyString(),
                },
            ],
        )

    async def test_a_human_verifier_stops_the_run_until_someone_answers(
        self,
    ) -> None:
        """The verdict is a write, so nothing is dispatched to wait on it."""
        sop = _sop(
            "user-1",
            HumanVerifier(question="Watertight?"),
            ("modeller", "modeller"),
        )
        await self.storage.upsert_sop("user-1", sop)
        service = SOPService(self.storage, _Workspaces(), self.bus, None)
        run = await service.create_run("user-1", sop)

        chat = _ScriptedChat(
            self.storage,
            [(SubmitHandover, {"handover": "a hull"})],
        )
        await self._drive(chat, run.id)

        parked = await self.storage.get_sop_run("user-1", run.id)
        self.assertEqual(parked.state.phase, SOPPhase.AWAITING)
        self.assertEqual(len(chat.asked), 1)

        # Whoever is asked files the verdict through the same tool an
        # agent reviewer would, and the run picks up from that.
        await SubmitVerdict(
            storage=self.storage,
            user_id="user-1",
            sop_run_id=run.id,
            step_index=0,
            verifier="user-1",
        )(passed=True)

        await self._drive(_ScriptedChat(self.storage, []), run.id)

        self.assertEqual(
            (await self.storage.get_sop_run("user-1", run.id)).state.phase,
            SOPPhase.COMPLETED,
        )

    async def test_deleting_a_run_takes_its_conversations(self) -> None:
        """A session nobody opened is still wakeable, so none is left."""
        sop = _sop("user-1", None, ("modeller", "modeller"))
        await self.storage.upsert_sop("user-1", sop)
        service = SOPService(self.storage, _Workspaces(), self.bus, None)
        run = await service.create_run("user-1", sop)
        session_id = run.sessions["modeller"]

        self.assertTrue(await self.storage.delete_sop_run("user-1", run.id))

        self.assertIsNone(
            await self.storage.get_session("user-1", "a-1", session_id),
        )
        self.assertEqual(await self.storage.list_sessions("user-1", "a-1"), [])

    async def test_deleting_a_procedure_takes_every_run_conversation(
        self,
    ) -> None:
        """The same cascade, one level up."""
        sop = _sop("user-1", None, ("modeller", "modeller"))
        await self.storage.upsert_sop("user-1", sop)
        service = SOPService(self.storage, _Workspaces(), self.bus, None)
        first = await service.create_run("user-1", sop)
        second = await service.create_run("user-1", sop)

        self.assertTrue(await self.storage.delete_sop("user-1", sop.id))

        for run in (first, second):
            self.assertIsNone(
                await self.storage.get_session(
                    "user-1",
                    "a-1",
                    run.sessions["modeller"],
                ),
            )
        self.assertEqual(await self.storage.list_sop_runs("user-1"), [])

    async def test_one_conversation_per_session_key(self) -> None:
        """A key named twice is one session; two keys are two."""
        sop = _sop("user-1", None, ("modeller", "modeller"))
        sop.data.steps.append(
            SOPStepDataV1(
                subject="paint",
                description="give it a livery",
                executor=SOPAgentRef(agent_id="a-1", session_key="modeller"),
            ),
        )
        await self.storage.upsert_sop("user-1", sop)

        service = SOPService(self.storage, _Workspaces(), self.bus, None)
        run = await service.create_run("user-1", sop)

        self.assertEqual(list(run.sessions), ["modeller"])
        session = await self.storage.get_session(
            "user-1",
            "a-1",
            run.sessions["modeller"],
        )
        self.assertEqual(
            session.origin.model_dump(),
            {
                "type": "sop",
                "sop_run_id": run.id,
                "session_key": "modeller",
            },
        )
