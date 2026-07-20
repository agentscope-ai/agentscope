# -*- coding: utf-8 -*-
"""Shared white-box storage fixtures for Session Fork tests."""
# pylint: disable=protected-access

import fakeredis.aioredis

from agentscope.app.storage import ChatModelConfig, RedisStorage, SessionConfig


def make_storage(
    *,
    key_ttl: int | None = None,
    decode_responses: bool = True,
) -> RedisStorage:
    """Create a RedisStorage backed by fakeredis."""
    storage = RedisStorage.__new__(RedisStorage)
    storage._client = fakeredis.aioredis.FakeRedis(
        decode_responses=decode_responses,
    )
    storage.key_ttl = key_ttl
    storage.key_config = RedisStorage.KeyConfig()
    return storage


def make_config(name: str = "Original") -> SessionConfig:
    """Build a session config with nested mutable data."""
    return SessionConfig(
        workspace_id="shared-workspace",
        name=name,
        chat_model_config=ChatModelConfig(
            type="openai",
            credential_id="credential",
            model="model",
            parameters={"temperature": 0.2},
        ),
    )
