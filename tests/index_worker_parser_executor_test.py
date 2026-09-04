"""Regression tests for IndexWorker parser execution."""
import asyncio
import pickle
import threading
from typing import ClassVar
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.app._service._index_worker import IndexWorker
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
        self.release.wait(timeout=2.0)
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
    """Default parser execution must be thread-based and observable."""

    async def test_default_path_accepts_unpicklable_parser(self) -> None:
        """A parser with a threading.Lock works without a process pool."""
        parser = _UnpicklableParser()
        with self.assertRaises(TypeError):
            pickle.dumps(parser)

        result = await _worker(parser)._parse(parser, b"body", "x.txt")

        self.assertEqual(result, [])

    async def test_default_path_does_not_block_event_loop(self) -> None:
        """Synchronous parser work runs outside the event-loop thread."""
        parser = _BlockingParser()
        task = asyncio.create_task(
            _worker(parser)._parse(parser, b"body", "x.txt"),
        )
        release_timer = threading.Timer(0.5, parser.release.set)
        release_timer.start()
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 1.0
            while not parser.started.is_set():
                if loop.time() >= deadline:
                    self.fail("Parser did not start.")
                await asyncio.sleep(0.01)
            self.assertFalse(task.done())
            parser.release.set()
            self.assertEqual(
                await asyncio.wait_for(task, timeout=1.0),
                [],
            )
        finally:
            release_timer.cancel()
            parser.release.set()
            if not task.done():
                await task

    async def test_parser_exception_is_propagated(self) -> None:
        """Parser failures remain visible to the indexing pipeline."""
        parser = _FailingParser()

        with self.assertRaisesRegex(RuntimeError, "parser boom"):
            await _worker(parser)._parse(parser, b"body", "x.txt")
