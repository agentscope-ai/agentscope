# -*- coding: utf-8 -*-
# pylint: disable=protected-access,unused-argument
"""Event-loop liveness tests for the Gemini embedding paths.

``embed_content`` is a blocking SDK call, so these tests pin the contract
that the model must not hold the event loop while waiting for it: other
coroutines and scheduled callbacks have to keep running.
"""
import asyncio
import threading
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.credential import GeminiCredential
from agentscope.embedding import GeminiEmbeddingModel


class GeminiEventLoopTest(IsolatedAsyncioTestCase):
    """Text and multimodal paths must yield the event loop."""

    @staticmethod
    def _progressed(loop: asyncio.AbstractEventLoop) -> bool:
        """True if the loop ticked while the SDK call was in flight."""
        flag = threading.Event()
        loop.call_soon_threadsafe(flag.set)
        return flag.wait(1)

    def _model(self, model: str) -> GeminiEmbeddingModel:
        return GeminiEmbeddingModel(
            credential=GeminiCredential(api_key="k"),
            model=model,
            dimensions=768,
        )

    async def test_text_call_does_not_block_loop(self) -> None:
        """The event loop keeps running while ``embed_content`` waits."""
        loop = asyncio.get_running_loop()
        model = self._model("gemini-embedding-001")
        observed: list[bool] = []

        def sdk_call(**_kwargs: Any) -> Any:
            observed.append(self._progressed(loop))
            return SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.1, 0.2])],
            )

        with patch.object(model.client.models, "embed_content", sdk_call):
            await model(["hello"])

        self.assertEqual(observed, [True])

    async def test_multimodal_call_does_not_block_loop(self) -> None:
        """The multimodal path yields the loop too."""
        loop = asyncio.get_running_loop()
        model = self._model("gemini-embedding-2")
        observed: list[bool] = []

        def sdk_call(**_kwargs: Any) -> Any:
            observed.append(self._progressed(loop))
            return SimpleNamespace(
                embeddings=[SimpleNamespace(values=[0.1, 0.2])],
            )

        with patch.object(model.client.models, "embed_content", sdk_call):
            await model(["hello"])

        self.assertEqual(observed, [True])
