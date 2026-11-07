# -*- coding: utf-8 -*-
"""Multi-agent formatters must not emit a null content for a textless
system message.

``Msg.get_text_content()`` returns ``None`` when the message carries no
text block, but an empty system message (``Msg(..., [], "system")``) is a
valid input. Copying that ``None`` into the provider payload makes the
first request fail: OpenAI-compatible endpoints reject a non-assistant
message whose ``content`` is null, and the Gemini API rejects a ``text``
part that is null. These regressions pin the empty string, which those
APIs accept, and keep the ordinary non-empty system prompt unchanged.
"""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.formatter import (
    DashScopeMultiAgentFormatter,
    GeminiMultiAgentFormatter,
    OllamaMultiAgentFormatter,
)
from agentscope.message import Msg


def _empty_system_and_user() -> list[Msg]:
    """A textless system message followed by a user turn."""
    return [Msg("sys", [], "system"), Msg("u", "hi", "user")]


def _system_and_user() -> list[Msg]:
    """A normal system prompt followed by a user turn."""
    return [Msg("sys", "be brief", "system"), Msg("u", "hi", "user")]


class _SystemMessageAssertions:
    """Shared checks for the system-turn payload of each formatter."""

    async def _assert_empty_emits_empty_string(
        self,
        formatter: Any,
        turn: dict,
    ) -> None:
        res = await formatter.format(_empty_system_and_user())
        self.assertEqual(res[0], turn)

    async def _assert_non_empty_unchanged(
        self,
        formatter: Any,
        turn: dict,
    ) -> None:
        res = await formatter.format(_system_and_user())
        self.assertEqual(res[0], turn)


class DashScopeMultiAgentSystemMessageTest(
    _SystemMessageAssertions,
    IsolatedAsyncioTestCase,
):
    """DashScope multi-agent formatter system turn."""

    async def test_empty_system_emits_empty_string(self) -> None:
        await self._assert_empty_emits_empty_string(
            DashScopeMultiAgentFormatter(),
            {"role": "system", "content": ""},
        )

    async def test_non_empty_system_unchanged(self) -> None:
        await self._assert_non_empty_unchanged(
            DashScopeMultiAgentFormatter(),
            {"role": "system", "content": "be brief"},
        )


class OllamaMultiAgentSystemMessageTest(
    _SystemMessageAssertions,
    IsolatedAsyncioTestCase,
):
    """Ollama multi-agent formatter system turn."""

    async def test_empty_system_emits_empty_string(self) -> None:
        await self._assert_empty_emits_empty_string(
            OllamaMultiAgentFormatter(),
            {"role": "system", "content": ""},
        )

    async def test_non_empty_system_unchanged(self) -> None:
        await self._assert_non_empty_unchanged(
            OllamaMultiAgentFormatter(),
            {"role": "system", "content": "be brief"},
        )


class GeminiMultiAgentSystemMessageTest(
    _SystemMessageAssertions,
    IsolatedAsyncioTestCase,
):
    """Gemini multi-agent formatter system turn."""

    async def test_empty_system_emits_empty_string(self) -> None:
        await self._assert_empty_emits_empty_string(
            GeminiMultiAgentFormatter(),
            {"role": "user", "parts": [{"text": ""}]},
        )

    async def test_non_empty_system_unchanged(self) -> None:
        await self._assert_non_empty_unchanged(
            GeminiMultiAgentFormatter(),
            {"role": "user", "parts": [{"text": "be brief"}]},
        )
