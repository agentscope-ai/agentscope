# -*- coding: utf-8 -*-
"""Subagent registration probe tests (no healthcheck)."""
from __future__ import annotations

import asyncio

import pytest

from pydantic import BaseModel

from agentscope.agent import SubAgentUnavailable, SubAgentBase
from agentscope.message import Msg
from ._shared import build_host_agent, build_spec


def test_subagent_registration_probe_constructor_failure() -> None:
    """Registration should abort when constructor/export fails fast."""

    async def _run() -> None:
        agent = build_host_agent()

        class CtorFailSubAgent(SubAgentBase):
            class Input(BaseModel):
                message: str = "noop"

            InputModel = Input

            async def observe(self, msg):  # pragma: no cover
                pass

            async def reply(self, input_obj, **_):  # pragma: no cover
                return Msg(
                    "ctor",
                    input_obj.message,
                    "assistant",
                )  # type: ignore[name-defined]

            def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
                super().__init__(**kwargs)
                raise RuntimeError("missing dependency")

        with pytest.raises(SubAgentUnavailable):
            await agent.register_subagent(
                CtorFailSubAgent,
                build_spec("ctorfail"),
            )

    asyncio.run(_run())
