# -*- coding: utf-8 -*-
"""Tests for concurrent embedding batch lifetimes."""
import asyncio
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.credential import OpenAICredential
from agentscope.embedding import EmbeddingModelBase, EmbeddingResponse


class _RetryableError(RuntimeError):
    """A provider error eligible for retry."""


class _LocalEmbedding(EmbeddingModelBase[str]):
    """Exercise the base implementation without a provider connection."""

    def __init__(self, max_retries: int = 0) -> None:
        super().__init__(
            credential=OpenAICredential(api_key="unused-test-key"),
            model="local",
            dimensions=1,
            parameters=None,
            context_size=100,
            batch_size=1,
            max_retries=max_retries,
            retry_delay=0,
        )

    @classmethod
    def _get_retryable_exceptions(cls) -> tuple[type[Exception], ...]:
        return (_RetryableError,)

    async def _call_api(self, inputs: list[str], **kwargs) -> EmbeddingResponse:
        raise NotImplementedError


class EmbeddingBatchLifecycleTest(IsolatedAsyncioTestCase):
    """A failed call must finish its cooperative batch tasks."""

    async def test_batch_failure_waits_for_sibling_cleanup(self) -> None:
        """Join async cleanup and preserve the original provider error."""
        for retryable in (False, True):
            for inputs in (["fail", "slow"], ["slow", "fail"]):
                with self.subTest(retryable=retryable, inputs=inputs):
                    model = _LocalEmbedding(max_retries=1 if retryable else 0)
                    started = asyncio.Event()
                    cleaning = asyncio.Event()
                    release = asyncio.Event()
                    finished = asyncio.Event()
                    error = (
                        _RetryableError("failed")
                        if retryable
                        else ValueError("failed")
                    )
                    attempts = 0

                    async def call_api(batch, **kwargs):
                        nonlocal attempts
                        if batch == ["fail"]:
                            await started.wait()
                            attempts += 1
                            raise error
                        started.set()
                        try:
                            await asyncio.Event().wait()
                        finally:
                            cleaning.set()
                            await release.wait()
                            finished.set()

                    with patch.object(model, "_call_api", side_effect=call_api):
                        call = asyncio.create_task(model(inputs))
                        try:
                            await asyncio.wait_for(cleaning.wait(), 1)
                            self.assertFalse(call.done())
                            release.set()
                            with self.assertRaises(type(error)) as caught:
                                await asyncio.wait_for(call, 1)
                            self.assertIs(caught.exception, error)
                            self.assertTrue(finished.is_set())
                            self.assertEqual(attempts, 2 if retryable else 1)
                        finally:
                            release.set()
                            call.cancel()
                            await asyncio.gather(call, return_exceptions=True)

    async def test_parent_cancellation_joins_batches(self) -> None:
        """Cancellation waits for all batch cleanup before propagating."""
        model = _LocalEmbedding()
        started = [asyncio.Event(), asyncio.Event()]
        finished = []
        cleaning = asyncio.Event()
        release = asyncio.Event()

        async def call_api(batch, **kwargs):
            index = int(batch[0])
            started[index].set()
            try:
                await asyncio.Event().wait()
            finally:
                if index == 1:
                    cleaning.set()
                    await release.wait()
                finished.append(index)

        with patch.object(model, "_call_api", side_effect=call_api):
            call = asyncio.create_task(model(["0", "1"]))
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(event.wait() for event in started)),
                    1,
                )
                call.cancel()
                await asyncio.wait_for(cleaning.wait(), 1)
                await asyncio.sleep(0)
                self.assertFalse(call.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(call, 1)
                self.assertCountEqual(finished, [0, 1])
            finally:
                release.set()
                call.cancel()
                await asyncio.gather(call, return_exceptions=True)

    async def test_cancelled_batch_joins_sibling(self) -> None:
        """A child cancellation also cleans up the other batch."""
        model = _LocalEmbedding()
        started = asyncio.Event()
        finished = asyncio.Event()

        async def call_api(batch, **kwargs):
            if batch == ["cancel"]:
                await started.wait()
                raise asyncio.CancelledError
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                finished.set()

        with patch.object(model, "_call_api", side_effect=call_api):
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(model(["cancel", "slow"]), 1)
        self.assertTrue(finished.is_set())

    async def test_success_preserves_input_order(self) -> None:
        """Merge in input order even when batches finish in reverse order."""
        model = _LocalEmbedding()
        second_finished = asyncio.Event()

        async def call_api(batch, **kwargs):
            if batch == ["first"]:
                await second_finished.wait()
                return EmbeddingResponse(embeddings=[[1.0]])
            second_finished.set()
            return EmbeddingResponse(embeddings=[[2.0]])

        with patch.object(model, "_call_api", side_effect=call_api):
            response = await asyncio.wait_for(model(["first", "second"]), 1)
        self.assertEqual(response.embeddings, [[1.0], [2.0]])

    async def test_empty_input_does_not_call_provider(self) -> None:
        """Empty input creates no batch work."""
        model = _LocalEmbedding()
        with patch.object(model, "_call_api", new_callable=AsyncMock) as call:
            response = await model([])
        self.assertEqual(response.embeddings, [])
        call.assert_not_awaited()
