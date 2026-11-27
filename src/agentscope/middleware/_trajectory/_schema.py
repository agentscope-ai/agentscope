# -*- coding: utf-8 -*-
"""Data structures and export helpers for recorded agent trajectories."""

import dataclasses
import json
import math
from dataclasses import dataclass, field
from typing import Any, Literal


def sanitize_json_value(value: Any) -> Any:
    """Recursively convert values into RFC 8259 JSON-safe primitives."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {
            str(key): sanitize_json_value(val) for key, val in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_json_value(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump(mode="json")
        except TypeError:
            dumped = value.model_dump()
        return sanitize_json_value(dumped)
    if hasattr(value, "to_dict"):
        return sanitize_json_value(value.to_dict())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return sanitize_json_value(dataclasses.asdict(value))
    return str(value)


def serialize_message(msg: Any) -> dict[str, Any]:
    """Serialize an AgentScope ``Msg`` or dict into a normalized record."""
    if isinstance(msg, dict):
        return {
            "id": str(msg.get("id", "")),
            "role": str(msg.get("role", "user")),
            "name": str(msg.get("name", "")),
            "content": sanitize_json_value(msg.get("content", [])),
            "metadata": sanitize_json_value(msg.get("metadata", {})),
        }
    return {
        "id": str(getattr(msg, "id", "")),
        "role": str(getattr(msg, "role", "user")),
        "name": str(getattr(msg, "name", "")),
        "content": sanitize_json_value(getattr(msg, "content", [])),
        "metadata": sanitize_json_value(getattr(msg, "metadata", {})),
    }


def serialize_blocks(blocks: Any) -> list[dict[str, Any]]:
    """Serialize a sequence of content blocks into JSON-safe dicts."""
    if not blocks:
        return []
    result: list[dict[str, Any]] = []
    for block in blocks:
        sanitized = sanitize_json_value(block)
        if isinstance(sanitized, dict):
            result.append(sanitized)
        else:
            result.append({"type": "text", "text": str(sanitized)})
    return result


def _render_blocks_text(content: Any) -> str:
    """Render block lists or raw content into a concise text string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(
            sanitize_json_value(content),
            ensure_ascii=False,
            allow_nan=False,
        )
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            btype = item.get("type")
            if btype == "text":
                parts.append(str(item.get("text", "")))
            elif btype == "hint":
                parts.append(_render_blocks_text(item.get("hint", "")))
            elif btype == "data":
                src = item.get("source")
                parts.append(
                    json.dumps(
                        sanitize_json_value(src if src is not None else item),
                        ensure_ascii=False,
                        allow_nan=False,
                    ),
                )
            elif btype != "thinking":
                parts.append(
                    json.dumps(
                        sanitize_json_value(item),
                        ensure_ascii=False,
                        allow_nan=False,
                    ),
                )
        else:
            parts.append(str(item))
    return "\n".join(part for part in parts if part)


def _blocks_to_openai_messages(
    role: str,
    content: Any,
    name: str = "",
) -> list[dict[str, Any]]:
    """Convert a serialized AgentScope message to OpenAI chat format."""
    if not isinstance(content, list):
        msg: dict[str, Any] = {
            "role": role,
            "content": _render_blocks_text(content),
        }
        if name:
            msg["name"] = name
        return [msg]

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []

    for block in content:
        if not isinstance(block, dict):
            text_parts.append(str(block))
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(str(block.get("text", "")))
        elif btype == "hint":
            hint_val = _render_blocks_text(block.get("hint", ""))
            if hint_val:
                text_parts.append(hint_val)
        elif btype == "tool_call":
            raw_input = block.get("input", "{}")
            args_str = (
                raw_input
                if isinstance(raw_input, str)
                else json.dumps(
                    sanitize_json_value(raw_input),
                    ensure_ascii=False,
                    allow_nan=False,
                )
            )
            tool_calls.append(
                {
                    "id": str(block.get("id", "")),
                    "type": "function",
                    "function": {
                        "name": str(block.get("name", "")),
                        "arguments": args_str,
                    },
                },
            )
        elif btype == "tool_result":
            tool_results.append(
                {
                    "role": "tool",
                    "tool_call_id": str(block.get("id", "")),
                    "name": str(block.get("name", "")),
                    "content": _render_blocks_text(block.get("output", "")),
                },
            )

    out: list[dict[str, Any]] = []
    joined = "\n".join(part for part in text_parts if part)
    if role == "assistant":
        if joined or tool_calls:
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": joined if joined else None,
            }
            if name:
                assistant_msg["name"] = name
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            out.append(assistant_msg)
        out.extend(tool_results)
        return out

    if joined or not tool_results:
        base_msg: dict[str, Any] = {"role": role, "content": joined}
        if name:
            base_msg["name"] = name
        out.append(base_msg)
    out.extend(tool_results)
    return out


@dataclass
class StepUsage:
    """Token and timing usage for one model step or a whole trajectory."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    time_sec: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert usage to a JSON-safe dictionary."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_input_tokens": self.cache_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "time_sec": sanitize_json_value(self.time_sec),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StepUsage":
        """Reconstruct :class:`StepUsage` from a dictionary."""
        return cls(
            input_tokens=int(data.get("input_tokens", 0)),
            output_tokens=int(data.get("output_tokens", 0)),
            cache_input_tokens=int(data.get("cache_input_tokens", 0)),
            cache_creation_input_tokens=int(
                data.get("cache_creation_input_tokens", 0),
            ),
            time_sec=float(data.get("time_sec") or 0.0),
        )


@dataclass
class ToolExecutionRecord:
    """Recorded execution of a single tool invocation."""

    tool_call_id: str
    tool_name: str
    tool_input: Any
    output: Any = None
    state: str = "success"
    is_error: bool = False
    error: str | None = None
    is_external: bool = False
    duration_sec: float = 0.0
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert tool record to a JSON-safe dictionary."""
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "tool_input": sanitize_json_value(self.tool_input),
            "output": sanitize_json_value(self.output),
            "state": self.state,
            "is_error": self.is_error,
            "error": self.error,
            "is_external": self.is_external,
            "duration_sec": sanitize_json_value(self.duration_sec),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolExecutionRecord":
        """Reconstruct :class:`ToolExecutionRecord` from a dictionary."""
        return cls(
            tool_call_id=str(data.get("tool_call_id", "")),
            tool_name=str(data.get("tool_name", "")),
            tool_input=data.get("tool_input"),
            output=data.get("output"),
            state=str(data.get("state", "success")),
            is_error=bool(data.get("is_error", False)),
            error=data.get("error"),
            is_external=bool(data.get("is_external", False)),
            duration_sec=float(data.get("duration_sec") or 0.0),
            started_at=str(data.get("started_at", "")),
            finished_at=str(data.get("finished_at", "")),
        )


@dataclass
class ModelStepRecord:
    """Recorded reasoning model invocation and its resulting tool runs."""

    step_index: int
    segment_index: int = 0
    model_name: str = ""
    input_messages: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] | None = None
    response_content: list[dict[str, Any]] = field(default_factory=list)
    usage: StepUsage | None = None
    tool_executions: list[ToolExecutionRecord] = field(default_factory=list)
    duration_sec: float = 0.0
    error: str | None = None
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert model step record to a JSON-safe dictionary."""
        return {
            "step_index": self.step_index,
            "segment_index": self.segment_index,
            "model_name": self.model_name,
            "input_messages": sanitize_json_value(self.input_messages),
            "tools": sanitize_json_value(self.tools),
            "response_content": sanitize_json_value(self.response_content),
            "usage": self.usage.to_dict() if self.usage is not None else None,
            "tool_executions": [t.to_dict() for t in self.tool_executions],
            "duration_sec": sanitize_json_value(self.duration_sec),
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelStepRecord":
        """Reconstruct :class:`ModelStepRecord` from a dictionary."""
        raw_usage = data.get("usage")
        usage = (
            StepUsage.from_dict(raw_usage)
            if isinstance(raw_usage, dict)
            else None
        )
        raw_tools = data.get("tool_executions") or []
        return cls(
            step_index=int(data.get("step_index", 0)),
            segment_index=int(data.get("segment_index", 0)),
            model_name=str(data.get("model_name", "")),
            input_messages=list(data.get("input_messages") or []),
            tools=data.get("tools"),
            response_content=list(data.get("response_content") or []),
            usage=usage,
            tool_executions=[
                ToolExecutionRecord.from_dict(item) for item in raw_tools
            ],
            duration_sec=float(data.get("duration_sec") or 0.0),
            error=data.get("error"),
            started_at=str(data.get("started_at", "")),
            finished_at=str(data.get("finished_at", "")),
        )


@dataclass
class CompactionRecord:
    """Recorded context compaction boundary within a trajectory."""

    after_step_index: int
    before_message_count: int
    after_message_count: int
    segment_before: int
    segment_after: int
    duration_sec: float = 0.0
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert compaction record to a JSON-safe dictionary."""
        return {
            "after_step_index": self.after_step_index,
            "before_message_count": self.before_message_count,
            "after_message_count": self.after_message_count,
            "segment_before": self.segment_before,
            "segment_after": self.segment_after,
            "duration_sec": sanitize_json_value(self.duration_sec),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CompactionRecord":
        """Reconstruct :class:`CompactionRecord` from a dictionary."""
        return cls(
            after_step_index=int(data.get("after_step_index", 0)),
            before_message_count=int(data.get("before_message_count", 0)),
            after_message_count=int(data.get("after_message_count", 0)),
            segment_before=int(data.get("segment_before", 0)),
            segment_after=int(data.get("segment_after", 0)),
            duration_sec=float(data.get("duration_sec") or 0.0),
            timestamp=str(data.get("timestamp", "")),
        )


@dataclass
class RewardRecord:
    """Outcome or process verification reward attached to a trajectory."""

    name: str
    score: float
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert reward record to a JSON-safe dictionary."""
        return {
            "name": self.name,
            "score": sanitize_json_value(self.score),
            "reason": self.reason,
            "metadata": sanitize_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RewardRecord":
        """Reconstruct :class:`RewardRecord` from a dictionary."""
        raw_score = data.get("score")
        score = float(raw_score) if raw_score is not None else float("nan")
        return cls(
            name=str(data.get("name", "reward")),
            score=score,
            reason=data.get("reason"),
            metadata=dict(data.get("metadata") or {}),
        )


TrajectoryStatus = Literal[
    "completed",
    "interrupted",
    "paused_hitl",
    "paused_external",
    "exceed_max_iters",
    "error",
]


@dataclass
class TrajectoryRecord:
    """Complete multi-step rollout trajectory for one ``Agent.reply`` call."""

    trajectory_id: str
    session_id: str
    reply_id: str
    agent_name: str
    system_prompt: str = ""
    input_messages: list[dict[str, Any]] = field(default_factory=list)
    output_message: dict[str, Any] | None = None
    steps: list[ModelStepRecord] = field(default_factory=list)
    compactions: list[CompactionRecord] = field(default_factory=list)
    rewards: list[RewardRecord] = field(default_factory=list)
    total_reward: float | None = None
    status: TrajectoryStatus = "completed"
    error: str | None = None
    total_usage: StepUsage = field(default_factory=StepUsage)
    duration_sec: float = 0.0
    started_at: str = ""
    finished_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert trajectory record to a JSON-safe dictionary."""
        return {
            "trajectory_id": self.trajectory_id,
            "session_id": self.session_id,
            "reply_id": self.reply_id,
            "agent_name": self.agent_name,
            "system_prompt": self.system_prompt,
            "input_messages": sanitize_json_value(self.input_messages),
            "output_message": sanitize_json_value(self.output_message),
            "steps": [step.to_dict() for step in self.steps],
            "compactions": [c.to_dict() for c in self.compactions],
            "rewards": [r.to_dict() for r in self.rewards],
            "total_reward": sanitize_json_value(self.total_reward),
            "status": self.status,
            "error": self.error,
            "total_usage": self.total_usage.to_dict(),
            "duration_sec": sanitize_json_value(self.duration_sec),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metadata": sanitize_json_value(self.metadata),
        }

    def to_json(self) -> str:
        """Serialize trajectory to an RFC 8259 compliant JSON string."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrajectoryRecord":
        """Reconstruct :class:`TrajectoryRecord` from a dictionary."""
        raw_usage = data.get("total_usage")
        total_usage = (
            StepUsage.from_dict(raw_usage)
            if isinstance(raw_usage, dict)
            else StepUsage()
        )
        raw_total_reward = data.get("total_reward")
        total_reward = (
            float(raw_total_reward) if raw_total_reward is not None else None
        )
        return cls(
            trajectory_id=str(data.get("trajectory_id", "")),
            session_id=str(data.get("session_id", "")),
            reply_id=str(data.get("reply_id", "")),
            agent_name=str(data.get("agent_name", "")),
            system_prompt=str(data.get("system_prompt", "")),
            input_messages=list(data.get("input_messages") or []),
            output_message=data.get("output_message"),
            steps=[
                ModelStepRecord.from_dict(s) for s in (data.get("steps") or [])
            ],
            compactions=[
                CompactionRecord.from_dict(c)
                for c in (data.get("compactions") or [])
            ],
            rewards=[
                RewardRecord.from_dict(r) for r in (data.get("rewards") or [])
            ],
            total_reward=total_reward,
            status=data.get("status", "completed"),
            error=data.get("error"),
            total_usage=total_usage,
            duration_sec=float(data.get("duration_sec") or 0.0),
            started_at=str(data.get("started_at", "")),
            finished_at=str(data.get("finished_at", "")),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_openai_messages(
        self,
        segment_index: int | None = None,
    ) -> list[dict[str, Any]]:
        """Export rollout messages in OpenAI chat-completions format.

        Args:
            segment_index (`int | None`, optional):
                When set, export the context segment corresponding to that
                compaction segment index. When ``None``, exports the final
                segment (or full context when no compaction occurred).
        """
        if not self.steps:
            messages: list[dict[str, Any]] = []
            if self.system_prompt:
                messages.append(
                    {"role": "system", "content": self.system_prompt},
                )
            for msg in self.input_messages:
                messages.extend(
                    _blocks_to_openai_messages(
                        str(msg.get("role", "user")),
                        msg.get("content", []),
                        str(msg.get("name", "")),
                    ),
                )
            if self.output_message is not None:
                messages.extend(
                    _blocks_to_openai_messages(
                        str(self.output_message.get("role", "assistant")),
                        self.output_message.get("content", []),
                        str(self.output_message.get("name", "")),
                    ),
                )
            return messages

        target_seg = (
            self.steps[-1].segment_index
            if segment_index is None
            else segment_index
        )
        seg_steps = [
            step for step in self.steps if step.segment_index == target_seg
        ]
        if not seg_steps:
            return []

        last_step = seg_steps[-1]
        exported: list[dict[str, Any]] = []
        for msg in last_step.input_messages:
            exported.extend(
                _blocks_to_openai_messages(
                    str(msg.get("role", "user")),
                    msg.get("content", []),
                    str(msg.get("name", "")),
                ),
            )

        if last_step.response_content:
            exported.extend(
                _blocks_to_openai_messages(
                    "assistant",
                    last_step.response_content,
                    self.agent_name,
                ),
            )
        for tool_exec in last_step.tool_executions:
            exported.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_exec.tool_call_id,
                    "name": tool_exec.tool_name,
                    "content": _render_blocks_text(tool_exec.output),
                },
            )
        return exported

    def to_training_segments(self) -> list[dict[str, Any]]:
        """Partition the trajectory at compaction boundaries for RL training.

        Returns one segment dictionary per contiguous context window so
        compaction-aware trainers do not conflate pre- and post-compaction
        token prefixes.
        """
        segment_ids = sorted({step.segment_index for step in self.steps})
        if not segment_ids:
            return [
                {
                    "trajectory_id": self.trajectory_id,
                    "session_id": self.session_id,
                    "reply_id": self.reply_id,
                    "segment_index": 0,
                    "messages": self.to_openai_messages(),
                    "rewards": [r.to_dict() for r in self.rewards],
                    "total_reward": sanitize_json_value(self.total_reward),
                    "status": self.status,
                    "metadata": sanitize_json_value(self.metadata),
                },
            ]

        return [
            {
                "trajectory_id": self.trajectory_id,
                "session_id": self.session_id,
                "reply_id": self.reply_id,
                "segment_index": seg_id,
                "messages": self.to_openai_messages(segment_index=seg_id),
                "rewards": [r.to_dict() for r in self.rewards],
                "total_reward": sanitize_json_value(self.total_reward),
                "status": self.status,
                "metadata": sanitize_json_value(self.metadata),
            }
            for seg_id in segment_ids
        ]
