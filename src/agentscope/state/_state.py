# -*- coding: utf-8 -*-
"""The agent state class."""
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import Enum
from typing import Any, Literal, Type, TYPE_CHECKING

from pydantic import (
    BaseModel,
    Field,
    PrivateAttr,
    field_serializer,
    model_validator,
)

import aiofiles.os

from .._utils._common import _generate_id
from ._task import Task
from ..message import (
    TextBlock,
    DataBlock,
    Msg,
    ToolCallBlock,
    ToolCallState,
    ToolResultBlock,
    ToolResultState,
    HintBlock,
)
from ..permission import PermissionContext, PermissionRule, PermissionMode
from ..permission._evidence import (
    AuthorizationEvidence,
    has_unreviewable_input,
    ScheduleAuthorization,
    retain_authorization_evidence,
)


if TYPE_CHECKING:
    from ..event import ExternalExecutionResultEvent, UserConfirmResultEvent


class _MtimeSource(Enum):
    """Distinguish a legacy host lookup from an unavailable backend mtime."""

    HOST = "host"


class ReadCacheEntry(BaseModel):
    """The read file cache."""

    lines: list[str]
    updated_at: float
    bytes: float
    file_path: str


class ToolContext(BaseModel):
    """The tool context, e.g. tool cache"""

    max_cache_files: int = Field(default=100, gt=1)
    """The maximum number of cached files."""
    max_cache_bytes: float = Field(default=25000, gt=10000)
    """The maximum size of the accumulated read file cache."""
    read_file_cache: list[ReadCacheEntry] = Field(default_factory=list)
    """The cache for Read/Write/Edit file tools."""

    activated_groups: list[str] = Field(default_factory=list)
    """The names of the activated tool groups, each group contains a set of
    tools."""

    async def get_cache(
        self,
        file_path: str,
        mtime: float | None | _MtimeSource = _MtimeSource.HOST,
    ) -> ReadCacheEntry | None:
        """Get cached file content if still valid.

        Args:
            file_path (`str`):
                The absolute path of the file.
            mtime (`float | None`, optional):
                The file's modification time, obtained from the same
                filesystem that read the file, e.g. a workspace backend.
                Omitting this argument queries the host filesystem for
                compatibility with local callers. Explicit ``None`` means
                the backend timestamp is unavailable: invalidate the entry
                without querying the host filesystem.

        Returns:
            `ReadCacheEntry | None`:
                The cached entry if valid, otherwise None.
        """

        # Find the cache entry
        for entry in self.read_file_cache:
            if entry.file_path == file_path:
                if mtime is _MtimeSource.HOST:
                    try:
                        mtime = await aiofiles.os.path.getmtime(file_path)
                    except Exception:
                        mtime = None

                # Concurrent calls may have reordered or dropped the entry
                # while awaiting, so locate it again by the object itself.
                if entry not in self.read_file_cache:
                    return None

                self.read_file_cache.remove(entry)
                if mtime != entry.updated_at:
                    # Cache is outdated, or the file no longer exists
                    return None

                # Move the entry to the most recent position
                self.read_file_cache.append(entry)
                return entry
        return None

    async def cache_file(
        self,
        file_path: str,
        lines: list[str],
        mtime: float | None | _MtimeSource = _MtimeSource.HOST,
    ) -> None:
        """Cache file content with LRU eviction.

        Args:
            file_path (`str`):
                The absolute path of the file.
            lines (`list[str]`):
                The lines of the file content.
            mtime (`float | None`, optional):
                The file's modification time, obtained from the same
                filesystem that read the file, e.g. a workspace backend.
                Omitting this argument queries the host filesystem for
                compatibility with local callers. Explicit ``None`` means
                the backend timestamp is unavailable: remove any old entry
                for this path and do not cache the content.
        """
        if mtime is _MtimeSource.HOST:
            try:
                mtime = await aiofiles.os.path.getmtime(file_path)
            except Exception:
                mtime = None

        # Remove existing cache for this file if present
        self.read_file_cache = [
            entry
            for entry in self.read_file_cache
            if entry.file_path != file_path
        ]

        # Unknown backend metadata cannot validate this content against an
        # unrelated host file, including after a successful write.
        if mtime is None:
            return

        # Calculate size in KB
        new_entry_bytes = (
            sum(len(line.encode("utf-8")) for line in lines) / 1024
        )

        # An entry that cannot fit even in an empty cache must not evict
        # unrelated entries or make the configured byte limit ineffective.
        if new_entry_bytes > self.max_cache_bytes:
            return

        # Evict the oldest entries if exceeding max_cache_files
        while len(self.read_file_cache) >= self.max_cache_files:
            self.read_file_cache.pop(0)

        # Evict the oldest entries if exceeding max_cache_bytes
        current_size = sum(entry.bytes for entry in self.read_file_cache)
        while (
            self.read_file_cache
            and current_size + new_entry_bytes > self.max_cache_bytes
        ):
            removed = self.read_file_cache.pop(0)
            current_size -= removed.bytes

        # Add new entry to the end (most recent)
        self.read_file_cache.append(
            ReadCacheEntry(
                lines=lines,
                updated_at=mtime,
                bytes=new_entry_bytes,
                file_path=file_path,
            ),
        )

    async def clean_file_cache(
        self,
        reserved_file_paths: set[str] | None = None,
    ) -> None:
        """Drop read caches whose paths are not in ``reserved_file_paths``.

        Args:
            reserved_file_paths: File paths from Read calls that remain in the
                context. Caches for these files are kept; all others are
                evicted.
        """
        reserved_file_paths = reserved_file_paths or set()

        self.read_file_cache = [
            entry
            for entry in self.read_file_cache
            if entry.file_path in reserved_file_paths
        ]


class TaskContext(BaseModel):
    """The task context."""

    tasks: list[Task] = Field(default_factory=lambda: [])
    """The task context."""


class ActingRewrite(BaseModel):
    """Original middleware input and the invocation it forwarded."""

    source: tuple[str, str]
    forwarded: tuple[str, str]
    final_input: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    """Final tool middleware arguments, before any confirmation edits."""

    cache_index: int | None = Field(
        default=None,
        exclude_if=lambda v: v is None,
    )
    """Acting middleware that supplied the cached result, if applicable."""


class ToolApproval(BaseModel):
    """Server checkpoint for a pending or confirmed invocation."""

    user_confirmed: bool = False
    confirmation_target: str | None = None
    acting_rewrite: ActingRewrite | None = None
    external_request_id: str | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    """ID of the outstanding external execution request."""

    external_result_schema: dict | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    """Dispatched metadata contract. An empty schema accepts any metadata."""


class ReplyContext(BaseModel):
    """The context of the current agent reply."""

    reply_id: str = Field(default_factory=_generate_id)
    """The id of the current reply, which is also used as the id of the
    final message of the reply."""

    tool_approvals: dict[str, ToolApproval] = Field(
        default_factory=dict,
        exclude_if=lambda value: not value,
    )

    cur_iter: int = 0
    """The current iteration of the agent's reasoning-acting loop in this
    reply."""

    structured_schema: Type[BaseModel] | dict | None = None
    """The reply's structured output requirement, a pydantic model class in
    process and serialized as its JSON schema dict."""

    @field_serializer("structured_schema")
    def _serialize_structured_schema(
        self,
        value: Type[BaseModel] | dict | None,
    ) -> dict | None:
        """Serialize a schema class into its JSON schema dict."""
        return value.model_json_schema() if isinstance(value, type) else value

    structured_output: dict | None = None
    """The structured output generated within this reply."""

    def approval_for(self, call_id: str) -> ToolApproval:
        """Create a checkpoint only when an invocation needs one."""
        if call_id not in self.tool_approvals:
            self.tool_approvals = {
                **self.tool_approvals,
                call_id: ToolApproval(),
            }
        return self.tool_approvals[call_id]

    def has_user_confirmation(self, call: ToolCallBlock) -> bool:
        """Only a confirmed, executable invocation can skip another review."""
        approval = self.tool_approvals.get(call.id)
        return bool(
            call.state == ToolCallState.ALLOWED
            and approval
            and approval.user_confirmed,
        )


class _MemberLimits(BaseModel):
    """Additional restrictions for the current delegated team turn."""

    read_only: bool
    interactive: bool
    deny_rules: dict[str, list[PermissionRule]] = Field(default_factory=dict)
    evidence: list[AuthorizationEvidence] = Field(default_factory=list)
    complete: bool = True
    schedule: ScheduleAuthorization | None = None


class ScheduleSessionLimits(BaseModel):
    """Session preferences which can only narrow the schedule's policy."""

    mode: PermissionMode = PermissionMode.BYPASS
    read_only: bool = False
    allow_workspace_edits: bool = True

    @classmethod
    def from_context(
        cls,
        context: PermissionContext,
    ) -> "ScheduleSessionLimits":
        """Keep persisted settings when their original source is unknown."""
        return cls(
            mode=context.mode,
            read_only=context.read_only,
            allow_workspace_edits=context.allow_workspace_edits,
        )


class AgentState(BaseModel):
    """The agent state that should be saved and loaded from storage."""

    session_id: str = Field(default_factory=_generate_id)
    """The session id of the agent. Normally, each session will maintain one
    independent agent state for each agent."""

    summary: str | list[TextBlock | DataBlock] = ""
    """The compressed summary of the context, which will be prepended to the
    context when fed into the LLM."""

    context: list[Msg] = Field(default_factory=list)
    """The uncompressed conversation context, which will be fed into the LLM"""

    authorization_evidence: list[AuthorizationEvidence] = Field(
        default_factory=list,
    )
    """Original instructions removed from the active context by compression."""
    authorization_evidence_complete: bool = True
    """Whether earlier instructions were retained before compression."""
    schedule_authorization: ScheduleAuthorization | None = None
    """Current task evidence, refreshed from trusted schedule storage."""

    authorization_version: Literal[0, 1] = 0
    """Unversioned checkpoints start at zero and are saved as version one."""

    _delegation_parent: Callable[[], "AgentState"] | None = PrivateAttr(
        default=None,
    )

    def model_post_init(self, context: Any) -> None:
        """Stamp migrated and new states for compact checkpoint writes."""
        super().model_post_init(context)
        self.authorization_version = 1

    @model_validator(mode="before")
    @classmethod
    def _mark_missing_authorization_history(cls, data: Any) -> Any:
        """Old serialized user turns can also be generated team or SOP work."""
        if not isinstance(data, dict) or (
            data.get("authorization_version") == 1
            or "authorization_evidence_complete" in data
            or "authorization_evidence" in data
        ):
            return data
        incomplete = bool(data.get("summary"))
        context = []
        for message in data.get("context") or []:
            if (
                isinstance(message, dict)
                and message.get("role") == "user"
                and not (message.get("metadata") or {}).get(
                    "authorization_source",
                )
            ):
                incomplete = True
                message = {
                    **message,
                    "metadata": {
                        **(message.get("metadata") or {}),
                        "authorization_source": "unverified",
                    },
                }
            context.append(message)
        if incomplete:
            return {
                **data,
                "context": context,
                "authorization_evidence_complete": False,
            }
        return data

    def retain_authorization(self, messages: list[Msg]) -> None:
        """Archive source-tagged instructions before removing their context."""
        if has_unreviewable_input(messages):
            self.authorization_evidence_complete = False
        self.authorization_evidence = retain_authorization_evidence(
            self.authorization_evidence,
            messages,
        )

    def permission_review_context(self) -> dict[str, Any]:
        """Provide only retained evidence, never the generated summary."""
        evidence = self.authorization_evidence
        complete = (
            self.authorization_evidence_complete
            and not has_unreviewable_input(self.context)
        )
        schedule = self.schedule_authorization
        if self.member_limits:
            evidence = list(evidence)
        for limits in self.member_limits.values():
            evidence.extend(limits.evidence)
            complete &= limits.complete
            if limits.schedule is not None:
                if schedule is not None and (
                    schedule.user_id != limits.schedule.user_id
                    or schedule.schedule_id != limits.schedule.schedule_id
                ):
                    complete = False
                schedule = limits.schedule
        if self._delegation_parent is not None:
            parent = self._delegation_parent()
            current = parent.permission_review_context()
            schedule = current.get("schedule_authorization")
            evidence = retain_authorization_evidence(
                [*evidence, *current.get("authorization_evidence", [])],
                parent.context,
            )
            complete &= current.get("evidence_complete", True)
        elif self.member_limits:
            evidence = retain_authorization_evidence(evidence, [])
        result: dict[str, Any] = {}
        if evidence:
            result["authorization_evidence"] = evidence
        if not complete:
            result["evidence_complete"] = False
        if schedule is not None:
            result["schedule_authorization"] = schedule
        return result

    # =================================================================
    # For backward compatibility
    # =================================================================
    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_reply_fields(cls, data: Any) -> Any:
        """Migrate the top-level ``reply_id``/``cur_iter`` fields (the
        pre-``reply_context`` storage format) into ``reply_context``, so
        that the states saved by previous versions load correctly."""
        if isinstance(data, dict) and (
            "reply_id" in data or "cur_iter" in data
        ):
            data = dict(data)
            reply_context = dict(data.get("reply_context") or {})
            for key in ("reply_id", "cur_iter"):
                if key in data and key not in reply_context:
                    reply_context[key] = data.pop(key)
            data["reply_context"] = reply_context
        return data

    @property
    def reply_id(self) -> str:
        """The reply id of the current reply."""
        return self.reply_context.reply_id

    @reply_id.setter
    def reply_id(self, value: str) -> None:
        """Set the reply id of the current reply."""
        if value != self.reply_context.reply_id:
            self.reply_context.tool_approvals.clear()
        self.reply_context.reply_id = value

    @property
    def cur_iter(self) -> int:
        """The current iteration of the agent's reasoning-acting loop in
        this reply."""
        return self.reply_context.cur_iter

    @cur_iter.setter
    def cur_iter(self, value: int) -> None:
        """Set the current iteration of the agent's reasoning-acting loop in
        this reply."""
        self.reply_context.cur_iter = value

    # =================================================================
    # The reply context
    # =================================================================
    reply_context: ReplyContext = Field(default_factory=ReplyContext)
    """The reply related context."""

    # =================================================================
    # The permission context
    # =================================================================
    permission_context: PermissionContext = Field(
        default_factory=PermissionContext,
    )
    """Local permission settings. Use effective_permission_context to check
    a tool or pass permissions to another agent."""

    schedule_session_limits: ScheduleSessionLimits | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    """Explicit local bounds for sessions managed by a schedule."""

    member_limits: dict[str, _MemberLimits] = Field(
        default_factory=dict,
        exclude_if=lambda limits: not limits,
    )
    """Active team restrictions by leader session, apart from local policy."""

    def inherit_member_scope(
        self,
        parent: "AgentState",
        *,
        key: str | None = None,
    ) -> None:
        """Keep the current assignment separate from the member's own state."""
        policy = parent.effective_permission_context
        authority = parent.permission_review_context()
        self.member_limits[key or parent.session_id] = _MemberLimits(
            read_only=policy.read_only,
            interactive=policy.interactive,
            deny_rules=policy.deny_rules,
            evidence=retain_authorization_evidence(
                authority.get("authorization_evidence", []),
                parent.context,
            ),
            complete=authority.get("evidence_complete", True),
            schedule=authority.get("schedule_authorization"),
        ).model_copy(deep=True)

    @contextmanager
    def assignment_scope(
        self,
        parent: "AgentState",
        key: str,
    ) -> Iterator[None]:
        """Apply one assignment's authority during dispatch and resume."""
        self.inherit_member_scope(parent, key=key)
        try:
            yield
        finally:
            self.member_limits.pop(key, None)

    @property
    def effective_permission_context(self) -> PermissionContext:
        """Combine the current local settings with active team restrictions."""
        context = self.permission_context
        if not self.member_limits:
            return context
        limits = self.member_limits.values()
        denials = {
            name: list(rules) for name, rules in context.deny_rules.items()
        }
        for scope in limits:
            for name, rules in scope.deny_rules.items():
                current = denials.setdefault(name, [])
                current.extend(rule for rule in rules if rule not in current)
        return context.model_copy(
            update={
                "deny_rules": denials,
                "read_only": context.read_only
                or any(limit.read_only for limit in limits),
                "interactive": context.interactive
                and all(limit.interactive for limit in limits),
            },
        )

    # =================================================================
    # The tool context
    # =================================================================
    tool_context: ToolContext = Field(default_factory=ToolContext)

    # =================================================================
    # The tasks context
    # =================================================================
    tasks_context: TaskContext = Field(default_factory=TaskContext)
    """The task context that records the agent tasks."""

    # =================================================================
    # The middleware context
    # =================================================================
    middle_context: dict[str, Any] = Field(default_factory=dict)
    """The context that allow the middlewares to store/get data across
    different replies."""

    def get_reply_message(self, name: str | None) -> Msg | None:
        """Find this agent's current reply, even after later messages.

        Args:
            name (`str | None`):
                Author of the assistant reply, or None to match by reply ID.

        Returns:
            `Msg | None`:
                The assistant message matching both ``name`` and the current
                ``reply_id``, or ``None`` if it has not been created.
        """
        return next(
            (
                message
                for message in reversed(self.context)
                if message.id == self.reply_id
                and message.role == "assistant"
                and (name is None or message.name == name)
            ),
            None,
        )

    def append_context(
        self,
        name: str,
        blocks: list[
            TextBlock | DataBlock | HintBlock | ToolCallBlock | ToolResultBlock
        ],
    ) -> None:
        """Append the given blocks to the agent's own message with the current
        `reply_id`. If such message doesn't exist, a new assistant message
        with agent's name and current reply ID will be created.
        """
        message = self.get_reply_message(name)
        if message is not None:
            message.content.extend(blocks)
        else:
            # Create a new assistant message with the current reply ID
            self.context.append(
                Msg(
                    id=self.reply_id,
                    role="assistant",
                    name=name,
                    content=blocks,
                ),
            )

    def has_awaiting_tool_calls(self, name: str | None) -> bool:
        """Whether the current reply written by ``name`` has
        any tool call still awaiting an outside response — an
        ``ASKING`` user confirmation or a ``SUBMITTED`` external
        execution with no matching tool result yet.

        Args:
            name (`str | None`):
                Agent name, or None to match the current reply by ID.

        Returns:
            `bool`:
                ``True`` when at least one such tool call is pending.
        """
        return bool(self.get_awaiting_tool_calls(name))

    def confirmation_request(
        self,
        name: str | None,
        call_id: str,
    ) -> str | None:
        """Get the current request ID from the persisted reply."""
        message = self.get_reply_message(name)
        return message.confirmation_request(call_id) if message else None

    def confirmation_status(
        self,
        name: str | None,
        event: "UserConfirmResultEvent",
    ) -> Literal["stale", "ready", "reissue", "invalid"]:
        """Classify a receipt against the active reply before resuming it."""
        if event.reply_id != self.reply_id:
            return "stale"
        message = self.get_reply_message(name)
        if message is None:
            return "invalid"
        calls = {
            call.id: call for call in message.get_content_blocks("tool_call")
        }
        if any(r.request_id is None for r in event.confirm_results):
            event.validate_request_ids(
                {
                    call.id: message.confirmation_request(call.id)
                    for call in calls.values()
                    if call.state == ToolCallState.ASKING
                },
            )
        ready = False
        reissue = False
        for result in event.confirm_results:
            call = calls.get(result.tool_call.id)
            if call is None:
                return "invalid"
            if call.state in (ToolCallState.SUBMITTED, ToolCallState.FINISHED):
                continue
            if call.state != ToolCallState.ASKING:
                return "invalid"
            request = message.confirmation_request(call.id)
            if request is None:
                reissue = True
            elif result.answers(call, request):
                ready = True
        if reissue:
            return "reissue"
        return "ready" if ready else "stale"

    def pending_external_results(
        self,
        name: str | None,
        event: "ExternalExecutionResultEvent",
    ) -> "ExternalExecutionResultEvent | None":
        """Keep the first result for each submitted call in this reply."""
        if event.reply_id != self.reply_id:
            return None
        message = self.get_reply_message(name)
        if message is None:
            raise ValueError("Agent is not waiting for external execution.")
        calls = {
            call.id: call for call in message.get_content_blocks("tool_call")
        }
        consumed = {
            block.id for block in message.get_content_blocks("tool_result")
        }
        if not event.execution_results:
            if any(
                call.state == ToolCallState.SUBMITTED
                and call.id not in consumed
                for call in calls.values()
            ):
                return event
            raise ValueError("Agent is not waiting for external execution.")
        pending = []
        for result in event.execution_results:
            call = calls.get(result.id)
            status = call.state if call is not None else None
            if status == ToolCallState.FINISHED or (
                status == ToolCallState.SUBMITTED and result.id in consumed
            ):
                continue
            if status != ToolCallState.SUBMITTED:
                raise ValueError(
                    f"Tool call {result.id!r} is not waiting for an "
                    "external execution result.",
                )
            if result.name != call.name:
                raise ValueError(
                    f"Expected tool {call.name!r} for call {call.id!r}.",
                )
            if result.state == ToolResultState.RUNNING:
                raise ValueError(
                    f"Expected a terminal result for call {call.id!r}.",
                )
            pending.append(result)
            consumed.add(result.id)
        if not pending:
            return None
        return event.model_copy(update={"execution_results": pending})

    def get_awaiting_tool_calls(self, name: str | None) -> list[ToolCallBlock]:
        """Get the current reply's tool calls still awaiting an
        outside response — an ``ASKING`` user confirmation or a
        ``SUBMITTED`` external execution with no matching tool result yet.

        Args:
            name (`str | None`):
                Agent name, or None to match the current reply by ID.

        Returns:
            `list[ToolCallBlock]`:
                The awaiting tool call blocks, empty if none.
        """
        last_msg = self.get_reply_message(name)
        if last_msg is None:
            return []
        result_ids = {b.id for b in last_msg.get_content_blocks("tool_result")}
        return [
            tc
            for tc in last_msg.get_content_blocks("tool_call")
            if tc.state == ToolCallState.ASKING
            or (
                tc.state == ToolCallState.SUBMITTED and tc.id not in result_ids
            )
        ]

    def get_unfinished_tool_calls(self, name: str) -> list[ToolCallBlock]:
        """Get the current reply's tool calls with no matching tool result
        yet, whatever their state. A reply accumulates into a single message,
        so the calls of the finished reasoning-acting rounds are excluded
        while the ones of the ongoing round are returned.

        Args:
            name (`str`):
                Only messages authored by this agent name are inspected;
                observed messages from other agents are ignored.

        Returns:
            `list[ToolCallBlock]`:
                The unfinished tool call blocks, empty if none.
        """
        last_msg = self.get_reply_message(name)
        if last_msg is None:
            return []
        result_ids = {b.id for b in last_msg.get_content_blocks("tool_result")}
        return [
            tc
            for tc in last_msg.get_content_blocks("tool_call")
            if tc.id not in result_ids
        ]
