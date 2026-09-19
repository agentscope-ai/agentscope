# -*- coding: utf-8 -*-
"""``AgentData.chat_config`` grouping test case.

The text-mode settings moved from the top level of ``AgentData`` into a
``chat_config`` block, so a realtime voice mode can add its own block
beside them. Nothing migrates the stored rows and nothing rewrites the
request bodies clients already send, so both the old and the new shape
have to land in the same place — that is what these cases pin down.
"""
from typing import Any
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis
from fastapi.testclient import TestClient

from agentscope.app import create_app
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import AgentData, AgentRecord, RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager

HEADERS = {"X-User-ID": "alice"}


class AgentConfigGroupingTest(IsolatedAsyncioTestCase):
    """Reading, creating and updating agents across both shapes."""

    async def asyncSetUp(self) -> None:
        """Start an app backed by fakeredis."""
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

        class _Storage(RedisStorage):
            async def __aenter__(self) -> Any:
                self._client = redis
                return self

            async def aclose(self) -> None:
                self._client = None

        class _Bus(RedisMessageBus):
            async def __aenter__(self) -> Any:
                self._client = redis
                return self

            async def aclose(self) -> None:
                self._client = None

        app = create_app(
            storage=_Storage(),
            message_bus=_Bus(),
            workspace_manager=LocalWorkspaceManager("/tmp"),
            enable_index_worker=False,
        )
        # pylint: disable=consider-using-with
        self.client = self.enterContext(TestClient(app))
        self.storage = app.state.storage

    def test_legacy_record_folds_into_chat_config(self) -> None:
        """An agent written before the grouping reads back grouped."""
        data = AgentData.model_validate(
            {
                "id": "a1",
                "name": "ann",
                "system_prompt": "You are ann.",
                "context_config": {"max_image_num": 3},
                "react_config": {"max_iters": 7},
                "invite_config": {
                    "invitable": True,
                    "invite_description": "an old agent",
                },
            },
        )

        self.assertListEqual(
            sorted(data.model_dump()),
            ["chat_config", "id", "name", "system_prompt"],
        )
        self.assertListEqual(
            sorted(data.model_dump()["chat_config"]),
            ["context_config", "invite_config", "react_config"],
        )
        self.assertEqual(data.chat_config.context_config.max_image_num, 3)
        self.assertEqual(data.chat_config.react_config.max_iters, 7)
        self.assertEqual(
            data.chat_config.invite_config.invite_description,
            "an old agent",
        )

    def test_create_accepts_both_shapes(self) -> None:
        """POST bodies in either shape store the same grouped record."""
        nested = self.client.post(
            "/agent/",
            headers=HEADERS,
            json={
                "name": "nested",
                "chat_config": {"react_config": {"max_iters": 5}},
            },
        )
        legacy = self.client.post(
            "/agent/",
            headers=HEADERS,
            json={"name": "legacy", "react_config": {"max_iters": 5}},
        )
        self.assertEqual(nested.status_code, 201)
        self.assertEqual(legacy.status_code, 201)

        agents = self.client.get("/agent/", headers=HEADERS).json()["agents"]
        by_name = {a["data"]["name"]: a["data"] for a in agents}
        self.assertListEqual(
            sorted(by_name["nested"]["chat_config"]),
            ["context_config", "invite_config", "react_config"],
        )
        self.assertEqual(
            by_name["nested"]["chat_config"]["react_config"]["max_iters"],
            5,
        )
        self.assertEqual(
            by_name["legacy"]["chat_config"]["react_config"]["max_iters"],
            5,
        )

    def test_update_keeps_untouched_sub_configs(self) -> None:
        """A legacy PATCH replaces its own sub-config and nothing else."""
        agent_id = self.client.post(
            "/agent/",
            headers=HEADERS,
            json={
                "name": "ann",
                "chat_config": {
                    "react_config": {"max_iters": 5},
                    "invite_config": {
                        "invitable": True,
                        "invite_description": "keep me",
                    },
                },
            },
        ).json()["agent_id"]

        updated = self.client.patch(
            f"/agent/{agent_id}",
            headers=HEADERS,
            json={"context_config": {"max_image_num": 9}},
        )

        self.assertEqual(updated.status_code, 200)
        chat_config = updated.json()["data"]["chat_config"]
        self.assertEqual(chat_config["context_config"]["max_image_num"], 9)
        self.assertEqual(chat_config["react_config"]["max_iters"], 5)
        self.assertEqual(
            chat_config["invite_config"]["invite_description"],
            "keep me",
        )

    def test_schema_exposes_the_grouped_sections(self) -> None:
        """The form schema carries the sections inside ``chat_config``."""
        schema = self.client.get("/agent/schema/v2").json()["schema"]

        self.assertListEqual(
            sorted(schema["properties"]),
            ["chat_config", "name", "system_prompt"],
        )
        chat_config = schema["properties"]["chat_config"]
        self.assertListEqual(
            sorted(chat_config["properties"]),
            ["context_config", "invite_config", "react_config"],
        )
        self.assertNotIn(
            "summary_schema",
            chat_config["properties"]["context_config"]["properties"],
        )

    async def test_stored_agent_survives_a_round_trip(self) -> None:
        """A legacy row in storage is readable and rewritten grouped."""
        record = AgentRecord(
            user_id="alice",
            data=AgentData.model_validate(
                {
                    "name": "ann",
                    "react_config": {"max_iters": 7},
                },
            ),
        )
        agent_id = await self.storage.upsert_agent("alice", record)

        stored = await self.storage.get_agent("alice", agent_id)

        self.assertEqual(stored.data.chat_config.react_config.max_iters, 7)
        self.assertListEqual(
            sorted(stored.data.model_dump()),
            ["chat_config", "id", "name", "system_prompt"],
        )
