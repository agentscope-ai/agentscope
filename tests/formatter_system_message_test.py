# -*- coding: utf-8 -*-
"""Multi-agent formatters must not emit a null content for a textless
system message.

``Msg.get_text_content()`` returns ``None`` when the message carries no
text block, but an empty system message (``Msg(..., [], "system")``) is a
valid input. Copying that ``None`` into the provider payload makes the
first request fail: OpenAI-compatible endpoints reject a non-assistant
message whose ``content`` is null, the formatters that read the text
blocks directly inherit a base that emits an empty content array, which
those endpoints also reject, and the Gemini API rejects a ``text`` part
that is null. These regressions pin the empty string, which those APIs
accept, and keep the ordinary non-empty system prompt unchanged.
"""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.formatter import (
    DashScopeMultiAgentFormatter,
    DeepSeekMultiAgentFormatter,
    GeminiMultiAgentFormatter,
    OllamaMultiAgentFormatter,
    OpenAIMultiAgentFormatter,
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
        """A textless system message must format to an empty string."""
        res = await formatter.format(_empty_system_and_user())
        self.assertEqual(res[0], turn)

    async def _assert_non_empty_unchanged(
        self,
        formatter: Any,
        turn: dict,
    ) -> None:
        """A non-empty system prompt must be formatted unchanged."""
        res = await formatter.format(_system_and_user())
        self.assertEqual(res[0], turn)


class DashScopeMultiAgentSystemMessageTest(
    _SystemMessageAssertions,
    IsolatedAsyncioTestCase,
):
    """DashScope multi-agent formatter system turn."""

    async def test_empty_system_emits_empty_string(self) -> None:
        """A textless system message emits an empty string, not null."""
        await self._assert_empty_emits_empty_string(
            DashScopeMultiAgentFormatter(),
            {"role": "system", "content": ""},
        )

    async def test_non_empty_system_unchanged(self) -> None:
        """A non-empty DashScope system prompt is unchanged."""
        await self._assert_non_empty_unchanged(
            DashScopeMultiAgentFormatter(),
            {"role": "system", "content": "be brief"},
        )


class DeepSeekMultiAgentSystemMessageTest(
    _SystemMessageAssertions,
    IsolatedAsyncioTestCase,
):
    """DeepSeek multi-agent formatter system turn."""

    async def test_empty_system_emits_empty_string(self) -> None:
        """A textless system message emits an empty string, not an array."""
        await self._assert_empty_emits_empty_string(
            DeepSeekMultiAgentFormatter(),
            {"role": "system", "content": ""},
        )

    async def test_non_empty_system_unchanged(self) -> None:
        """A non-empty DeepSeek system prompt keeps its text blocks."""
        await self._assert_non_empty_unchanged(
            DeepSeekMultiAgentFormatter(),
            {"role": "system", "content": [{"type": "text", "text": "be brief"}]},
        )


class OllamaMultiAgentSystemMessageTest(
    _SystemMessageAssertions,
    IsolatedAsyncioTestCase,
):
    """Ollama multi-agent formatter system turn."""

    async def test_empty_system_emits_empty_string(self) -> None:
        """A textless system message emits an empty string, not null."""
        await self._assert_empty_emits_empty_string(
            OllamaMultiAgentFormatter(),
            {"role": "system", "content": ""},
        )

    async def test_non_empty_system_unchanged(self) -> None:
        """A non-empty Ollama system prompt is unchanged."""
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
        """A textless system message emits an empty string, not null."""
        await self._assert_empty_emits_empty_string(
            GeminiMultiAgentFormatter(),
            {"role": "user", "parts": [{"text": ""}]},
        )

    async def test_non_empty_system_unchanged(self) -> None:
        """A non-empty Gemini system prompt is unchanged."""
        await self._assert_non_empty_unchanged(
            GeminiMultiAgentFormatter(),
            {"role": "user", "parts": [{"text": "be brief"}]},
        )


class OpenAIMultiAgentSystemMessageTest(
    _SystemMessageAssertions,
    IsolatedAsyncioTestCase,
):
    """OpenAI multi-agent formatter system turn."""

    async def test_empty_system_emits_empty_string(self) -> None:
        """A textless system message emits an empty string, not an array."""
        await self._assert_empty_emits_empty_string(
            OpenAIMultiAgentFormatter(),
            {"role": "system", "content": ""},
        )

    async def test_non_empty_system_unchanged(self) -> None:
        """A non-empty OpenAI system prompt keeps its text blocks."""
        await self._assert_non_empty_unchanged(
            OpenAIMultiAgentFormatter(),
            {"role": "system", "content": [{"type": "text", "text": "be brief"}]},
        )
