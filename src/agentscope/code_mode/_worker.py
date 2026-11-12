# -*- coding: utf-8 -*-
"""Standalone standard-library worker, transported as Python source."""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import os
import sys
import threading
import traceback
from typing import Any, Callable, Awaitable

if os.name == "nt":
    import ctypes
    import msvcrt


def deny_process_creation(event: str, _arguments: tuple) -> None:
    """Guard standard APIs, not an OS security boundary.

    Native calls via ctypes or direct _posixsubprocess calls can bypass this.
    """
    if event in {
        "subprocess.Popen",
        "_winapi.CreateProcess",
        "os.system",
        "os.fork",
        "os.forkpty",
        "os.posix_spawn",
        "os.spawn",
        "os.exec",
        "os.startfile",
        "os.startfile/2",
    }:
        raise PermissionError(
            "Direct process creation requires a backend with process-tree "
            "isolation. Use an enabled native tool through tools instead.",
        )


class Tools:
    """Resolve aliases dynamically so newly activated groups are usable."""

    def __init__(self, worker: Worker) -> None:
        self._worker = worker

    def __getattr__(self, name: str) -> Callable[..., Awaitable[Any]]:
        async def invoke(**arguments: Any) -> Any:
            return await self._worker.call("tool", name, arguments)

        return invoke


class Worker:
    """Multiplex asynchronous tool calls over a bounded JSON line channel."""

    def __init__(self) -> None:
        self.limit = int(sys.argv[1])
        self.input = os.fdopen(os.dup(sys.stdin.fileno()), "rb")
        self.output = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
        os.set_inheritable(self.input.fileno(), False)
        os.set_inheritable(self.output.fileno(), False)
        # Reserve the original pipes for RPC. Children and native writes use
        # the separate stderr pipe; inherited stdin must not consume replies.
        with open(os.devnull, "rb") as null:
            os.dup2(null.fileno(), 0)
        os.dup2(2, 1)
        if os.name == "nt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            set_handle = kernel.SetStdHandle
            set_handle.argtypes = [ctypes.c_ulong, ctypes.c_void_p]
            set_handle.restype = ctypes.c_int
            for number, descriptor in ((-10, 0), (-11, 1), (-12, 2)):
                if not set_handle(number, msvcrt.get_osfhandle(descriptor)):
                    raise ctypes.WinError(ctypes.get_last_error())
        self.lock = threading.Lock()
        self.pending: dict[int, asyncio.Future] = {}
        self.cancellations: dict[int, asyncio.Future] = {}
        self.sequence = 0
        self.loop = asyncio.get_running_loop()
        self.shutdown = asyncio.Event()

    def emit(self, packet: dict) -> None:
        """Write a single complete packet under the protocol lock."""
        data = json.dumps(packet, ensure_ascii=False).encode("utf-8") + b"\n"
        if len(data) > self.limit:
            raise ValueError("Code Mode message limit exceeded")
        with self.lock:
            self.output.write(data)
            self.output.flush()

    def resolve(self, packet: dict) -> None:
        """Resolve an RPC future on the asyncio loop thread."""
        if packet.get("cancel_ack"):
            future = self.cancellations.get(packet["id"])
            if future is not None and not future.done():
                future.set_result(None)
            return
        future = self.pending.get(packet["id"])
        if future is None or future.done():
            return
        if packet.get("cancelled"):
            future.cancel()
        elif "error" in packet:
            future.set_exception(RuntimeError(packet["error"]))
        else:
            future.set_result(packet["result"])

    def disconnect(self) -> None:
        """Unblock every caller when the host closes its pipe."""
        self.shutdown.set()
        for future in (*self.pending.values(), *self.cancellations.values()):
            if not future.done():
                future.set_exception(RuntimeError("Code Mode disconnected"))

    def read_responses(self) -> None:
        """Keep blocking reads off the event loop."""
        try:
            while line := self.input.readline(self.limit + 1):
                if len(line) > self.limit:
                    break
                self.loop.call_soon_threadsafe(self.resolve, json.loads(line))
        except (OSError, ValueError, RuntimeError):
            pass
        finally:
            if not self.loop.is_closed():
                self.loop.call_soon_threadsafe(self.disconnect)

    async def cancel(self, call_id: int) -> None:
        """Wait for host cleanup even if the caller is cancelled again."""
        acknowledgement = self.loop.create_future()
        self.cancellations[call_id] = acknowledgement
        try:
            self.emit({"kind": "cancel", "id": call_id})
            while not acknowledgement.done():
                try:
                    await asyncio.shield(acknowledgement)
                except asyncio.CancelledError:
                    continue
            acknowledgement.result()
        finally:
            self.cancellations.pop(call_id, None)

    async def call(
        self,
        kind: str,
        name: str = "",
        arguments: dict | None = None,
    ) -> Any:
        """Await one host operation without blocking other RPCs."""
        self.sequence += 1
        call_id = self.sequence
        future = self.loop.create_future()
        self.pending[call_id] = future
        try:
            self.emit(
                {
                    "kind": kind,
                    "id": call_id,
                    "name": name,
                    "arguments": arguments or {},
                },
            )
            return await future
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                await self.cancel(call_id)
            raise
        finally:
            self.pending.pop(call_id, None)

    def text(self, value: Any) -> None:
        """Select text or JSON for model output."""
        if isinstance(value, str) and not value:
            return
        self.emit({"kind": "text", "value": value})

    def write(self, value: str) -> int:
        """Route print and stderr through selected output."""
        if value:
            self.text(value)
        return len(value)

    def flush(self) -> None:
        """Packets are already flushed by emit."""

    async def run(self) -> None:
        """Execute one script with top-level await and fresh globals."""
        raw = self.input.readline(self.limit + 1)
        if len(raw) > self.limit:
            raise ValueError("Code Mode message limit exceeded")
        initial = json.loads(raw)
        threading.Thread(target=self.read_responses, daemon=True).start()
        sys.stdout = sys.stderr = self  # type: ignore[assignment]

        async def search_tools(query: str = "") -> list[dict]:
            return await self.call("search", query)

        async def describe_tool(name: str) -> dict:
            return await self.call("describe", name)

        def media(kind: str) -> Callable[[dict], None]:
            return lambda block: self.emit({"kind": kind, "value": block})

        namespace: dict[str, Any] = {
            "__name__": "__code_mode__",
            "tools": Tools(self),
            "ALL_TOOLS": initial["tools"],
            "search_tools": search_tools,
            "describe_tool": describe_tool,
            "text": self.text,
            "image": media("image"),
            "audio": media("audio"),
            "video": media("video"),
        }
        try:
            tree = ast.parse(initial["code"], filename="<run_code>")
            if tree.body and isinstance(tree.body[-1], ast.Expr):
                expression = tree.body[-1]
                tree.body[-1] = ast.copy_location(
                    ast.Assign(
                        targets=[ast.Name("_code_result", ast.Store())],
                        value=expression.value,
                    ),
                    expression,
                )
                ast.fix_missing_locations(tree)
            compiled = compile(
                tree,
                "<run_code>",
                "exec",
                flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
            )
            result = eval(compiled, namespace)  # pylint: disable=eval-used
            if inspect.isawaitable(result):
                await result
            if namespace.get("_code_result") is not None:
                self.text(namespace["_code_result"])
            self.emit({"kind": "done"})
        except BaseException as exc:  # pylint: disable=broad-except
            self.emit(
                {
                    "kind": "done",
                    "error": traceback.format_exc()[-2048:],
                    "cancelled": isinstance(exc, asyncio.CancelledError),
                },
            )
        # Keep descendants attached until the host has collected and killed
        # them. Sending done must not race with process-tree discovery.
        await self.shutdown.wait()


async def main() -> None:
    """Create the worker only after its event loop is running."""
    if sys.argv[2] != "1":
        sys.addaudithook(deny_process_creation)
    await Worker().run()


if __name__ == "__main__":
    asyncio.run(main())
