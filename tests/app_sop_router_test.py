# -*- coding: utf-8 -*-
"""Tests for the SOP endpoints, over a real app and fake backends.

No model is ever called: every run here stops at a step a person has to
judge, which is exactly the path the endpoints exist to serve.
"""
import tempfile
from typing import Any
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis
from fastapi.testclient import TestClient

from agentscope.app import create_app
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager

HEADERS = {"X-User-ID": "alice"}

MODEL = {
    "type": "dashscope",
    "credential_id": "c-1",
    "model": "qwen-max",
    "parameters": {},
}


def _fake_backends() -> tuple:
    """Build a fakeredis-backed storage and message bus."""
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

    return _Storage(), _Bus()


class SOPRouterTest(IsolatedAsyncioTestCase):
    """Procedures, their runs, and the verdicts people file."""

    def setUp(self) -> None:
        """Start an app and register the agent a procedure refers to."""
        # pylint: disable=consider-using-with
        workdir = self.enterContext(tempfile.TemporaryDirectory())
        storage, bus = _fake_backends()
        self._client = self.enterContext(
            TestClient(
                create_app(
                    storage=storage,
                    message_bus=bus,
                    workspace_manager=LocalWorkspaceManager(workdir),
                    enable_index_worker=False,
                ),
            ),
        )
        self._agent_id = self._client.post(
            "/agent/",
            json={"name": "modeller", "system_prompt": "hi"},
            headers=HEADERS,
        ).json()["agent_id"]

    def _data(self) -> dict:
        """A one-step procedure a person signs off."""
        return {
            "name": "ship",
            "description": "build one",
            "steps": [
                {
                    "subject": "model",
                    "description": "make the hull",
                    "executor": {
                        "agent_id": self._agent_id,
                        "session_key": "modeller",
                    },
                    "verifier": {
                        "type": "human",
                        "question": "Watertight?",
                    },
                },
            ],
            "session_settings": {
                "modeller": {"chat_model_config": MODEL},
            },
        }

    def test_a_procedure_round_trips_through_the_api(self) -> None:
        """Create, read, list, edit, delete."""
        created = self._client.post(
            "/sop/",
            json={"data": self._data()},
            headers=HEADERS,
        )
        self.assertEqual(created.status_code, 201)
        sop_id = created.json()["sop_id"]

        fetched = self._client.get(f"/sop/{sop_id}", headers=HEADERS)
        self.assertEqual(fetched.json()["data"]["name"], "ship")

        listed = self._client.get("/sop/", headers=HEADERS).json()
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["sops"][0]["id"], sop_id)

        renamed = dict(self._data(), name="ship faster")
        patched = self._client.patch(
            f"/sop/{sop_id}",
            json={"data": renamed},
            headers=HEADERS,
        )
        self.assertEqual(patched.json()["data"]["name"], "ship faster")

        self.assertEqual(
            self._client.delete(f"/sop/{sop_id}", headers=HEADERS).status_code,
            204,
        )
        self.assertEqual(
            self._client.get(f"/sop/{sop_id}", headers=HEADERS).status_code,
            404,
        )

    def test_a_step_naming_an_unconfigured_conversation_is_refused(
        self,
    ) -> None:
        """A run of it could not open that session, so it is caught here."""
        data = self._data()
        data["session_settings"] = {}

        refused = self._client.post(
            "/sop/",
            json={"data": data},
            headers=HEADERS,
        )

        self.assertEqual(refused.status_code, 422)
        self.assertIn("modeller", refused.json()["detail"])

    def test_the_schema_resolves_every_ref_it_names(self) -> None:
        """A tagged union's mapping has to point at something."""
        schema = self._client.get("/sop/schema", headers=HEADERS).json()[
            "schema"
        ]

        self.assertIn("steps", schema["properties"])
        defs = set(schema.get("$defs", {}))
        named = set()

        def _walk(node: Any) -> None:
            """Collect every ``#/$defs/...`` pointer in the schema."""
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str) and ref.startswith("#/$defs/"):
                    named.add(ref.removeprefix("#/$defs/"))
                mapping = node.get("discriminator", {}).get("mapping", {})
                for target in mapping.values():
                    named.add(target.removeprefix("#/$defs/"))
                for value in node.values():
                    _walk(value)
            elif isinstance(node, list):
                for value in node:
                    _walk(value)

        _walk(schema)
        self.assertIn("AgentVerifier", named)
        self.assertEqual(named - defs, set())

    def test_a_run_opens_its_conversations_and_is_listed(self) -> None:
        """Starting a run returns it before it has got anywhere."""
        sop_id = self._client.post(
            "/sop/",
            json={"data": self._data()},
            headers=HEADERS,
        ).json()["sop_id"]

        started = self._client.post(
            f"/sop/{sop_id}/runs",
            json={"inputs": []},
            headers=HEADERS,
        )
        self.assertEqual(started.status_code, 201)
        run = started.json()["run"]
        self.assertEqual(list(run["sessions"]), ["modeller"])
        self.assertEqual(run["sop_id"], sop_id)

        listed = self._client.get(
            "/sop/runs",
            params={"sop_id": sop_id},
            headers=HEADERS,
        ).json()
        self.assertEqual([_["id"] for _ in listed["runs"]], [run["id"]])

        self.assertEqual(
            self._client.get(
                f"/sop/runs/{run['id']}",
                headers=HEADERS,
            ).json()["run"]["id"],
            run["id"],
        )

    def test_a_verdict_is_refused_on_a_step_nobody_was_asked_about(
        self,
    ) -> None:
        """Nothing has been handed over yet, so there is nothing to judge."""
        sop_id = self._client.post(
            "/sop/",
            json={"data": self._data()},
            headers=HEADERS,
        ).json()["sop_id"]
        run_id = self._client.post(
            f"/sop/{sop_id}/runs",
            json={"inputs": []},
            headers=HEADERS,
        ).json()["run"]["id"]

        refused = self._client.post(
            f"/sop/runs/{run_id}/verdict",
            json={"step_index": 0, "passed": True},
            headers=HEADERS,
        )
        self.assertEqual(refused.status_code, 409)

        missing = self._client.post(
            f"/sop/runs/{run_id}/verdict",
            json={"step_index": 9, "passed": True},
            headers=HEADERS,
        )
        self.assertEqual(missing.status_code, 404)

    def test_another_user_sees_none_of_it(self) -> None:
        """Everything is owner-scoped, including the run endpoints."""
        sop_id = self._client.post(
            "/sop/",
            json={"data": self._data()},
            headers=HEADERS,
        ).json()["sop_id"]
        run_id = self._client.post(
            f"/sop/{sop_id}/runs",
            json={"inputs": []},
            headers=HEADERS,
        ).json()["run"]["id"]

        other = {"X-User-ID": "bob"}
        self.assertEqual(
            self._client.get("/sop/", headers=other).json()["total"],
            0,
        )
        self.assertEqual(
            self._client.get(f"/sop/{sop_id}", headers=other).status_code,
            404,
        )
        self.assertEqual(
            self._client.get(
                f"/sop/runs/{run_id}",
                headers=other,
            ).status_code,
            404,
        )
        self.assertEqual(
            self._client.post(
                f"/sop/runs/{run_id}/verdict",
                json={"step_index": 0, "passed": True},
                headers=other,
            ).status_code,
            404,
        )
