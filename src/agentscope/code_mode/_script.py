# -*- coding: utf-8 -*-
"""Host-side RPC and selected output for one Python script."""

import asyncio
import hashlib
import json
from functools import partial
from typing import Any, TYPE_CHECKING

from ._process import WorkerProcess
from ..message import (
    Base64Source,
    DataBlock,
    TextBlock,
    ToolResultState,
    URLSource,
)
from ..state import AgentState
from ..tool import ToolChunk

if TYPE_CHECKING:
    from ._toolkit import CodeModeToolkit

_TRUNCATED = "\n[Code output truncated]"


def _source_key(source: Base64Source | URLSource) -> str:
    return hashlib.sha256(
        json.dumps(source.model_dump(mode="json"), sort_keys=True).encode(
            "utf-8",
        ),
    ).hexdigest()


class Script:
    """Keep pending calls, output budgets and media provenance per script."""

    def __init__(
        self,
        toolkit: "CodeModeToolkit",
        state: AgentState,
    ) -> None:
        self.toolkit = toolkit
        self.state = state
        self.output: list[TextBlock | DataBlock] = []
        self.calls: list[dict] = []
        self.pending: set[asyncio.Task] = set()
        self.requests: dict[int, asyncio.Task] = {}
        # Retain only IDs for late cancellation acknowledgements, not tasks.
        self.completed_requests: set[int] = set()
        self.allowed_tools = toolkit.allowed_tools
        self.sources: set[str] = set()
        # Reserve space for a visible marker even after many small outputs.
        self.remaining = toolkit.config.max_output_bytes - len(_TRUNCATED)
        self.truncated = False
        self._output_lock = asyncio.Lock()

    def _truncate_blocks(self) -> bool:
        """Reserve the final block for one marker; called under the lock."""
        if len(self.output) < self.toolkit.config.max_output_blocks - 1:
            return False
        self.output.append(TextBlock(text=_TRUNCATED))
        self.truncated = True
        self.remaining = 0
        return True

    async def definitions(self) -> dict[str, dict]:
        """Refresh activated groups, registrations and live MCP schemas."""
        definitions = await self.toolkit.definitions(
            self.state.tool_context.activated_groups,
        )
        return {
            name: definition
            for name, definition in definitions.items()
            if self.allowed_tools is None or name in self.allowed_tools
        }

    @staticmethod
    def summaries(definitions: dict[str, dict]) -> list[dict]:
        """Return discovery metadata without large parameter schemas."""
        return [
            {
                key: definition[key]
                for key in (
                    "name",
                    "python_name",
                    "description",
                )
            }
            for definition in definitions.values()
        ]

    async def text(self, value: Any) -> None:
        """Append ordered text, truncating once at the output byte budget."""
        if self.truncated or (isinstance(value, str) and not value):
            return
        rendered = (
            value
            if isinstance(value, str)
            else await asyncio.to_thread(
                json.dumps,
                value,
                ensure_ascii=False,
            )
        )
        encoded = await asyncio.to_thread(rendered.encode, "utf-8")
        async with self._output_lock:
            if self.truncated or self._truncate_blocks():
                return
            if len(encoded) > self.remaining:
                rendered = encoded[: self.remaining].decode(
                    "utf-8",
                    errors="ignore",
                )
                rendered += _TRUNCATED
                self.truncated = True
                self.remaining = 0
            else:
                self.remaining -= len(encoded)
            self.output.append(TextBlock(text=rendered))

    async def media(self, kind: str, value: dict) -> None:
        """Only forward inline media or a source observed in a tool result."""
        if self.truncated:
            return
        block = await asyncio.to_thread(DataBlock.model_validate, value)
        if not block.source.media_type.startswith(f"{kind}/"):
            raise ValueError(f"{kind}() requires {kind}/* media")
        source_key = await asyncio.to_thread(_source_key, block.source)
        if (
            block.source.type == "url"
            and block.source.url.scheme != "data"
            and source_key not in self.sources
        ):
            raise ValueError("Read media through an enabled tool first")
        encoded = await asyncio.to_thread(block.model_dump_json)
        size = len(encoded.encode("utf-8"))
        async with self._output_lock:
            if self.truncated or self._truncate_blocks():
                return
            if size > self.remaining:
                raise ValueError("Code media exceeds the output limit")
            self.remaining -= size
            self.output.append(block)

    async def invoke(self, name: str, arguments: dict) -> dict:
        """Invoke the agent's permission-aware native execution entry."""
        record = {"name": name, "state": "calling"}
        self.calls.append(record)
        try:
            if (
                self.allowed_tools is not None
                and name not in self.allowed_tools
            ):
                raise ValueError(f"Tool excluded by tool choice: {name}")
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be an object")
            tool = await self.toolkit.catalog.check_tool_available(
                name,
                self.state.tool_context.activated_groups,
            )
            async with self.toolkit.execution_slot(tool):
                executor = self.toolkit.executor
                assert executor is not None
                response = await executor(name, arguments)
            record["id"] = response.id
            record["state"] = response.state.value
            if response.state == ToolResultState.INTERRUPTED:
                raise asyncio.CancelledError()
            result = await asyncio.to_thread(response.model_dump, mode="json")
            if response.state != ToolResultState.SUCCESS:
                raise RuntimeError(json.dumps(result, ensure_ascii=False))
            for block in response.content:
                if isinstance(block, DataBlock):
                    key = await asyncio.to_thread(_source_key, block.source)
                    self.sources.add(key)
            return {
                key: result[key] for key in ("content", "metadata", "state")
            }
        except asyncio.CancelledError:
            record["state"] = "interrupted"
            raise
        except Exception:
            if record["state"] == "calling":
                record["state"] = "error"
            raise

    async def respond(self, worker: WorkerProcess, packet: dict) -> None:
        """Dispatch a worker request and always unblock its RPC future."""
        try:
            definitions = await self.toolkit.definitions(
                self.state.tool_context.activated_groups,
            )
            name = packet.get("name", "")
            if packet["kind"] == "search":
                terms = name.casefold().split()
                result = [
                    item
                    for item in self.summaries(definitions)
                    if (
                        self.allowed_tools is None
                        or item["name"] in self.allowed_tools
                    )
                    and all(
                        term in json.dumps(item).casefold() for term in terms
                    )
                ]
            else:
                definition = next(
                    (
                        item
                        for item in definitions.values()
                        if name in (item["name"], item["python_name"])
                    ),
                    None,
                )
                if definition is None:
                    raise ValueError(f"Unknown or unavailable tool: {name}")
                if (
                    self.allowed_tools is not None
                    and definition["name"] not in self.allowed_tools
                ):
                    raise ValueError(
                        f"Tool excluded by tool choice: {definition['name']}",
                    )
                result = definition
                if packet["kind"] == "tool":
                    result = await self.invoke(
                        definition["name"],
                        packet["arguments"],
                    )
            response = {"id": packet["id"], "result": result}
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            response = {"id": packet["id"], "cancelled": True}
        except Exception as exc:
            response = {
                "id": packet["id"],
                "error": f"{type(exc).__name__}: {exc}"[:2048],
            }
        try:
            await worker.send(response)
        except ValueError as exc:
            await worker.send({"id": packet["id"], "error": str(exc)})
        except (BrokenPipeError, ConnectionError):
            pass

    @staticmethod
    async def acknowledge_cancel(
        worker: WorkerProcess,
        call_id: int,
        task: asyncio.Task,
    ) -> None:
        """Confirm cancellation only after execution and cleanup finish."""
        await asyncio.gather(task, return_exceptions=True)
        await worker.send({"id": call_id, "cancel_ack": True})

    def task_done(self, task: asyncio.Task) -> None:
        """Release completed tasks and consume errors as final gather did."""
        self.pending.discard(task)
        if not task.cancelled():
            task.exception()

    def request_done(self, call_id: int, task: asyncio.Task) -> None:
        """Release task results while retaining bounded late-cancel IDs."""
        self.task_done(task)
        if self.requests.get(call_id) is task:
            del self.requests[call_id]
            self.completed_requests.add(call_id)

    async def receive(self, worker: WorkerProcess) -> None:
        """Receive outputs in order while allowing independent RPCs."""
        count = 0
        while True:
            packet = await worker.receive()
            kind = packet.get("kind")
            if kind in ("tool", "search", "describe"):
                count += 1
                if count > self.toolkit.config.max_calls:
                    raise RuntimeError("Code Mode host-call limit exceeded")
                task = asyncio.create_task(self.respond(worker, packet))
                self.pending.add(task)
                self.requests[packet["id"]] = task
                task.add_done_callback(
                    partial(self.request_done, packet["id"]),
                )
            elif kind == "cancel":
                target = self.requests.pop(packet["id"], None)
                if target is not None:
                    target.cancel()
                    acknowledgement = asyncio.create_task(
                        self.acknowledge_cancel(worker, packet["id"], target),
                    )
                    self.pending.add(acknowledgement)
                    acknowledgement.add_done_callback(self.task_done)
                elif packet["id"] in self.completed_requests:
                    self.completed_requests.remove(packet["id"])
                    await worker.send(
                        {"id": packet["id"], "cancel_ack": True},
                    )
            elif kind == "text":
                await self.text(packet["value"])
            elif kind in ("image", "audio", "video"):
                await self.media(kind, packet["value"])
            elif kind == "done":
                if packet.get("cancelled"):
                    raise asyncio.CancelledError()
                if packet.get("error"):
                    raise RuntimeError(packet["error"])
                return
            else:
                raise ValueError("Invalid Code Mode message kind")

    async def run(self, code: str) -> ToolChunk:
        """Run once, preserve selected partial output, and cancel leftovers."""
        status = ToolResultState.SUCCESS
        error = None
        deadline = asyncio.timeout(self.toolkit.config.timeout)
        try:
            async with deadline:
                definitions = await self.definitions()
                async with WorkerProcess(
                    self.toolkit.backend,
                    self.toolkit.cwd,
                    self.toolkit.python,
                    self.toolkit.config,
                    on_output=self.text,
                ) as worker:
                    try:
                        await worker.send(
                            {
                                "code": code,
                                "tools": self.summaries(definitions),
                            },
                        )
                        receiver = asyncio.create_task(self.receive(worker))
                        watchdog = asyncio.create_task(worker.watch())
                        self.pending.update((receiver, watchdog))
                        done, _ = await asyncio.wait(
                            (receiver, watchdog),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if watchdog in done:
                            await watchdog
                        await receiver
                    finally:
                        for task in self.pending:
                            task.cancel()
                        await asyncio.gather(
                            *self.pending,
                            return_exceptions=True,
                        )
        except TimeoutError:
            error = "Code Mode execution timed out"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:2048]
            if deadline.expired():
                error = f"Code Mode execution timed out; cleanup: {error}"
        if error:
            status = ToolResultState.ERROR
            await self.text(f"\nScript failed: {error}")
        return ToolChunk(
            content=self.output or [TextBlock(text="Script completed.")],
            state=status,
            metadata={
                "code_mode": {
                    "calls": self.calls,
                    "truncated": self.truncated,
                    "error": error,
                },
            },
        )
