# -*- coding: utf-8 -*-
"""Offline regressions for the RAGFlow example's indexing lifecycle."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from examples.rag import ragflow_knowledge as example


class RAGFlowExampleTest(unittest.IsolatedAsyncioTestCase):
    """Never search an unsuccessfully indexed document or leak its client."""

    async def test_failed_and_cancelled_parsing_are_errors(self) -> None:
        """Failure takes precedence even if progress has reached one."""
        for run in ("FAIL", "CANCEL", "fail"):
            knowledge = SimpleNamespace(
                list_documents=AsyncMock(
                    return_value=[
                        SimpleNamespace(
                            document_id="doc-1",
                            metadata={
                                "run": run,
                                "progress": 1.0,
                            },
                        ),
                    ],
                ),
            )
            with self.subTest(run=run), self.assertRaises(RuntimeError):
                await example.wait_until_indexed(knowledge, "doc-1")

    async def test_completion_follows_only_the_requested_document(
        self,
    ) -> None:
        """An unrelated completed document cannot end the wait."""
        knowledge = SimpleNamespace(
            list_documents=AsyncMock(
                side_effect=[
                    [
                        SimpleNamespace(
                            document_id="other",
                            metadata={"run": "DONE"},
                        ),
                    ],
                    [
                        SimpleNamespace(
                            document_id="doc-1",
                            metadata={"run": "DONE"},
                        ),
                    ],
                ],
            ),
        )
        await example.wait_until_indexed(
            knowledge,
            "doc-1",
            poll_interval_sec=0.001,
        )
        self.assertEqual(knowledge.list_documents.await_count, 2)

    async def test_progress_completion_remains_supported(self) -> None:
        """Older responses can signal completion through progress alone."""
        knowledge = SimpleNamespace(
            list_documents=AsyncMock(
                return_value=[
                    SimpleNamespace(
                        document_id="doc-1",
                        metadata={"progress": 1.0},
                    ),
                ],
            ),
        )
        await example.wait_until_indexed(knowledge, "doc-1")

    async def test_timeout_is_an_error_and_bounds_inflight_poll(self) -> None:
        """A stalled HTTP poll counts against the overall indexing deadline."""
        finished = asyncio.Event()

        async def stalled() -> list:
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
            return []

        knowledge = SimpleNamespace(list_documents=stalled)
        started = asyncio.get_running_loop().time()
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(
                example.wait_until_indexed(
                    knowledge,
                    "doc-1",
                    timeout_sec=0.01,
                ),
                timeout=1,
            )
        self.assertTrue(finished.is_set())
        self.assertLess(asyncio.get_running_loop().time() - started, 0.5)

    async def test_pending_document_times_out(self) -> None:
        """Timeout must not return normally and let the example search."""
        knowledge = SimpleNamespace(list_documents=AsyncMock(return_value=[]))
        with self.assertRaises(TimeoutError):
            await example.wait_until_indexed(
                knowledge,
                "doc-1",
                timeout_sec=0.01,
                poll_interval_sec=0.001,
            )

    async def test_main_closes_client_on_success_and_errors(self) -> None:
        """Upload, indexing and retrieval failures all close the client."""
        for failure in (None, "upload_document", "wait", "search"):
            knowledge = SimpleNamespace(
                upload_document=AsyncMock(return_value="doc-1"),
                list_documents=AsyncMock(return_value=[]),
                search=AsyncMock(return_value=[]),
                aclose=AsyncMock(),
            )
            wait = AsyncMock()
            if failure:
                operation = (
                    wait
                    if failure == "wait"
                    else getattr(
                        knowledge,
                        failure,
                    )
                )
                operation.side_effect = RuntimeError("synthetic failure")
            with self.subTest(failure=failure), patch.dict(
                "os.environ",
                {
                    "RAGFLOW_API_KEY": "test",
                    "RAGFLOW_DATASET_ID": "dataset-1",
                },
            ), patch.object(
                example,
                "RAGFlowKnowledgeBase",
                return_value=knowledge,
            ), patch.object(
                example,
                "wait_until_indexed",
                wait,
            ), patch(
                "builtins.print",
            ):
                if failure:
                    with self.assertRaisesRegex(RuntimeError, "synthetic"):
                        await example.main()
                else:
                    await example.main()
                knowledge.aclose.assert_awaited_once()
                if failure in ("upload_document", "wait"):
                    knowledge.search.assert_not_awaited()

    async def test_main_closes_client_on_cancellation(self) -> None:
        """Caller cancellation propagates after resource cleanup."""
        knowledge = SimpleNamespace(
            upload_document=AsyncMock(side_effect=asyncio.CancelledError),
            aclose=AsyncMock(),
        )
        with patch.dict(
            "os.environ",
            {
                "RAGFLOW_API_KEY": "test",
                "RAGFLOW_DATASET_ID": "dataset-1",
            },
        ), patch.object(
            example,
            "RAGFlowKnowledgeBase",
            return_value=knowledge,
        ), self.assertRaises(
            asyncio.CancelledError,
        ):
            await example.main()
        knowledge.aclose.assert_awaited_once()
