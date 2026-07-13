# -*- coding: utf-8 -*-
"""Shared helpers for optional live Daytona tests."""

import os
import uuid

from agentscope.workspace._daytona._constants import METADATA_WORKSPACE_ID_KEY

DAYTONA_API_KEY = os.getenv("DAYTONA_API_KEY", "")
DAYTONA_API_URL = os.getenv("DAYTONA_API_URL", "")
DAYTONA_TARGET = os.getenv("DAYTONA_TARGET", "")
SKIP_REASON = "DAYTONA_API_KEY environment variable is not set"


def live_daytona_workspace_id(prefix: str) -> str:
    """Return a unique provider label value for one live Daytona test."""
    return f"{prefix}-{uuid.uuid4().hex}"


def live_daytona_kwargs() -> dict[str, str]:
    """Connection kwargs shared by live Daytona tests."""
    return {
        "api_key": os.getenv("DAYTONA_API_KEY", ""),
        "api_url": os.getenv("DAYTONA_API_URL", ""),
        "target": os.getenv("DAYTONA_TARGET", ""),
    }


async def delete_live_daytona_workspace(workspace_id: str) -> None:
    """Delete one live test sandbox by AgentScope workspace label."""
    from daytona import AsyncDaytona, DaytonaConfig, ListSandboxesQuery

    config = {
        key: value for key, value in live_daytona_kwargs().items() if value
    }
    client = (
        AsyncDaytona(DaytonaConfig(**config)) if config else AsyncDaytona()
    )
    try:
        query = ListSandboxesQuery(
            labels={METADATA_WORKSPACE_ID_KEY: workspace_id},
        )
        async for sandbox in client.list(query):
            try:
                await sandbox.stop(timeout=60, force=True)
            except Exception:
                pass
            await sandbox.delete(timeout=60)
    finally:
        await client.close()
