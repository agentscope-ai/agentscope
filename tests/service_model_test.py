# -*- coding: utf-8 -*-
"""Unit tests for :func:`get_model` — the chat model factory.

The factory used to build the model with only ``credential``, ``model``
and ``parameters``, so the matching model card's ``context_size`` never
reached the constructor and every model silently kept its provider class
default — 65536 for DeepSeek while the card declares 1000000. ``Agent``
derives every context-compression threshold from
``model.context_size``, so an under-reported value makes compression
fire far too early.
"""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from agentscope.app._service._model import get_model
from agentscope.app.storage import ChatModelConfig


class _StubAccess:
    """Stand-in for ``ResourceAccessService``.

    ``get_model`` only reads the resolved record's ``data``.
    """

    def __init__(self, data: dict) -> None:
        self._data = data

    async def resolve_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> SimpleNamespace:
        """Return the canned credential record.

        Args:
            user_id (`str`):
                The viewer's user id, unused.
            credential_id (`str`):
                The credential id, unused.

        Returns:
            `SimpleNamespace`:
                The canned record.
        """
        del user_id, credential_id
        return SimpleNamespace(data=self._data)


def _config(model: str) -> ChatModelConfig:
    """Build a DeepSeek chat model config for ``model``.

    Args:
        model (`str`):
            The model name.

    Returns:
        `ChatModelConfig`:
            The configuration.
    """
    return ChatModelConfig(
        type="deepseek",
        credential_id="cred-1",
        model=model,
        parameters={},
    )


_CREDENTIAL = {"type": "deepseek_credential", "api_key": "test"}


class GetModelContextSizeTest(IsolatedAsyncioTestCase):
    """The factory must forward the model card's ``context_size``."""

    async def test_uses_card_context_size(self) -> None:
        """A matching card's value wins over the class default."""
        model = await get_model(
            "user-1",
            _config("deepseek-v4-flash"),
            _StubAccess(_CREDENTIAL),
        )

        self.assertEqual(1000000, model.context_size)

    async def test_keeps_default_for_unknown_model(self) -> None:
        """A model without a card keeps the constructor default."""
        model = await get_model(
            "user-1",
            _config("my-custom-model"),
            _StubAccess(_CREDENTIAL),
        )

        self.assertEqual(65536, model.context_size)
