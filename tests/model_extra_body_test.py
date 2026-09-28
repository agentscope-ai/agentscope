# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests that a caller's ``extra_body`` dict is never mutated.

The DeepSeek, Moonshot and Volcengine chat models default
``extra_body["thinking"]["type"]`` while building a request. They must copy
the dict instead of filling it in place, otherwise a reused dict grows an
unexpected ``thinking`` key and pins the first call's value.
"""
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.credential import (
    DeepSeekCredential,
    MoonshotCredential,
    VolcengineCredential,
)
from agentscope.model import (
    DeepSeekChatModel,
    MoonshotChatModel,
    VolcengineChatModel,
)


class ExtraBodyMutationTest(IsolatedAsyncioTestCase):
    """Shared assertions for the ``extra_body`` mutation defect."""

    def _build_model(self) -> Any:
        """Create the model under test."""
        raise NotImplementedError

    async def _call(self, model: Any, extra_body: dict) -> AsyncMock:
        """Run one API call against a mocked client.

        The response parser is stubbed out, because the defect under test
        happens while the request is assembled, before any parsing.

        Args:
            model (`Any`):
                The model under test.
            extra_body (`dict`):
                The dict to hand to the model as ``extra_body``.

        Returns:
            `AsyncMock`:
                The mocked ``chat.completions.create``.
        """
        create = AsyncMock(return_value=MagicMock())
        client = MagicMock()
        client.chat.completions.create = create
        model.client = client
        with patch.object(
            type(model),
            "_parse_completion_response",
            return_value=MagicMock(),
        ):
            await model._call_api(
                "extra-body-test-model",
                [],
                extra_body=extra_body,
            )
        return create

    async def test_caller_extra_body_is_not_mutated(self) -> None:
        """The dict the caller passed in must come back unchanged."""
        model = self._build_model()
        model.parameters.thinking_enable = False
        extra = {"top_k": 5}

        create = await self._call(model, extra)

        self.assertEqual(extra, {"top_k": 5})
        sent = create.call_args.kwargs["extra_body"]
        self.assertEqual(
            sent,
            {"top_k": 5, "thinking": {"type": "disabled"}},
        )

    async def test_reused_extra_body_does_not_pin_thinking_type(
        self,
    ) -> None:
        """A reused dict must not freeze the first call's thinking type."""
        model = self._build_model()
        extra = {"top_k": 5}

        model.parameters.thinking_enable = False
        await self._call(model, extra)

        model.parameters.thinking_enable = True
        create = await self._call(model, extra)

        sent = create.call_args.kwargs["extra_body"]
        self.assertEqual(sent["thinking"]["type"], "enabled")


class DeepSeekExtraBodyTest(ExtraBodyMutationTest):
    """DeepSeek must not mutate the caller's ``extra_body``."""

    def _build_model(self) -> Any:
        """Create a DeepSeek chat model."""
        return DeepSeekChatModel(
            credential=DeepSeekCredential(api_key="test"),
            model="deepseek-v4-pro",
            stream=False,
            context_size=65_536,
        )


class MoonshotExtraBodyTest(ExtraBodyMutationTest):
    """Moonshot must not mutate the caller's ``extra_body``."""

    def _build_model(self) -> Any:
        """Create a non-k3 Moonshot chat model.

        ``kimi-k3`` reports thinking through ``reasoning_effort`` instead of
        ``extra_body``, so it takes a different branch in ``_call_api``.
        """
        return MoonshotChatModel(
            credential=MoonshotCredential(api_key="test"),
            model="kimi-k2-5",
            stream=False,
            context_size=131_072,
        )


class VolcengineExtraBodyTest(ExtraBodyMutationTest):
    """Volcengine must not mutate the caller's ``extra_body``."""

    def _build_model(self) -> Any:
        """Create a Volcengine chat model."""
        return VolcengineChatModel(
            credential=VolcengineCredential(api_key="test"),
            model="doubao-seed-2-1-pro-260628",
            stream=False,
            context_size=65_536,
        )
