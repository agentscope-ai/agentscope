"""Regression tests for IndexWorker parser execution."""

import asyncio
import pickle
import threading
from concurrent.futures import Future
from typing import ClassVar
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch

from agentscope.app._service._index_worker import IndexWorker, _run_parser_sync
from agentscope.rag import ParserBase, Section


class _UnpicklableParser(ParserBase):
    """Parser-shaped test double with legitimate non-pickleable state."""

    supported_media_types: ClassVar[list[str]] = ["text/x-test"]

    def __init__(self) -> None:
        self._lock = threading.Lock()

    async def parse(self, file: bytes, filename: str) -> list[Section]:
        with self._lock:
            return []


class _BlockingParser(_UnpicklableParser):
    """Parser-shaped test double that blocks synchronously."""

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    async def parse(self, file: bytes, filename: str) -> list[Section]:
        self.started.set()
        self.release.wait()
        return []


class _FailingParser(_UnpicklableParser):
    """Parser-shaped test double that raises from parse()."""

    async def parse(self, file: bytes, filename: str) -> list[Section]:
        raise RuntimeError("parser boom")


def _worker(parser: _UnpicklableParser) -> IndexWorker:
    """Build a worker without starting any storage or vector backends."""
    return IndexWorker(
        storage=object(),  # type: ignore[arg-type]
        blob_store=object(),  # type: ignore[arg-type]
        knowledge_base_manager=object(),  # type: ignore[arg-type]
        parsers=[parser],
        node_id="test-node",
    )


class IndexWorkerParserExecutorTest(IsolatedAsyncioTestCase):
    """Parser execution must honor the configured executor."""

    async def test_default_path_accepts_unpicklable_parser(self) -> None:
        """A parser with a threading.Lock works without a process pool."""
        parser = _UnpicklableParser()
        with self.assertRaises(TypeError):
            pickle.dumps(parser)

        result = await _worker(parser)._parse(parser, b"body", "x.txt")

        self.assertEqual(result, [])

    async def test_explicit_executor_path_is_preserved(self) -> None:
        """An explicit executor still uses run_in_executor."""
        parser = _UnpicklableParser()
        executor = MagicMock()
        future: Future[list[Section]] = Future()
        future.set_result([])
        executor.submit.return_value = future
        worker = IndexWorker(
            storage=object(),  # type: ignore[arg-type]
            blob_store=object(),  # type: ignore[arg-type]
            knowledge_base_manager=object(),  # type: ignore[arg-type]
            parsers=[parser],
            node_id="test-node",
            parser_executor=executor,
        )

        with patch(
            "agentscope.app._service._index_worker.asyncio.to_thread",
            side_effect=AssertionError(
                "explicit executor used asyncio.to_thread",
            ),
        ):
            result = await worker._parse(parser, b"body", "x.txt")

        self.assertEqual(result, [])
        executor.submit.assert_called_once_with(
            _run_parser_sync,
            parser,
            b"body",
            "x.txt",
        )

    async def test_default_path_does_not_block_event_loop(self) -> None:
        """Synchronous parser work runs outside the event-loop thread."""
        parser = _BlockingParser()
        loop = asyncio.get_running_loop()
        started = asyncio.Event()

        def watch_parser_start() -> None:
            parser.started.wait()
            loop.call_soon_threadsafe(started.set)

        watcher = threading.Thread(target=watch_parser_start, daemon=True)
        watcher.start()
        task = asyncio.create_task(
            _worker(parser)._parse(parser, b"body", "x.txt"),
        )
        try:
            await started.wait()

            probe = asyncio.Event()

            async def event_loop_probe() -> None:
                probe.set()

            await asyncio.create_task(event_loop_probe())
            self.assertTrue(probe.is_set())
            self.assertFalse(task.done())

            parser.release.set()
            self.assertEqual(await task, [])
        finally:
            parser.release.set()
            if not task.done():
                await task
            watcher.join()

    async def test_parser_exception_is_propagated(self) -> None:
        """Parser failures remain visible to the indexing pipeline."""
        parser = _FailingParser()

        with self.assertRaisesRegex(RuntimeError, "parser boom"):
            await _worker(parser)._parse(parser, b"body", "x.txt")
