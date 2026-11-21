# -*- coding: utf-8 -*-
"""TrajectoryMiddleware capturing rollouts and verifier rewards."""

import asyncio
import inspect
import json
import math
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncGenerator,
    Awaitable,
    Callable,
    Sequence,
    Union,
    cast,
)

import aioitertools

from .._base import MiddlewareBase
from ..._logging import logger
from ..._utils._common import _generate_id, _generate_timestamp
from ...event import (
    ExternalExecutionResultEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ...message import Msg, ToolCallBlock, ToolResultState
from ...model import ChatModelBase
from ...tool import ToolChunk, ToolResponse
from ...types import ReplyFinishedReason
from ._schema import (
    CompactionRecord,
    ModelStepRecord,
    RewardRecord,
    StepUsage,
    ToolExecutionRecord,
    TrajectoryRecord,
    TrajectoryStatus,
    sanitize_json_value,
    serialize_blocks,
    serialize_message,
)
from ._sink import InMemoryTrajectorySink, TrajectorySink

if TYPE_CHECKING:
    from ...agent import Agent
    from ...model import ChatResponse

TrajectoryVerifier = Callable[
    [TrajectoryRecord],
    Union[
        RewardRecord,
        float,
        Awaitable[Union[RewardRecord, float]],
    ],
]


@dataclass
class _ActiveTrajectory:
    """Mutable in-flight rollout state bound per-task via ContextVar."""

    trajectory_id: str
    session_id: str
    reply_id: str
    agent_name: str
    system_prompt: str
    input_messages: list[dict[str, Any]]
    started_mono: float
    started_at: str
    metadata: dict[str, Any]
    segment_index: int = 0
    steps: list[ModelStepRecord] = field(default_factory=list)
    compactions: list[CompactionRecord] = field(default_factory=list)
    orphan_tool_executions: list[ToolExecutionRecord] = field(
        default_factory=list,
    )
    output_message: dict[str, Any] | None = None
    finished_reason: str | None = None
    hitl_pending: bool = False
    external_pending: bool = False
    error: str | None = None


_ACTIVE_TRAJECTORY: ContextVar[_ActiveTrajectory | None] = ContextVar(
    "_ACTIVE_TRAJECTORY",
    default=None,
)


def _extract_usage(usage: Any) -> StepUsage | None:
    """Convert an AgentScope ``ChatUsage`` object into :class:`StepUsage`."""
    if usage is None:
        return None
    return StepUsage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_input_tokens=int(getattr(usage, "cache_input_tokens", 0) or 0),
        cache_creation_input_tokens=int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0,
        ),
        time_sec=float(getattr(usage, "time", 0.0) or 0.0),
    )


def _parse_tool_input(raw_input: Any) -> Any:
    """Parse JSON string tool inputs when valid, else keep original."""
    if not isinstance(raw_input, str):
        return sanitize_json_value(raw_input)
    try:
        return sanitize_json_value(json.loads(raw_input))
    except (json.JSONDecodeError, TypeError, ValueError):
        return raw_input


def _aggregate_usage(steps: list[ModelStepRecord]) -> StepUsage:
    """Sum token usage across all recorded model calls."""
    total = StepUsage()
    for step in steps:
        if step.usage is None:
            continue
        total.input_tokens += step.usage.input_tokens
        total.output_tokens += step.usage.output_tokens
        total.cache_input_tokens += step.usage.cache_input_tokens
        total.cache_creation_input_tokens += (
            step.usage.cache_creation_input_tokens
        )
        total.time_sec += step.usage.time_sec
    return total


def _resolve_status(active: _ActiveTrajectory) -> TrajectoryStatus:
    """Map observed events and errors into a canonical trajectory status."""
    reason = active.finished_reason
    if reason == ReplyFinishedReason.ERROR or active.error is not None:
        return "error"
    if reason == ReplyFinishedReason.INTERRUPTED:
        return "interrupted"
    if reason == ReplyFinishedReason.EXCEED_MAX_ITERS:
        return "exceed_max_iters"
    if active.hitl_pending:
        return "paused_hitl"
    if active.external_pending:
        return "paused_external"
    if reason is None and active.output_message is None:
        return "interrupted"
    return "completed"


def _apply_tool_item_to_record(
    record: ToolExecutionRecord,
    item: Any,
) -> None:
    """Update a tool record from terminal ToolChunk or ToolResponse."""
    if isinstance(item, (ToolResponse, ToolChunk)):
        state_str = str(item.state)
        record.state = state_str
        record.is_error = state_str in {
            ToolResultState.ERROR,
            ToolResultState.DENIED,
            ToolResultState.INTERRUPTED,
        }
        record.output = sanitize_json_value(item.content)


async def _observe_model_stream(
    stream: AsyncGenerator["ChatResponse", None],
    step: ModelStepRecord,
    started_mono: float,
) -> AsyncGenerator["ChatResponse", None]:
    """Forward streamed model chunks while recording final blocks and usage."""
    last_chunk: Any = None
    fallback_blocks: list[dict[str, Any]] = []
    try:
        async for chunk in aioitertools.iter(stream):
            last_chunk = chunk
            content = getattr(chunk, "content", None)
            if content:
                fallback_blocks = serialize_blocks(content)
            yield chunk
    except (GeneratorExit, asyncio.CancelledError):
        step.error = None
        raise
    except BaseException as exc:
        step.error = str(exc) or type(exc).__name__
        raise
    finally:
        step.duration_sec = time.monotonic() - started_mono
        step.finished_at = _generate_timestamp()
        if last_chunk is not None:
            content = getattr(last_chunk, "content", None)
            step.response_content = (
                serialize_blocks(content) if content else fallback_blocks
            )
            step.usage = _extract_usage(getattr(last_chunk, "usage", None))


class TrajectoryMiddleware(MiddlewareBase):
    """Middleware capturing RL/eval-ready trajectories and verifier rewards.

    Hooks ``on_reply``, ``on_model_call``, ``on_acting``, and
    ``on_compress_context`` cleanly using per-task ``ContextVar`` scoping
    without holding context tokens across generator ``yield`` boundaries.

    Example::

        from agentscope.middleware import (
            JsonlTrajectorySink,
            TrajectoryMiddleware,
        )

        trajectory_mw = TrajectoryMiddleware(
            sinks=[JsonlTrajectorySink("rollouts.jsonl")],
            verifiers=[
                lambda traj: 1.0 if traj.status == "completed" else 0.0,
            ],
        )
        agent = Agent(..., middlewares=[trajectory_mw])
    """

    def __init__(
        self,
        sinks: Sequence[TrajectorySink] | None = None,
        verifiers: Sequence[TrajectoryVerifier] | None = None,
        *,
        capture_tools: bool = False,
        metadata: dict[str, Any] | None = None,
        raise_on_verifier_error: bool = False,
        enabled: bool = True,
    ) -> None:
        """Initialize ``TrajectoryMiddleware``.

        Args:
            sinks (`Sequence[TrajectorySink] | None`, optional):
                Destinations receiving each completed ``TrajectoryRecord``.
                Defaults to a single ``InMemoryTrajectorySink``.
            verifiers (`Sequence[TrajectoryVerifier] | None`, optional):
                Sync or async verifier functions evaluated on each completed
                ``TrajectoryRecord``, returning a float or ``RewardRecord``.
            capture_tools (`bool`, defaults to `False`):
                Whether to store tool schemas on each ``ModelStepRecord``.
            metadata (`dict[str, Any] | None`, optional):
                Default metadata copied into each ``TrajectoryRecord``.
            raise_on_verifier_error (`bool`, defaults to `False`):
                If ``True``, propagate verifier exceptions. If ``False``,
                record errors under ``trajectory.metadata["verifier_errors"]``.
            enabled (`bool`, defaults to `True`):
                When ``False``, every hook immediately delegates with minimal
                overhead.
        """
        if sinks is None:
            self._in_memory_sink: InMemoryTrajectorySink | None = (
                InMemoryTrajectorySink()
            )
            self._sinks: list[TrajectorySink] = [self._in_memory_sink]
        else:
            self._sinks = list(sinks)
            self._in_memory_sink = next(
                (
                    sink
                    for sink in self._sinks
                    if isinstance(sink, InMemoryTrajectorySink)
                ),
                None,
            )

        self.verifiers: list[TrajectoryVerifier] = list(verifiers or [])
        self.capture_tools = capture_tools
        self.default_metadata: dict[str, Any] = dict(metadata or {})
        self.raise_on_verifier_error = raise_on_verifier_error
        self.enabled = enabled
        self.last_trajectory: TrajectoryRecord | None = None

    @property
    def trajectories(self) -> list[TrajectoryRecord]:
        """Return trajectories buffered by the active in-memory sink."""
        if self._in_memory_sink is None:
            return []
        return self._in_memory_sink.trajectories

    async def _run_verifiers(self, record: TrajectoryRecord) -> None:
        """Evaluate configured verifiers and populate ``record.rewards``."""
        verifier_errors: list[dict[str, str]] = []
        for idx, verifier in enumerate(self.verifiers):
            verifier_name = getattr(
                verifier,
                "__name__",
                type(verifier).__name__,
            )
            if verifier_name == "<lambda>":
                verifier_name = f"verifier_{idx}"
            try:
                outcome = verifier(record)
                if inspect.isawaitable(outcome):
                    outcome = await outcome
                if isinstance(outcome, RewardRecord):
                    record.rewards.append(outcome)
                elif isinstance(outcome, (int, float)) and not isinstance(
                    outcome,
                    bool,
                ):
                    record.rewards.append(
                        RewardRecord(
                            name=verifier_name,
                            score=float(outcome),
                        ),
                    )
                else:
                    raise TypeError(
                        "Verifier must return RewardRecord or float, "
                        f"got {type(outcome).__name__}",
                    )
            except Exception as exc:
                if self.raise_on_verifier_error:
                    raise
                logger.warning(
                    "[TrajectoryMiddleware] Verifier %s failed: %s",
                    verifier_name,
                    exc,
                )
                verifier_errors.append(
                    {
                        "verifier": verifier_name,
                        "error": str(exc) or type(exc).__name__,
                    },
                )

        if verifier_errors:
            record.metadata["verifier_errors"] = verifier_errors

        finite_scores = [
            float(r.score)
            for r in record.rewards
            if math.isfinite(float(r.score))
        ]
        if finite_scores:
            record.total_reward = sum(finite_scores)

    def _build_record(
        self,
        agent: "Agent",
        active: _ActiveTrajectory,
    ) -> TrajectoryRecord:
        """Build a TrajectoryRecord from active rollout state."""
        if active.orphan_tool_executions:
            active.metadata["unattached_tool_executions"] = [
                t.to_dict() for t in active.orphan_tool_executions
            ]
        reply_id = active.reply_id or agent.state.reply_id or ""
        return TrajectoryRecord(
            trajectory_id=active.trajectory_id,
            session_id=active.session_id,
            reply_id=reply_id,
            agent_name=active.agent_name,
            system_prompt=active.system_prompt,
            input_messages=active.input_messages,
            output_message=active.output_message,
            steps=active.steps,
            compactions=active.compactions,
            status=_resolve_status(active),
            error=active.error,
            total_usage=_aggregate_usage(active.steps),
            duration_sec=time.monotonic() - active.started_mono,
            started_at=active.started_at,
            finished_at=_generate_timestamp(),
            metadata=active.metadata,
        )

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Capture one full reply rollout and emit it to verifiers/sinks."""
        if not self.enabled:
            async for item in next_handler(**input_kwargs):
                yield item
            return

        inputs = input_kwargs.get("inputs")
        meta = dict(self.default_metadata)
        input_messages: list[dict[str, Any]] = []
        orphan_tools: list[ToolExecutionRecord] = []

        if isinstance(inputs, Msg):
            input_messages = [serialize_message(inputs)]
        elif isinstance(inputs, list):
            input_messages = [serialize_message(m) for m in inputs]
        elif isinstance(inputs, ExternalExecutionResultEvent):
            meta["incoming_event"] = str(inputs.type)
            now_ts = _generate_timestamp()
            for res in inputs.execution_results:
                state_str = str(res.state)
                orphan_tools.append(
                    ToolExecutionRecord(
                        tool_call_id=res.id,
                        tool_name=res.name,
                        tool_input=None,
                        output=sanitize_json_value(res.output),
                        state=state_str,
                        is_error=state_str
                        in {
                            ToolResultState.ERROR,
                            ToolResultState.DENIED,
                            ToolResultState.INTERRUPTED,
                        },
                        is_external=True,
                        started_at=now_ts,
                        finished_at=now_ts,
                    ),
                )
        elif isinstance(inputs, (UserConfirmResultEvent, UserInterruptEvent)):
            meta["incoming_event"] = str(inputs.type)

        active = _ActiveTrajectory(
            trajectory_id=_generate_id(),
            session_id=agent.state.session_id,
            reply_id=agent.state.reply_id or "",
            agent_name=agent.name,
            system_prompt=getattr(agent, "_system_prompt", ""),
            input_messages=input_messages,
            started_mono=time.monotonic(),
            started_at=_generate_timestamp(),
            metadata=meta,
            orphan_tool_executions=orphan_tools,
        )

        gen = next_handler(**input_kwargs)
        try:
            while True:
                token = _ACTIVE_TRAJECTORY.set(active)
                try:
                    item = await anext(gen)
                except StopAsyncIteration:
                    break
                finally:
                    _ACTIVE_TRAJECTORY.reset(token)

                if isinstance(item, ReplyStartEvent):
                    active.reply_id = item.reply_id
                elif isinstance(item, RequireUserConfirmEvent):
                    active.hitl_pending = True
                elif isinstance(item, RequireExternalExecutionEvent):
                    active.external_pending = True
                elif isinstance(item, ReplyEndEvent):
                    active.finished_reason = str(item.finished_reason)
                    if item.error is not None:
                        active.error = item.error.message
                elif isinstance(item, Msg):
                    active.output_message = serialize_message(item)

                yield item
        except (GeneratorExit, asyncio.CancelledError):
            if active.finished_reason is None:
                active.finished_reason = ReplyFinishedReason.INTERRUPTED
            raise
        except BaseException as exc:
            active.error = str(exc) or type(exc).__name__
            active.finished_reason = ReplyFinishedReason.ERROR
            raise
        finally:
            record = self._build_record(agent, active)
            await self._run_verifiers(record)
            self.last_trajectory = record
            for sink in self._sinks:
                await sink.write(record)

    async def on_model_call(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[
            ...,
            Awaitable[
                Union["ChatResponse", AsyncGenerator["ChatResponse", None]]
            ],
        ],
    ) -> Union["ChatResponse", AsyncGenerator["ChatResponse", None]]:
        """Record one reasoning model call inside the active trajectory."""
        if not self.enabled:
            return await next_handler(**input_kwargs)

        active = _ACTIVE_TRAJECTORY.get()
        if active is None:
            return await next_handler(**input_kwargs)

        model = input_kwargs.get("current_model")
        model_name = (
            getattr(model, "model", "")
            if isinstance(model, ChatModelBase)
            else getattr(model, "model", model.__class__.__name__)
        )
        raw_messages = input_kwargs.get("messages") or []
        tools_payload = (
            sanitize_json_value(input_kwargs.get("tools"))
            if self.capture_tools and input_kwargs.get("tools") is not None
            else None
        )

        started_mono = time.monotonic()
        step = ModelStepRecord(
            step_index=len(active.steps),
            segment_index=active.segment_index,
            model_name=str(model_name),
            input_messages=[serialize_message(m) for m in raw_messages],
            tools=tools_payload,
            started_at=_generate_timestamp(),
        )
        active.steps.append(step)

        try:
            result = await next_handler(**input_kwargs)
        except BaseException as exc:
            step.error = str(exc) or type(exc).__name__
            step.duration_sec = time.monotonic() - started_mono
            step.finished_at = _generate_timestamp()
            raise

        if inspect.isasyncgen(result):
            return _observe_model_stream(
                cast(AsyncGenerator["ChatResponse", None], result),
                step,
                started_mono,
            )

        step.duration_sec = time.monotonic() - started_mono
        step.finished_at = _generate_timestamp()
        step.response_content = serialize_blocks(
            getattr(result, "content", None),
        )
        step.usage = _extract_usage(getattr(result, "usage", None))
        return result

    async def on_acting(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Record one tool execution inside the latest model step."""
        if not self.enabled:
            async for item in next_handler(**input_kwargs):
                yield item
            return

        active = _ACTIVE_TRAJECTORY.get()
        tool_call = input_kwargs.get("tool_call")
        if active is None or not isinstance(tool_call, ToolCallBlock):
            async for item in next_handler(**input_kwargs):
                yield item
            return

        started_mono = time.monotonic()
        record = ToolExecutionRecord(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            tool_input=_parse_tool_input(tool_call.input),
            started_at=_generate_timestamp(),
        )
        if active.steps:
            active.steps[-1].tool_executions.append(record)
        else:
            active.orphan_tool_executions.append(record)

        last_item: Any = None
        gen = next_handler(**input_kwargs)
        try:
            async for item in aioitertools.iter(gen):
                last_item = item
                yield item
        except (GeneratorExit, asyncio.CancelledError):
            record.state = str(ToolResultState.INTERRUPTED)
            record.is_error = True
            raise
        except BaseException as exc:
            record.state = str(ToolResultState.ERROR)
            record.is_error = True
            record.error = str(exc) or type(exc).__name__
            raise
        finally:
            record.duration_sec = time.monotonic() - started_mono
            record.finished_at = _generate_timestamp()
            if last_item is not None:
                _apply_tool_item_to_record(record, last_item)

    async def on_compress_context(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., Awaitable[None]],
    ) -> None:
        """Record context compaction boundaries and advance segment index."""
        if not self.enabled:
            await next_handler(**input_kwargs)
            return

        active = _ACTIVE_TRAJECTORY.get()
        if active is None:
            await next_handler(**input_kwargs)
            return

        before_count = len(agent.state.context)
        before_summary = agent.state.summary
        started_mono = time.monotonic()
        await next_handler(**input_kwargs)
        after_count = len(agent.state.context)
        after_summary = agent.state.summary

        if after_count != before_count or after_summary != before_summary:
            seg_before = active.segment_index
            seg_after = seg_before + 1
            active.segment_index = seg_after
            active.compactions.append(
                CompactionRecord(
                    after_step_index=len(active.steps) - 1,
                    before_message_count=before_count,
                    after_message_count=after_count,
                    segment_before=seg_before,
                    segment_after=seg_after,
                    duration_sec=time.monotonic() - started_mono,
                    timestamp=_generate_timestamp(),
                ),
            )
