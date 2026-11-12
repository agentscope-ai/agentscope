# -*- coding: utf-8 -*-
"""Bounded worker processes using the selected backend's actual boundary."""

import asyncio
import codecs
import json
import os
import signal
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable, Self

try:
    import psutil
except ImportError:
    psutil = None

from ._config import CodeModeConfig
from ..tool import BackendBase

_CLEANUP_TIMEOUT = 2.0


class WorkerProcess:
    """Own process startup, RPC pipes, resource sampling and termination."""

    def __init__(
        self,
        backend: BackendBase,
        cwd: str,
        python: str,
        config: CodeModeConfig,
        *,
        on_output: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        if psutil is None:
            raise ImportError("Code Mode requires agentscope[code-mode].")
        self.backend = backend
        self.cwd = cwd
        self.python = python
        self.config = config
        self.on_output = on_output
        self.process: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()
        self._closed = False
        self._root = None
        self._children: dict[int, Any] = {}
        self._tree_lock = threading.Lock()
        self._output_task: asyncio.Task | None = None
        self._output_error: Exception | None = None

    async def __aenter__(self) -> Self:
        source = await asyncio.to_thread(
            Path(__file__).with_name("_worker.py").read_text,
            encoding="utf-8",
        )
        startup = asyncio.create_task(
            self.backend.start_process(
                [
                    self.python,
                    "-I",
                    "-S",
                    "-u",
                    "-c",
                    source,
                    str(self.config.max_message_bytes),
                    "1" if self.backend.isolates_process_tree is True else "0",
                ],
                cwd=self.cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=self.config.max_message_bytes,
            ),
        )
        try:
            self.process = await asyncio.shield(startup)
            self._root = psutil.Process(self.process.pid)
            self._output_task = asyncio.create_task(self._capture_output())
        except BaseException:
            try:
                self.process = await startup
            finally:
                await self.close()
            raise
        return self

    @staticmethod
    async def _discard(stream: asyncio.StreamReader | None) -> None:
        """Drain a pipe without retaining its contents in host memory."""
        if stream is not None:
            while await stream.read(4096):
                pass

    async def _capture_output(self) -> None:
        """Decode ordinary output incrementally, separately from RPC."""
        assert self.process is not None and self.process.stderr is not None
        stream = self.process.stderr
        if self.on_output is None:
            await self._discard(stream)
            return
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while data := await stream.read(4096):
                await self.on_output(decoder.decode(data))
            await self.on_output(decoder.decode(b"", final=True))
        except Exception as exc:
            self._output_error = exc
            # Keep the pipe flowing until the watchdog triggers shutdown.
            await self._discard(stream)

    def _encode(self, packet: dict) -> bytes:
        data = json.dumps(packet, ensure_ascii=False).encode("utf-8") + b"\n"
        if len(data) > self.config.max_message_bytes:
            raise ValueError("Code Mode message limit exceeded")
        return data

    async def send(self, packet: dict) -> None:
        """Serialize large payloads off the event loop."""
        data = await asyncio.to_thread(self._encode, packet)
        assert self.process is not None and self.process.stdin is not None
        async with self.lock:
            self.process.stdin.write(data)
            await self.process.stdin.drain()

    async def receive(self) -> dict:
        """Read one bounded packet; raw process crashes are script errors."""
        assert self.process is not None and self.process.stdout is not None
        line = await self.process.stdout.readline()
        if not line:
            raise RuntimeError("Code Mode worker exited before completion")
        if len(line) > self.config.max_message_bytes:
            raise ValueError("Code Mode message limit exceeded")
        packet = await asyncio.to_thread(json.loads, line)
        if not isinstance(packet, dict):
            raise ValueError("Invalid Code Mode packet")
        return packet

    def _process_tree(self) -> list:
        """Retain process identities even after descendants are reparented."""
        assert self.process is not None
        with self._tree_lock:
            try:
                if self._root is None:
                    self._root = psutil.Process(self.process.pid)
                for child in self._root.children(recursive=True):
                    self._children[child.pid] = child
            except psutil.NoSuchProcess:
                pass
            return list(self._children.values())

    def _sample(self) -> None:
        children = self._process_tree()
        processes = ([self._root] if self._root is not None else []) + children
        memory = 0
        cpu = 0.0
        for process in processes:
            try:
                if not process.is_running():
                    continue
                memory += process.memory_info().rss
                cpu += sum(process.cpu_times()[:2])
            except psutil.NoSuchProcess:
                continue
        if memory > self.config.max_memory_bytes:
            raise RuntimeError("Code Mode memory limit exceeded")
        if cpu > self.config.max_cpu_seconds:
            raise RuntimeError("Code Mode CPU limit exceeded")

    async def watch(self) -> None:
        """Apply sampled process-tree resource limits without blocking I/O."""
        while True:
            if self._output_error is not None:
                raise self._output_error
            try:
                await asyncio.to_thread(self._sample)
            except (psutil.AccessDenied, PermissionError) as exc:
                raise RuntimeError(
                    "Code Mode process monitoring unavailable: access denied",
                ) from exc
            await asyncio.sleep(0.1)

    def _kill_tree(self) -> None:
        assert self.process is not None
        children = []
        try:
            try:
                children = self._process_tree()
            except (psutil.AccessDenied, PermissionError):
                with self._tree_lock:
                    children = list(self._children.values())
            for process in reversed(children):
                try:
                    # psutil checks the retained identity before killing,
                    # preventing a reused PID from targeting another process.
                    process.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        finally:
            if os.name != "nt":
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif self.process.returncode is None:
                self.process.kill()
        psutil.wait_procs(children, timeout=1)

    async def close(self) -> None:
        """Bound termination, pipe draining and final process reaping."""
        if self.process is not None and not self._closed:
            try:
                async with asyncio.timeout(_CLEANUP_TIMEOUT):
                    # This bounds waiting, not the thread's lifetime. The
                    # thread may still finish after transport.close() below.
                    await asyncio.to_thread(self._kill_tree)
                    if self.process.stdin is not None:
                        self.process.stdin.close()
                    await asyncio.gather(
                        self._discard(self.process.stdout),
                        self._output_task
                        if self._output_task is not None
                        else self._discard(self.process.stderr),
                    )
            except TimeoutError as exc:
                raise RuntimeError(
                    "Code Mode process cleanup timed out",
                ) from exc
            finally:
                # Process has no public API to close inherited pipe readers.
                # pylint: disable-next=protected-access
                self.process._transport.close()
                if self._output_task is not None:
                    self._output_task.cancel()
                try:
                    async with asyncio.timeout(_CLEANUP_TIMEOUT):
                        await self.process.wait()
                        if self._output_task is not None:
                            await asyncio.gather(
                                self._output_task,
                                return_exceptions=True,
                            )
                    self._closed = True
                except TimeoutError as exc:
                    raise RuntimeError(
                        "Code Mode process cleanup timed out",
                    ) from exc
            if self._output_error is not None:
                raise self._output_error

    async def __aexit__(self, *_args: Any) -> None:
        await self.close()
