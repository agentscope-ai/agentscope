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
import asyncio
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any
from unittest import TestCase
from unittest.mock import patch

import fakeredis.aioredis
from fastapi.testclient import TestClient

from agentscope.agent import ContextConfig, ReActConfig
from agentscope.app import create_app
from agentscope.app._bus_ops import session_execution
from agentscope.app.message_bus import MessageBusKeys, RedisMessageBus
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ASRModelConfig,
    RealtimeModelConfig,
    RedisStorage,
    VoiceProfileData,
    VoiceProfileRecord,
)
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.credential import OpenAICredential
from agentscope.permission import PermissionMode, PermissionRule
from agentscope.permission._evidence import ScheduleAuthorization
from agentscope.event import (
    ConfirmResult,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
)
from agentscope.message import ToolCallBlock, UserMsg
from agentscope.state import AgentState, Task
from agentscope.state._state import ReadCacheEntry

HEADERS = {"X-User-ID": "alice"}


class SessionConfigPatchTest(TestCase):
    """Exercise the PATCH endpoint against a fakeredis-backed app."""

    def setUp(self) -> None:
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
            _client = None

            async def __aenter__(self) -> Any:
                self._client = redis
                return self

            async def aclose(self) -> None:
                """Release the fixture client."""
                self._client = None

            async def upsert_session(self, *args: Any, **kwargs: Any) -> Any:
                """Capture each state write before persisting it."""
                recorded_states.append(kwargs.get("state"))
                return await super().upsert_session(*args, **kwargs)

        class _Bus(RedisMessageBus):
            _client = None

            async def __aenter__(self) -> Any:
                self._client = redis
                return self

            async def aclose(self) -> None:
                """Release the fixture client."""
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

        # The async Redis client belongs to the application event loop.
        self.portal = self.client.portal
        storage = app.state.storage
        self.agent_id = self.portal.call(
            storage.upsert_agent,
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
        record = self.portal.call(
            storage.get_session,
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
        self.portal.call(
            partial(
                storage.update_session_state,
                user_id="alice",
                agent_id=self.agent_id,
                session_id=self.session_id,
                state=record.state,
            ),
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

    def test_realtime_config_omission_and_explicit_null(self) -> None:
        """Omission keeps realtime config while null clears it."""
        record = self.portal.call(
            self.storage.get_session,
            "alice",
            self.agent_id,
            self.session_id,
        )
        realtime_config = RealtimeModelConfig(
            type="dashscope_omni_realtime",
            credential_id="credential-1",
            model="qwen3.5-omni-plus-realtime",
            parameters={"voice": "Tina"},
        )
        self.portal.call(
            partial(
                self.storage.upsert_session,
                user_id="alice",
                agent_id=self.agent_id,
                config=record.config.model_copy(
                    update={"realtime_model_config": realtime_config},
                ),
                session_id=self.session_id,
            ),
        )
        self.recorded_states.clear()

        omitted = self._patch({"name": "after"})
        cleared = self._patch({"realtime_model_config": None})

        self.assertDictEqual(
            {
                "omitted_status": omitted.status_code,
                "omitted_config": omitted.json()["config"][
                    "realtime_model_config"
                ],
                "cleared_status": cleared.status_code,
                "cleared_config": cleared.json()["config"][
                    "realtime_model_config"
                ],
                "written_states": self.recorded_states,
            },
            {
                "omitted_status": 200,
                "omitted_config": realtime_config.model_dump(mode="json"),
                "cleared_status": 200,
                "cleared_config": None,
                "written_states": [None, None],
            },
        )

    def test_asr_config_omission_and_explicit_null(self) -> None:
        """Omission keeps ASR config while null disables speech input."""
        record = self.portal.call(
            self.storage.get_session,
            "alice",
            self.agent_id,
            self.session_id,
        )
        asr_config = ASRModelConfig(
            type="dashscope_credential",
            credential_id="credential-1",
            model="qwen-audio-3.1-asr-flash",
            parameters={"language_hints": ["zh"]},
        )
        self.portal.call(
            partial(
                self.storage.upsert_session,
                user_id="alice",
                agent_id=self.agent_id,
                config=record.config.model_copy(
                    update={"asr_model_config": asr_config},
                ),
                session_id=self.session_id,
            ),
        )
        self.recorded_states.clear()

        omitted = self._patch({"name": "after"})
        cleared = self._patch({"asr_model_config": None})

        self.assertDictEqual(
            {
                "omitted_status": omitted.status_code,
                "omitted_config": omitted.json()["config"]["asr_model_config"],
                "cleared_status": cleared.status_code,
                "cleared_config": cleared.json()["config"]["asr_model_config"],
                "written_states": self.recorded_states,
            },
            {
                "omitted_status": 200,
                "omitted_config": asr_config.model_dump(mode="json"),
                "cleared_status": 200,
                "cleared_config": None,
                "written_states": [None, None],
            },
        )

    def test_voice_profile_and_realtime_configs_coexist(self) -> None:
        """Create and update keep both voice configs and validate ownership."""
        credential_id = self.portal.call(
            self.storage.upsert_credential,
            "alice",
            OpenAICredential(api_key="test-key"),
        )
        profile_id = self.portal.call(
            self.storage.upsert_voice_profile,
            "alice",
            VoiceProfileRecord(
                user_id="alice",
                data=VoiceProfileData(
                    name="Custom voice",
                    engine="openai_tts",
                    credential_id=credential_id,
                    model="gpt-4o-mini-tts",
                    voice="voice-custom",
                ),
            ),
        )
        configs = {
            "tts_model_config": {
                "type": "openai_credential",
                "credential_id": credential_id,
                "model": "gpt-4o-mini-tts",
                "voice_profile_id": profile_id,
                "parameters": {"voice": "voice-custom"},
            },
            "realtime_model_config": {
                "type": "openai_realtime",
                "credential_id": credential_id,
                "model": "gpt-realtime",
                "parameters": {},
            },
        }
        for operation in ("create", "update"):
            with self.subTest(operation=operation):
                if operation == "create":
                    response = self.client.post(
                        "/sessions/",
                        headers=HEADERS,
                        json={"agent_id": self.agent_id, **configs},
                    )
                    self.assertEqual(response.status_code, 201)
                    session_id = response.json()["session_id"]
                else:
                    response = self._patch(configs)
                    self.assertEqual(response.status_code, 200)
                    session_id = self.session_id
                record = self.portal.call(
                    self.storage.get_session,
                    "alice",
                    self.agent_id,
                    session_id,
                )
                self.assertEqual(
                    {
                        key: getattr(record.config, key).model_dump()
                        for key in configs
                    },
                    configs,
                )

    def test_permission_mode_patch_writes_only_that_field(self) -> None:
        """``permission_mode`` is the one field that must carry state."""
        response = self._patch({"permission_mode": "ask"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.recorded_states), 1)
        written = self.recorded_states[0]
        self.assertIsNotNone(written)
        self.assertEqual(
            written.permission_context.mode,
            PermissionMode.ASK,
        )
        # Everything else in the state rides along untouched.
        self.assertEqual(
            [task.subject for task in written.tasks_context.tasks],
            ["written by the run"],
        )

    def test_permission_patch_panel_reads_inherited_policy(self) -> None:
        """PATCH and list expose the same effective policy and public state."""
        args = ("alice", self.agent_id, self.session_id)
        record = self.portal.call(self.storage.get_session, *args)
        parent = AgentState()
        rule = PermissionRule(
            tool_name="Deploy",
            rule_content=None,
            behavior="deny",
            source="user",
        )
        parent.permission_context.deny_rules = {"Deploy": [rule]}
        parent.permission_context.read_only = True
        parent.retain_authorization([UserMsg("user", "member-private")])
        record.state.inherit_member_scope(parent)
        record.state.retain_authorization([UserMsg("user", "archive-private")])
        record.state.schedule_authorization = ScheduleAuthorization(
            schedule_id="s",
            user_id="alice",
            source="USER",
            description="schedule-private",
            evidence=parent.authorization_evidence,
        )
        self.portal.call(
            self.storage.update_session_state,
            *args,
            record.state,
        )
        response = self._patch({"permission_mode": "auto"})
        self.assertEqual(response.status_code, 200)
        for marker in (
            "member-private",
            "archive-private",
            "schedule-private",
        ):
            self.assertNotIn(marker, response.text)
        self.assertEqual(response.json(), self._listed()["session"])
        record.state.permission_context.mode = PermissionMode.AUTO
        self.assertEqual(
            response.json()["state"],
            {
                "permission_context": (
                    record.state.effective_permission_context.model_dump(
                        mode="json",
                    )
                ),
                "tasks_context": record.state.tasks_context.model_dump(),
            },
        )

    def test_missing_confirmation_id_returns_fixable_http_error(self) -> None:
        """Reject a malformed receipt before enqueueing or ending the reply."""
        args = ("alice", self.agent_id, self.session_id)
        record = self.portal.call(self.storage.get_session, *args)
        call = ToolCallBlock(
            id="tool",
            name="edit",
            input="{}",
            state="asking",
        )
        record.state.append_context("ann", [call])
        msg = record.state.get_reply_message("ann")
        card = RequireUserConfirmEvent.issue(msg.id, call)
        msg.set_confirmation_request(call.id, card.id)
        self.portal.call(
            self.storage.update_session_state,
            *args,
            record.state,
        )
        event = UserConfirmResultEvent(
            reply_id=msg.id,
            confirm_results=[
                ConfirmResult(confirmed=True, tool_call=call),
            ],
        )
        response = self.client.post(
            "/chat/",
            headers=HEADERS,
            json={
                "agent_id": self.agent_id,
                "session_id": self.session_id,
                "input": event.model_dump(mode="json"),
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("RequireUserConfirmEvent.id", response.json()["detail"])
        self.assertEqual(
            self.portal.call(self.storage.get_session, *args).state,
            record.state,
        )

    def test_patch_rejected_while_the_session_runs(self) -> None:
        """Holding the run lock makes configuration read-only."""
        lock_key = MessageBusKeys.session_lock(self.session_id)
        with self.portal.wrap_async_context_manager(
            self.bus.acquire_lock(lock_key, ttl_secs=30),
        ):
            response = self._patch({"name": "after"})

        self.assertEqual(response.status_code, 409)
        self.assertIn("running", response.json()["detail"])
        # Nothing reached storage, so the rejection is real and not just
        # a status code applied after the fact.
        self.assertEqual(self.recorded_states, [])

    def test_missing_session_reports_404_not_409(self) -> None:
        """A missing session reports as missing even while locked.

        The lock key is derived from the requested id, so a caller that
        typos a session id would otherwise get a confusing 409 for a
        session that never existed.
        """
        lock_key = MessageBusKeys.session_lock("ghost")
        with self.portal.wrap_async_context_manager(
            self.bus.acquire_lock(lock_key, ttl_secs=30),
        ):
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
        args = ("alice", self.agent_id, self.session_id)
        record = self.portal.call(self.storage.get_session, *args)
        parent = AgentState()
        parent.permission_context.read_only = True
        parent.retain_authorization([UserMsg("user", "member-private")])
        record.state.retain_authorization([UserMsg("user", "archive-private")])
        record.state.inherit_member_scope(parent)
        record.state.schedule_authorization = ScheduleAuthorization(
            schedule_id="s",
            user_id="alice",
            source="USER",
            description="schedule-private",
            evidence=parent.authorization_evidence,
        )
        self.portal.call(
            self.storage.update_session_state,
            *args,
            record.state,
        )
        response = self.client.get(
            "/sessions/",
            headers=HEADERS,
            params={"agent_id": self.agent_id},
        )
        self.assertEqual(response.status_code, 200)
        for marker in (
            "member-private",
            "archive-private",
            "schedule-private",
        ):
            self.assertNotIn(marker, response.text)
        listed = response.json()["sessions"][0]["session"]
        state = listed.pop("state")
        persisted = self.portal.call(self.storage.get_session, *args)
        self.assertEqual(
            listed,
            persisted.model_dump(
                mode="json",
                include={
                    "id",
                    "created_at",
                    "updated_at",
                    "user_id",
                    "agent_id",
                    "origin",
                    "team_id",
                    "config",
                },
            ),
        )
        self.assertEqual(set(state), {"permission_context", "tasks_context"})
        self.assertTrue(state["permission_context"]["read_only"])
        self.assertEqual(
            persisted.state,
            record.state,
        )
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

    def _running_status(self) -> tuple:
        """Read every HTTP projection that controls the chat UI."""

        def get(suffix: str) -> dict:
            response = self.client.get(
                f"/sessions/{self.session_id}/{suffix}",
                headers=HEADERS,
                params={"agent_id": self.agent_id},
            )
            self.assertEqual(response.status_code, 200)
            return response.json()

        listed = self._listed()
        return (
            listed["status"],
            listed["is_running"],
            get("status")["status"],
            get("messages")["is_running"],
        )

    def test_running_session_reports_running(self) -> None:
        """Chat and realtime share the same execution lease."""
        with self.portal.wrap_async_context_manager(
            session_execution(self.bus, self.session_id),
        ):
            self.assertEqual(
                self._running_status(),
                ("running", True, "running", True),
            )

    def test_all_status_reads_stay_idle_during_rename(self) -> None:
        """A second client opening chat during a PATCH sees idle."""
        entered, release = asyncio.Event(), asyncio.Event()
        save = self.storage.upsert_session

        async def wait_before_save(*args: Any, **kwargs: Any) -> Any:
            entered.set()
            await release.wait()
            return await save(*args, **kwargs)

        with patch.object(
            self.storage,
            "upsert_session",
            wait_before_save,
        ), ThreadPoolExecutor(1) as pool:
            pending = pool.submit(self._patch, {"name": "renamed"})
            try:
                self.portal.call(asyncio.wait_for, entered.wait(), 3)
                self.assertEqual(
                    self._running_status(),
                    ("idle", False, "idle", False),
                )
            finally:
                self.portal.call(release.set)
            self.assertEqual(pending.result(timeout=3).status_code, 200)

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

    def test_name_null_is_refused_rather_than_crashing(self) -> None:
        """Reject a null name with 422 and preserve the stored name."""
        name = self.client.get(
            "/sessions/",
            headers=HEADERS,
            params={"agent_id": self.agent_id},
        ).json()["sessions"][0]["session"]["config"]["name"]

        response = self._patch({"name": None})

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            [
                (error["type"], error["loc"])
                for error in response.json()["detail"]
            ],
            [("string_type", ["name"])],
        )
        self.assertEqual(
            self.client.get(
                "/sessions/",
                headers=HEADERS,
                params={"agent_id": self.agent_id},
            ).json()["sessions"][0]["session"]["config"]["name"],
            name,
        )

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
