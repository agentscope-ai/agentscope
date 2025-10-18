# -*- coding: utf-8 -*-
"""Subagent lifecycle tests."""
from __future__ import annotations

import asyncio

import pytest

from agentscope.agent import SubAgentUnavailable
from ._shared import (
    UnhealthySubAgent,
    build_host_agent,
    build_spec,
)


def test_subagent_lifecycle_healthcheck() -> None:
    """Registration should abort when subagent healthcheck fails."""

    async def _run() -> None:
        agent = build_host_agent()

        with pytest.raises(SubAgentUnavailable):
            await agent.register_subagent(
                UnhealthySubAgent,
                build_spec("unhealthy"),
            )

    asyncio.run(_run())
