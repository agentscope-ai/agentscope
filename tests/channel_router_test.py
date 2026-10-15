# -*- coding: utf-8 -*-
"""``PATCH /channels/{channel_id}`` test case — rejected writes and survivors.

The handler forwards the caller's field set as written, so the service has
to decide what a patch may contain. Two rules follow, and both are
asserted here:

- a value that the record cannot hold is refused, because persisting it
  would break every later read of that record — including the ``DELETE``
  that would undo it; and
- ``null`` stays usable where the column is nullable, so clearing the
  display name still works.
"""
import tempfile
from typing import Any
from unittest import IsolatedAsyncioTestCase

import fakeredis.aioredis
from fastapi.testclient import TestClient
from pydantic import BaseModel

from agentscope.app import create_app
from agentscope.app.channel import ChannelBase, ChannelStatus
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager

HEADERS = {"X-User-ID": "alice"}


class _FakeChannel(ChannelBase):
    """The minimal channel type the tests register."""

    channel_type = "fake"
    display_name = "Fake"
    platform_bot_id_field = "bot_id"

    class Credentials(BaseModel):
        """The credentials — only a platform bot id."""

        bot_id: str

    class Config(BaseModel):
        """No platform options."""

    def __init__(self, channel_id: str, credentials: Any, config: Any) -> None:
        """Store the identity without opening a platform connection."""
        self._channel_id = channel_id
        self.bot_id = credentials.bot_id
        self.status = ChannelStatus()

    @property
    def channel_id(self) -> str:
        """The instance identifier."""
        return self._channel_id

    async def start_listening(self, emit: Any) -> None:
        """No platform to listen to."""

    async def aclose(self) -> None:
        """Nothing was opened."""

    async def send_response(self, event: Any, events: Any) -> None:
        """Nothing is delivered."""


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


class ChannelConfigPatchTest(IsolatedAsyncioTestCase):
    """Exercise the channel PATCH endpoint against a fakeredis-backed app."""

    def setUp(self) -> None:
        """Start an app with one fake channel type and one channel."""
        # enterContext binds the context manager to the test's lifetime;
        # pylint does not recognise the unittest-native helper.
        # pylint: disable=consider-using-with
        workdir = self.enterContext(tempfile.TemporaryDirectory())
        storage, bus = _fake_backends()
        app = create_app(
            storage=storage,
            message_bus=bus,
            workspace_manager=LocalWorkspaceManager(workdir),
            channels=[_FakeChannel],
            enable_index_worker=False,
        )
        self._client = self.enterContext(TestClient(app))

        created = self._client.post(
            "/channels/",
            headers=HEADERS,
            json={
                "channel_type": "fake",
                "name": "my bot",
                "credentials": {"bot_id": "bot-1"},
                "routing": {
                    "bindings": [{"match_value": "*", "agent_id": "agent-x"}],
                },
                "session": {"chat_model_config": {"type": "x"}},
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.channel_id = created.json()["id"]

    def _patch(self, body: dict) -> Any:
        """PATCH this channel and return the response."""
        return self._client.patch(
            f"/channels/{self.channel_id}",
            headers=HEADERS,
            json=body,
        )

    def test_null_on_a_required_field_is_refused_and_harmless(self) -> None:
        """A rejected patch leaves the channel readable.

        ``model_copy`` used to apply the null without validating, so the
        record reached storage in a shape no reader could deserialize: the
        single channel, the channel list and even its own DELETE all
        answered 500 afterwards.
        """
        before = self._client.get(
            f"/channels/{self.channel_id}",
            headers=HEADERS,
        ).json()

        response = self._patch({"routing": None})

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(
            [
                channel["id"]
                for channel in self._client.get(
                    "/channels/",
                    headers=HEADERS,
                ).json()
            ],
            [self.channel_id],
        )
        self.assertEqual(
            self._client.get(
                f"/channels/{self.channel_id}",
                headers=HEADERS,
            ).json(),
            before,
        )
        self.assertEqual(
            self._client.delete(
                f"/channels/{self.channel_id}",
                headers=HEADERS,
            ).status_code,
            204,
        )

    def test_null_still_clears_the_nullable_name(self) -> None:
        """``name`` is the one nullable field, so it keeps accepting null."""
        self.assertEqual(self._patch({"name": None}).status_code, 200)

        self.assertEqual(
            self._client.get(
                f"/channels/{self.channel_id}",
                headers=HEADERS,
            ).json()["name"],
            None,
        )

    def test_valid_patch_is_applied_and_persisted(self) -> None:
        """A well-formed update still writes through to storage."""
        self.assertEqual(self._patch({"enabled": False}).status_code, 200)

        self.assertEqual(
            self._client.get(
                f"/channels/{self.channel_id}",
                headers=HEADERS,
            ).json()["enabled"],
            False,
        )
