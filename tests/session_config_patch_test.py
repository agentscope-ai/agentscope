# -*- coding: utf-8 -*-
"""``PATCH /sessions/{id}`` test case — write isolation and cwd storage.

The endpoint has to keep two writers apart. Configuration is written by
this handler; ``AgentState`` is written by the chat run's ``_persist()``,
which completes while the run still holds the session lock. Two rules
follow, and both are asserted here:

- a config change is refused while the lock is held, and
- a config change that does not carry ``permission_mode`` must not send
  ``state`` to storage at all, so the handler's opening snapshot can
  never land on top of what the run has persisted since.
"""
import tempfile
from typing import Any
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis
from fastapi.testclient import TestClient

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app import create_app
from agentscope.app.message_bus import MessageBusKeys, RedisMessageBus
from agentscope.app.storage import AgentData, AgentRecord, RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.permission import PermissionMode
from agentscope.message import UserMsg
from agentscope.state import Task
from agentscope.state._state import ReadCacheEntry

HEADERS = {"X-User-ID": "alice"}


class SessionConfigPatchTest(IsolatedAsyncioTestCase):
    """Exercise the PATCH endpoint against a fakeredis-backed app."""

    async def asyncSetUp(self) -> None:
        """Start an app and seed one agent with one session."""
        # enterContext binds the context manager to the test's lifetime;
        # pylint does not recognise the unittest-native helper.
        # pylint: disable=consider-using-with
        workdir = self.enterContext(tempfile.TemporaryDirectory())
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

        # Record every ``state`` argument storage receives, so a test can
        # assert on what the handler chose to write rather than on what
        # survived the round trip.
        recorded_states: list[Any] = []

        class _Storage(RedisStorage):
            async def __aenter__(self) -> Any:
                self._client = redis
                return self

            async def aclose(self) -> None:
                self._client = None

            async def upsert_session(self, *args: Any, **kwargs: Any) -> Any:
                recorded_states.append(kwargs.get("state"))
                return await super().upsert_session(*args, **kwargs)

        class _Bus(RedisMessageBus):
            async def __aenter__(self) -> Any:
                self._client = redis
                return self

            async def aclose(self) -> None:
                self._client = None

        self.recorded_states = recorded_states
        self.bus = _Bus()
        app = create_app(
            storage=_Storage(),
            message_bus=self.bus,
            workspace_manager=LocalWorkspaceManager(workdir),
            enable_index_worker=False,
        )
        self.client = self.enterContext(TestClient(app))

        storage = app.state.storage
        self.agent_id = await storage.upsert_agent(
            "alice",
            AgentRecord(
                user_id="alice",
                data=AgentData(
                    name="ann",
                    system_prompt="You are ann.",
                    context_config=ContextConfig(),
                    react_config=ReActConfig(),
                ),
            ),
        )
        created = self.client.post(
            "/sessions/",
            headers=HEADERS,
            json={"agent_id": self.agent_id, "name": "before"},
        )
        self.assertEqual(created.status_code, 201)
        self.session_id = created.json()["session_id"]

        # Give the stored state something distinguishable, so a
        # clobbering write is visible rather than merely theoretical.
        self.storage = storage
        record = await storage.get_session(
            "alice",
            self.agent_id,
            self.session_id,
        )
        record.state.tasks_context.tasks = [
            Task(
                subject="written by the run",
                description="",
                metadata={},
                id="1",
            ),
        ]
        # The heavy fields, so trimming them is observable rather than
        # vacuously true against a freshly created session.
        record.state.context = [
            UserMsg(name="alice", content="a long conversation"),
        ]
        record.state.summary = "a compressed history"
        record.state.tool_context.read_file_cache = [
            ReadCacheEntry(
                lines=["file contents"],
                updated_at=0.0,
                bytes=13,
                file_path="/w/a.py",
            ),
        ]
        await storage.update_session_state(
            user_id="alice",
            agent_id=self.agent_id,
            session_id=self.session_id,
            state=record.state,
        )
        self.recorded_states.clear()

    def _patch(self, body: dict) -> Any:
        """Send a PATCH for the seeded session."""
        return self.client.patch(
            f"/sessions/{self.session_id}",
            headers=HEADERS,
            params={"agent_id": self.agent_id},
            json=body,
        )

    def test_config_only_patch_does_not_write_state(self) -> None:
        """A rename leaves ``state`` entirely out of the storage call."""
        response = self._patch({"name": "after"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["config"]["name"], "after")
        # The load-bearing assertion: not merely "state came back
        # unchanged" (it would, since upsert re-reads) but "the handler
        # never offered a state to write".
        self.assertEqual(self.recorded_states, [None])

    def test_permission_mode_patch_writes_only_that_field(self) -> None:
        """``permission_mode`` is the one field that must carry state."""
        response = self._patch({"permission_mode": "accept_edits"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.recorded_states), 1)
        written = self.recorded_states[0]
        self.assertIsNotNone(written)
        self.assertEqual(
            written.permission_context.mode,
            PermissionMode.ACCEPT_EDITS,
        )
        # Everything else in the state rides along untouched.
        self.assertEqual(
            [task.subject for task in written.tasks_context.tasks],
            ["written by the run"],
        )

    async def test_patch_rejected_while_the_session_runs(self) -> None:
        """Holding the run lock makes configuration read-only."""
        lock_key = MessageBusKeys.session_lock(self.session_id)
        async with self.bus.acquire_lock(lock_key, ttl_secs=30):
            response = self._patch({"name": "after"})

        self.assertEqual(response.status_code, 409)
        self.assertIn("running", response.json()["detail"])
        # Nothing reached storage, so the rejection is real and not just
        # a status code applied after the fact.
        self.assertEqual(self.recorded_states, [])

    async def test_missing_session_reports_404_not_409(self) -> None:
        """A missing session reports as missing even while locked.

        The lock key is derived from the requested id, so a caller that
        typos a session id would otherwise get a confusing 409 for a
        session that never existed.
        """
        lock_key = MessageBusKeys.session_lock("ghost")
        async with self.bus.acquire_lock(lock_key, ttl_secs=30):
            response = self.client.patch(
                "/sessions/ghost",
                headers=HEADERS,
                params={"agent_id": self.agent_id},
                json={"name": "after"},
            )

        self.assertEqual(response.status_code, 404)

    def test_listing_strips_the_bulk_of_state(self) -> None:
        """The list ships panel seeds, never the conversation.

        ``context`` and ``tool_context`` hold the model's transcript and
        the contents of every file it has read, so returning them would
        make listing twenty sessions cost twenty transcripts to render a
        sidebar that shows a name and a date.
        """
        listed = self.client.get(
            "/sessions/",
            headers=HEADERS,
            params={"agent_id": self.agent_id},
        ).json()["sessions"][0]

        state = listed["session"]["state"]
        self.assertEqual(state["context"], [])
        self.assertEqual(state["summary"], "")
        self.assertEqual(state["tool_context"]["read_file_cache"], [])
        # The two the UI actually seeds from must survive the trim.
        self.assertEqual(
            [task["subject"] for task in state["tasks_context"]["tasks"]],
            ["written by the run"],
        )
        self.assertIn("mode", state["permission_context"])

    def _listed(self) -> Any:
        """Fetch the seeded session's entry from the list endpoint."""
        return self.client.get(
            "/sessions/",
            headers=HEADERS,
            params={"agent_id": self.agent_id},
        ).json()["sessions"][0]

    def test_idle_session_reports_idle(self) -> None:
        """A session nobody is running reports ``idle``."""
        listed = self._listed()

        self.assertEqual(listed["status"], "idle")
        self.assertFalse(listed["is_running"])

    async def test_running_session_reports_running(self) -> None:
        """Holding the run lease is what makes a session ``running``."""
        lock_key = MessageBusKeys.session_lock(self.session_id)
        async with self.bus.acquire_lock(lock_key, ttl_secs=30):
            listed = self._listed()

        self.assertEqual(listed["status"], "running")
        self.assertTrue(listed["is_running"])

    def test_cwd_round_trips(self) -> None:
        """A relative cwd is stored and read back verbatim."""
        self.assertEqual(
            self._patch({"cwd": "src/agentscope"}).status_code,
            200,
        )

        listed = self.client.get(
            "/sessions/",
            headers=HEADERS,
            params={"agent_id": self.agent_id},
        ).json()
        self.assertEqual(
            listed["sessions"][0]["session"]["config"]["cwd"],
            "src/agentscope",
        )

    def test_cwd_defaults_to_none_and_clears_to_none(self) -> None:
        """``None`` means the workspace root, both initially and after."""
        record = self.client.get(
            "/sessions/",
            headers=HEADERS,
            params={"agent_id": self.agent_id},
        ).json()["sessions"][0]["session"]
        self.assertIsNone(record["config"]["cwd"])

        self._patch({"cwd": "src"})
        self.assertEqual(self._patch({"cwd": None}).status_code, 200)

        listed = self.client.get(
            "/sessions/",
            headers=HEADERS,
            params={"agent_id": self.agent_id},
        ).json()
        self.assertIsNone(listed["sessions"][0]["session"]["config"]["cwd"])

    def test_cwd_is_not_confined_to_the_workspace_root(self) -> None:
        """Absolute paths and ``..`` are ordinary values, not attacks.

        ``cwd`` only names a place to look — it never changes where a
        tool executes — and the directory listing it feeds is itself
        unconfined. Rejecting these would stop a user from pointing the
        UI at a checkout that lives outside the workspace.
        """
        for outside in ("/etc", "..", "../elsewhere", "a/../../elsewhere"):
            with self.subTest(cwd=outside):
                response = self._patch({"cwd": outside})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["config"]["cwd"], outside)
