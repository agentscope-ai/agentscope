# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Unit tests for EmbeddingModelBase."""
import asyncio
from dataclasses import asdict
from typing import Any, Literal
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from utils import AnyString

from agentscope.credential import OpenAICredential
from agentscope.embedding import (
    EmbeddingModelBase,
    EmbeddingResponse,
    EmbeddingUsage,
)


def _resp(
    source: Literal["cache", "api"],
    value: float,
    tokens: int = 0,
    time: float = 0.0,
) -> EmbeddingResponse:
    """Build a batch response with a distinct embedding and usage."""
    return EmbeddingResponse(
        embeddings=[[value]],
        usage=EmbeddingUsage(tokens=tokens, time=time),
        source=source,
    )


class EmbeddingModelBaseTest(TestCase):
    """Test response merging across cached and API batches."""

    def test_merge_single_batch_preserves_cache_source(self) -> None:
        """Preserve the complete response for a single cached batch."""
        response = _resp("cache", 1.0)

        merged = EmbeddingModelBase._merge_responses([response])

        self.assertEqual(asdict(merged), asdict(response))
        self.assertIs(merged, response)

    def test_merge_all_cache_batches_reports_cache(self) -> None:
        """Merge cached vectors in order with zero API usage."""
        merged = EmbeddingModelBase._merge_responses(
            [
                _resp("cache", 1.0),
                _resp("cache", 2.0),
                _resp("cache", 3.0),
            ],
        )

        self.assertEqual(
            asdict(merged),
            {
                "embeddings": [[1.0], [2.0], [3.0]],
                "id": AnyString(),
                "created_at": AnyString(),
                "type": "embedding",
                "usage": {"tokens": 0, "time": 0.0, "type": "embedding"},
                "source": "cache",
            },
        )

    def test_merge_live_api_batches_uses_max_time(self) -> None:
        """Preserve vector order, sum tokens, and take the maximum time."""
        merged = EmbeddingModelBase._merge_responses(
            [
                _resp("api", 1.0, tokens=7, time=0.25),
                _resp("api", 2.0, tokens=5, time=0.5),
            ],
        )

        self.assertEqual(
            asdict(merged),
            {
                "embeddings": [[1.0], [2.0]],
                "id": AnyString(),
                "created_at": AnyString(),
                "type": "embedding",
                "usage": {"tokens": 12, "time": 0.5, "type": "embedding"},
                "source": "api",
            },
        )

    def test_merge_mixed_cache_and_api_reports_api(self) -> None:
        """Report API usage when cached and API batches are combined."""
        merged = EmbeddingModelBase._merge_responses(
            [
                _resp("cache", 1.0),
                _resp("api", 2.0, tokens=7, time=0.5),
            ],
        )

        self.assertEqual(
            asdict(merged),
            {
                "embeddings": [[1.0], [2.0]],
                "id": AnyString(),
                "created_at": AnyString(),
                "type": "embedding",
                "usage": {"tokens": 7, "time": 0.5, "type": "embedding"},
                "source": "api",
            },
        )

    def test_merge_empty_responses_reports_api(self) -> None:
        """An empty response list must not count as a cache hit."""
        merged = EmbeddingModelBase._merge_responses([])

        self.assertEqual(
            asdict(merged),
            asdict(
                EmbeddingResponse(
                    embeddings=[],
                    id=AnyString(),
                    created_at=AnyString(),
                    usage=EmbeddingUsage(tokens=0, time=0.0),
                ),
            ),
        )


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

    async def _call_api(
        self,
        inputs: list[str],
        **_kwargs: Any,
    ) -> EmbeddingResponse:
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

                    async def call_api(
                        batch: list[str],
                        *,
                        call_started: asyncio.Event = started,
                        call_error: Exception = error,
                        call_cleaning: asyncio.Event = cleaning,
                        call_release: asyncio.Event = release,
                        call_finished: asyncio.Event = finished,
                        **_kwargs: Any,
                    ) -> None:
                        nonlocal attempts
                        if batch == ["fail"]:
                            await call_started.wait()
                            attempts += 1
                            raise call_error
                        call_started.set()
                        try:
                            await asyncio.Event().wait()
                        finally:
                            call_cleaning.set()
                            await call_release.wait()
                            call_finished.set()

                    with patch.object(
                        model,
                        "_call_api",
                        side_effect=call_api,
                    ):
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

        async def call_api(batch: list[str], **_kwargs: Any) -> None:
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

        async def call_api(batch: list[str], **_kwargs: Any) -> None:
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

        async def call_api(
            batch: list[str],
            **_kwargs: Any,
        ) -> EmbeddingResponse:
            if batch == ["first"]:
                await second_finished.wait()
                return EmbeddingResponse(embeddings=[[1.0]])
            second_finished.set()
            return EmbeddingResponse(embeddings=[[2.0]])

        with patch.object(model, "_call_api", side_effect=call_api):
            response = await asyncio.wait_for(model(["first", "second"]), 1)
        self.assertEqual(
            asdict(response),
            asdict(
                EmbeddingResponse(
                    embeddings=[[1.0], [2.0]],
                    id=AnyString(),
                    created_at=AnyString(),
                    usage=EmbeddingUsage(tokens=0, time=0),
                ),
            ),
        )

    async def test_empty_input_does_not_call_provider(self) -> None:
        """Empty input creates no batch work."""
        model = _LocalEmbedding()
        with patch.object(model, "_call_api", new_callable=AsyncMock) as call:
            response = await model([])
        self.assertEqual(
            asdict(response),
            asdict(
                EmbeddingResponse(
                    embeddings=[],
                    id=AnyString(),
                    created_at=AnyString(),
                    usage=EmbeddingUsage(tokens=0, time=0),
                ),
            ),
        )
        call.assert_not_awaited()

    async def test_parent_cancellation_during_failure_cleanup(self) -> None:
        """Finish sibling cleanup despite repeated caller cancellation."""
        for cancel_count in (1, 3):
            with self.subTest(cancel_count=cancel_count):
                model = _LocalEmbedding()
                started = asyncio.Event()
                cleaning = asyncio.Event()
                release = asyncio.Event()
                finished = asyncio.Event()

                async def call_api(
                    batch: list[str],
                    *,
                    call_started: asyncio.Event = started,
                    call_cleaning: asyncio.Event = cleaning,
                    call_release: asyncio.Event = release,
                    call_finished: asyncio.Event = finished,
                    **_kwargs: Any,
                ) -> None:
                    if batch == ["fail"]:
                        await call_started.wait()
                        raise ValueError("failed")
                    call_started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        call_cleaning.set()
                        await call_release.wait()
                        call_finished.set()

                with patch.object(model, "_call_api", side_effect=call_api):
                    call = asyncio.create_task(model(["fail", "slow"]))
                    try:
                        await asyncio.wait_for(cleaning.wait(), 1)
                        for _ in range(cancel_count):
                            call.cancel()
                            await asyncio.sleep(0)
                            self.assertFalse(call.done())
                        self.assertFalse(finished.is_set())
                        release.set()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(call, 1)
                        self.assertEqual(
                            (cleaning.is_set(), finished.is_set()),
                            (True, True),
                        )
                    finally:
                        release.set()
                        call.cancel()
                        await asyncio.gather(call, return_exceptions=True)
