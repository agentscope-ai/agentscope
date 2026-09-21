# -*- coding: utf-8 -*-
# pylint: disable=protected-access,unused-argument
"""Event-loop liveness tests for the DashScope embedding paths.

The DashScope SDK exposes only blocking calls, so these tests pin the
contract that the model must not hold the event loop while waiting for a
response: other coroutines and scheduled callbacks have to keep running.
"""
import asyncio
import threading
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.credential import DashScopeCredential
from agentscope.embedding import DashScopeEmbeddingModel
from agentscope.message import DataBlock, Base64Source

from tests.embedding_dashscope_test import _text_resp


class DashScopeEventLoopTest(IsolatedAsyncioTestCase):
    """Text and multimodal paths must yield the event loop."""

    @staticmethod
    def _progressed(loop: asyncio.AbstractEventLoop) -> bool:
        """True if the loop ticked while the SDK call was in flight."""
        flag = threading.Event()
        loop.call_soon_threadsafe(flag.set)
        return flag.wait(1)

    @patch("dashscope.embeddings.TextEmbedding.call")
    async def test_text_call_does_not_block_loop(self, mock_api: Any) -> None:
        """The event loop keeps running while the text SDK call waits."""
        loop = asyncio.get_running_loop()
        observed: list[bool] = []

        def sdk_call(**_kwargs: Any) -> Any:
            observed.append(self._progressed(loop))
            return _text_resp([[0.1, 0.2]])

        mock_api.side_effect = sdk_call
        model = DashScopeEmbeddingModel(
            credential=DashScopeCredential(api_key="k"),
            model="text-embedding-v4",
            dimensions=2,
        )

        await model(["hello"])

        self.assertEqual(observed, [True])

    @patch("dashscope.MultiModalEmbedding.call")
    async def test_multimodal_call_does_not_block_loop(
        self, mock_api: Any
    ) -> None:
        """The event loop keeps running while the multimodal call waits."""
        loop = asyncio.get_running_loop()
        observed: list[bool] = []

        def sdk_call(**_kwargs: Any) -> Any:
            observed.append(self._progressed(loop))
            resp = _text_resp([[0.1, 0.2]])
            resp.usage = {"image_tokens": 1, "input_tokens": 2}
            return resp

        mock_api.side_effect = sdk_call
        model = DashScopeEmbeddingModel(
            credential=DashScopeCredential(api_key="k"),
            model="qwen3-vl-embedding",
            dimensions=2,
        )

        image = DataBlock(
            source=Base64Source(data="aGk=", media_type="image/png"),
        )
        await model([image])

        self.assertEqual(observed, [True])
