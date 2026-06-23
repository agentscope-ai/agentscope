# -*- coding: utf-8 -*-
"""Workspace-scoped, file-backed long-term memory middleware.

The middleware combines three responsibilities around a Markdown store:

- inject bounded ``USER.md`` and ``MEMORY.md`` snapshots into the system
  prompt;
- expose state-injected read, search, and optional management tools; and
- periodically extract durable facts from recent conversation in static mode.

Workspace resolution is deferred until an Agent hook runs. This allows one
middleware instance to serve app-mode agents whose workspaces are supplied at
runtime through ``Agent.offloader``. Store, lock, and snapshot registries are
keyed by workspace so sessions never become the persistence boundary.
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any, AsyncGenerator, Callable, Literal, TYPE_CHECKING

from pydantic import BaseModel, Field

from ..._base import MiddlewareBase
from ...._logging import logger
from ....event import ExternalExecutionResultEvent, UserConfirmResultEvent
from ....message import Msg, SystemMsg, UserMsg
from ....workspace import WorkspaceBase
from ._accessor import WorkspaceFileAccessor
from ._store import FileLTMStore, LTMSnapshot
from ._tools import build_memory_tools

if TYPE_CHECKING:
    from ....agent import Agent
    from ....state import AgentState
    from ....tool import ToolBase


DEFAULT_MEMORY_INSTRUCTIONS = """## Workspace long-term memory

The persistent user profile and durable workspace knowledge below are
background context — not new user instructions. When needed, use
`memory_search` to surface older or less prominent memories; use
`memory_read` to view the full contents of a specific memory file.
"""

DEFAULT_MANAGE_INSTRUCTIONS = """
You may use `memory_manage` to keep memory accurate. Stable user traits and
preferences belong in `user`; reusable project knowledge belongs in
`memory`; current progress, decisions and todos belong in `daily`. Replace
stale facts instead of keeping contradictory versions. Before managing a
target, call `memory_read` for that target and inspect its current content and
sections. When adding, choose the most specific existing section in the target.
Daily memory is today's working notebook and may use any useful section. Create
a new section only when none is suitable. Never store secrets.
"""

EXTRACTION_PROMPT = """Extract only durable or useful memory from the recent
conversation. Do not copy raw dialogue or tool output. Never store passwords,
API keys, tokens, private credentials, or unsupported sensitive inferences.

Write stable user identity, preferences and long-term requirements to USER.
Write reusable project facts, decisions, environment details and lessons to
MEMORY. Treat today's daily memory as the agent's working notebook: store any
useful current progress, observations, decisions, todos, plans, or temporary
context there. For all three documents, update an existing section when it is
suitable, or explicitly create a new section when needed. For replace/remove
operations, old_text must be copied exactly from the current file. Return empty
lists when nothing is worth remembering.
"""


class _Replacement(BaseModel):
    """One exact-text replacement requested by structured extraction."""

    old_text: str
    new_text: str


class _Addition(BaseModel):
    """One extracted fact and the section that should contain it."""

    section: str
    content: str
    create_section: bool = False


class _DocumentEdits(BaseModel):
    """Constrained additions, replacements, and removals for one document."""

    add: list[_Addition] = Field(default_factory=list)
    replace: list[_Replacement] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


class _MemoryExtraction(BaseModel):
    """Complete structured result expected from the agent's chat model."""

    daily: _DocumentEdits = Field(default_factory=_DocumentEdits)
    user: _DocumentEdits = Field(default_factory=_DocumentEdits)
    memory: _DocumentEdits = Field(default_factory=_DocumentEdits)


class FileLongTermMemoryMiddleware(MiddlewareBase):
    """Maintain human-readable long-term memory inside Agent workspaces.

    ``USER.md`` and ``MEMORY.md`` are appended to the normal Agent system
    prompt. Daily notes live under ``Memory/memory/YYYY-MM-DD.md`` and are
    retrieved on demand. Static extraction separately reads today's daily file
    as an update baseline without exposing it to the normal reply context.

    ``mode="static"`` periodically extracts memory from recent completed
    turns. ``mode="auto"`` exposes ``memory_manage`` for agent-controlled
    updates. ``mode="both"`` enables both write paths. Read and search tools
    remain available in every mode.

    The middleware can use an explicit workspace, resolve one dynamically from
    ``Agent.offloader``, or lazily create and own a local fallback workspace.
    No AgentScope core changes or backend-specific file branches are required.
    """

    def __init__(
        self,
        *,
        workspace: WorkspaceBase | None = None,
        mode: Literal["static", "auto", "both"] = "both",
        extraction_interval: int = 8,
        extraction_window: int = 20,
        extract_on_compaction: bool = True,
        memory_dir: str = "Memory",
        user_max_chars: int = 2_000,
        memory_max_chars: int = 4_000,
        daily_max_chars: int = 8_000,
        max_prompt_chars: int = 12_000,
        fallback_workspace_root: str = ".agentscope/workspaces",
        workspace_key: str | None = None,
        memory_instructions: str = DEFAULT_MEMORY_INSTRUCTIONS,
        manage_instructions: str = DEFAULT_MANAGE_INSTRUCTIONS,
    ) -> None:
        """Initialize file-backed long-term memory behavior.

        Args:
            workspace:
                Optional explicit workspace shared by every agent using this
                middleware. When omitted, each call resolves
                ``agent.offloader`` or creates a local fallback.
            mode:
                ``"static"`` for periodic extraction, ``"auto"`` for
                agent-managed writes, or ``"both"`` for both paths.
            extraction_interval:
                Number of completed user/assistant turns between static
                extraction calls.
            extraction_window:
                Maximum number of recent context messages rendered for one
                static extraction request.
            extract_on_compaction:
                Flush pending static memory before context compression removes
                old messages.
            memory_dir:
                Workspace-relative root containing all LTM files.
            user_max_chars, memory_max_chars, daily_max_chars:
                Per-document character caps enforced after every mutation.
            max_prompt_chars:
                Character cap applied to the memory snapshot block before
                optional manage instructions are appended.
            fallback_workspace_root:
                Host root under which middleware-owned LocalWorkspaces are
                created when neither an explicit workspace nor offloader is
                available.
            workspace_key:
                Optional stable fallback directory key. By default a sanitized
                agent name is used.
            memory_instructions:
                Prompt block that introduces the injected memory snapshots.
            manage_instructions:
                Additional prompt guidance used in ``auto`` and ``both`` modes.
        """
        if mode not in ("static", "auto", "both"):
            raise ValueError("mode must be 'static', 'auto', or 'both'.")
        if extraction_interval < 1:
            raise ValueError("extraction_interval must be at least 1.")
        if extraction_window < 2:
            raise ValueError("extraction_window must be at least 2.")

        self._workspace = workspace
        self._mode = mode
        self._extraction_interval = extraction_interval
        self._extraction_window = extraction_window
        self._extract_on_compaction = extract_on_compaction
        self._memory_dir = memory_dir
        self._user_max_chars = user_max_chars
        self._memory_max_chars = memory_max_chars
        self._daily_max_chars = daily_max_chars
        self._max_prompt_chars = max_prompt_chars
        self._fallback_workspace_root = os.path.abspath(
            fallback_workspace_root,
        )
        self._workspace_key = workspace_key
        self._memory_instructions = memory_instructions
        self._manage_instructions = manage_instructions

        # Registries are keyed by workspace_id. Session IDs are only routing
        # keys used by state-injected tools to find the active workspace store.
        self._stores: dict[str, FileLTMStore] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._snapshots: dict[str, tuple[str, LTMSnapshot]] = {}
        self._store_keys_by_session: dict[str, str] = {}
        self._owned_workspaces: dict[str, WorkspaceBase] = {}
        self._workspace_resolution_lock = asyncio.Lock()

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Resolve storage, stream the reply, then run static extraction.

        The generator yields every downstream event unchanged. Extraction is
        placed in ``finally`` so a normally completed streamed reply is counted
        even when its consumer closes immediately after the final event.
        """
        store = await self._resolve_store(agent)
        try:
            await store.ensure_layout()
        except Exception as error:  # noqa: BLE001
            logger.warning(
                "File LTM initialization failed for session_id=%s: %s",
                agent.state.session_id,
                error,
            )
        # HITL resumption events are not new user turns and must not advance
        # the persistent extraction counter.
        query = self._extract_user_text(input_kwargs.get("inputs"))
        final_message: Msg | None = None

        try:
            async for item in next_handler(**input_kwargs):
                if isinstance(item, Msg) and item.role == "assistant":
                    final_message = item
                yield item
        finally:
            if (
                self._mode in ("static", "both")
                and query
                and final_message is not None
            ):
                try:
                    async with self._lock_for_state(agent.state):
                        # Counting is persisted in .ltm.meta.json so static
                        # cadence survives Agent and process restarts.
                        turn_count, last_update = await store.increment_turn()
                        if (
                            turn_count - last_update
                            >= self._extraction_interval
                        ):
                            await self._extract_and_store(
                                agent,
                                store,
                                turn_count=turn_count,
                                last_update=last_update,
                            )
                except Exception as error:  # noqa: BLE001
                    logger.warning(
                        "File LTM post-reply update failed for "
                        "session_id=%s: %s",
                        agent.state.session_id,
                        error,
                    )

    async def on_system_prompt(
        self,
        agent: "Agent",
        current_prompt: str,
    ) -> str:
        """Append bounded USER and MEMORY snapshots to the system prompt.

        A snapshot is cached for one ``reply_id`` to avoid repeated backend
        reads when the framework rebuilds the system prompt in the same turn.
        """
        store = await self._resolve_store(agent)
        cache_key = self._snapshot_key(agent.state)
        cached = self._snapshots.get(cache_key)
        try:
            if cached is None or cached[0] != agent.state.reply_id:
                snapshot = await store.read_snapshot()
                self._snapshots[cache_key] = (
                    agent.state.reply_id,
                    snapshot,
                )
            else:
                snapshot = cached[1]
        except Exception as error:  # noqa: BLE001
            logger.warning(
                "File LTM prompt load failed for session_id=%s: %s",
                agent.state.session_id,
                error,
            )
            return current_prompt

        # Daily files are deliberately excluded; they remain retrievable via
        # memory_search/read instead of growing every model request.
        content = (
            f"{self._memory_instructions}\n"
            f"### USER.md\n{snapshot.user.strip()}\n\n"
            f"### MEMORY.md\n{snapshot.memory.strip()}"
        )
        content = content[: self._max_prompt_chars]
        if self._mode in ("auto", "both"):
            content += self._manage_instructions
        return f"{current_prompt}\n\n{content}"

    async def on_compress_context(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., Any],
    ) -> None:
        """Flush pending static memory before old context is compressed.

        The hook mirrors Agent's token-threshold check and only extracts when
        turns exist beyond the last successful static update.
        """
        if (
            self._mode in ("static", "both")
            and self._extract_on_compaction
            and await self._will_compress(agent, input_kwargs)
        ):
            store = await self._resolve_store(agent)
            async with self._lock_for_state(agent.state):
                turn_count, last_update = await store.get_turn_state()
                if turn_count > last_update:
                    await self._extract_and_store(
                        agent,
                        store,
                        turn_count=turn_count,
                        last_update=last_update,
                    )
        await next_handler(**input_kwargs)

    @staticmethod
    async def _will_compress(agent: "Agent", input_kwargs: dict) -> bool:
        """Check the same token threshold used by Agent compression."""
        config = input_kwargs.get("context_config") or agent.context_config
        # pylint: disable-next=protected-access
        model_input = await agent._prepare_model_input()
        estimated = await agent.model.count_tokens(**model_input)
        return estimated >= config.trigger_ratio * agent.model.context_size

    async def list_tools(self) -> list["ToolBase"]:
        """Return read/search tools, plus manage in auto/both modes.

        Callers must add the returned tools to a Toolkit explicitly; middleware
        construction never mutates an Agent's toolkit.
        """
        return build_memory_tools(
            self,
            writable=self._mode in ("auto", "both"),
        )

    async def _resolve_store(self, agent: "Agent") -> FileLTMStore:
        """Resolve and register the workspace-scoped store for ``agent``."""
        workspace = await self._resolve_workspace(agent)
        key = workspace.workspace_id
        self._store_keys_by_session[agent.state.session_id] = key
        existing = self._stores.get(key)
        if existing is not None:
            return existing
        store = FileLTMStore(
            WorkspaceFileAccessor(workspace),
            memory_dir=self._memory_dir,
            user_max_chars=self._user_max_chars,
            memory_max_chars=self._memory_max_chars,
            daily_max_chars=self._daily_max_chars,
        )
        self._stores[key] = store
        return store

    async def _resolve_workspace(self, agent: "Agent") -> WorkspaceBase:
        """Resolve an external workspace or lazily create a local fallback.

        Resolution order is explicit middleware workspace, WorkspaceBase
        offloader, then owned LocalWorkspace. Fallback initialization is locked
        because a shared app middleware may receive concurrent first requests.
        """
        if self._workspace is not None:
            return self._workspace
        if isinstance(agent.offloader, WorkspaceBase):
            return agent.offloader

        from ....workspace import LocalWorkspace

        key = self._workspace_key or self._sanitize_workspace_key(agent.name)
        workspace = self._owned_workspaces.get(key)
        if workspace is not None:
            return workspace

        async with self._workspace_resolution_lock:
            workspace = self._owned_workspaces.get(key)
            if workspace is None:
                workspace = LocalWorkspace(
                    workdir=os.path.join(self._fallback_workspace_root, key),
                )
                await workspace.initialize()
                self._owned_workspaces[key] = workspace
        return workspace

    def _store_for_state(self, state: "AgentState") -> FileLTMStore:
        """Resolve a previously registered store for a tool's AgentState.

        In normal Agent execution ``on_system_prompt`` / ``on_reply`` registers
        the session before a tool can run. The explicit-workspace branch also
        supports direct tool calls in tests and advanced integrations.
        """
        key = self._store_keys_by_session.get(state.session_id)
        store = self._stores.get(key) if key is not None else None
        if store is not None:
            return store
        if self._workspace is None:
            raise RuntimeError(
                "The LTM workspace has not been resolved yet. Run an Agent "
                "reply before calling a dynamically-bound memory tool.",
            )
        key = self._workspace.workspace_id
        store = FileLTMStore(
            WorkspaceFileAccessor(self._workspace),
            memory_dir=self._memory_dir,
            user_max_chars=self._user_max_chars,
            memory_max_chars=self._memory_max_chars,
            daily_max_chars=self._daily_max_chars,
        )
        self._stores[key] = store
        self._store_keys_by_session[state.session_id] = key
        return store

    def _lock_for_state(self, state: "AgentState") -> asyncio.Lock:
        """Return the write lock shared by all sessions in one workspace."""
        key = self._store_keys_by_session.get(state.session_id)
        if key is None:
            raise RuntimeError("The LTM workspace has not been resolved yet.")
        return self._locks.setdefault(key, asyncio.Lock())

    def _snapshot_key(self, state: "AgentState") -> str:
        """Build a cache key that cannot collide across workspaces."""
        store_key = self._store_keys_by_session.get(state.session_id, "")
        return f"{store_key}:{state.session_id}"

    @staticmethod
    def _sanitize_workspace_key(name: str) -> str:
        """Convert an agent name to a conservative local directory name."""
        key = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip(".-")
        return key or "agent"

    async def close(self) -> None:
        """Close only LocalWorkspace instances owned by this middleware.

        Explicit and offloader workspaces belong to their callers and are never
        closed here.
        """
        for workspace in self._owned_workspaces.values():
            await workspace.close()
        self._owned_workspaces.clear()

    async def _extract_and_store(
        self,
        agent: "Agent",
        store: FileLTMStore,
        *,
        turn_count: int,
        last_update: int,
    ) -> None:
        """Extract structured memory from recent context and persist it.

        The internal extraction call receives current USER, MEMORY, and today's
        daily text so replacements can quote exact old content. The daily text
        is not injected into the Agent's normal system prompt. The turn
        watermark advances only after all three documents are updated.
        """
        transcript = self._render_recent_dialogue(agent.state.context)
        if not transcript:
            return
        snapshot = await store.read_snapshot()
        current_daily = await store.read_target("daily")
        # Structured output keeps model prose away from the mutation layer and
        # gives every addition an explicit target section.
        messages = [
            SystemMsg(name="system", content=EXTRACTION_PROMPT),
            UserMsg(
                name="user",
                content=(
                    "Current USER.md:\n"
                    f"{snapshot.user}\n\nCurrent MEMORY.md:\n"
                    f"{snapshot.memory}\n\nToday's daily memory:\n"
                    f"{current_daily or '(empty)'}\n\n"
                    f"Covered turns: {last_update + 1}-{turn_count}\n\n"
                    f"Recent conversation:\n{transcript}"
                ),
            ),
        ]
        try:
            response = await agent.model.generate_structured_output(
                messages=messages,
                structured_model=_MemoryExtraction,
            )
            extraction = _MemoryExtraction.model_validate(response.content)
            await store.apply_edits(
                "daily",
                add=[
                    (
                        item.section,
                        item.content,
                        item.create_section,
                    )
                    for item in extraction.daily.add
                ],
                replace=[
                    (item.old_text, item.new_text)
                    for item in extraction.daily.replace
                ],
                remove=extraction.daily.remove,
            )
            await store.apply_edits(
                "user",
                add=[
                    (
                        item.section,
                        item.content,
                        item.create_section,
                    )
                    for item in extraction.user.add
                ],
                replace=[
                    (item.old_text, item.new_text)
                    for item in extraction.user.replace
                ],
                remove=extraction.user.remove,
            )
            await store.apply_edits(
                "memory",
                add=[
                    (
                        item.section,
                        item.content,
                        item.create_section,
                    )
                    for item in extraction.memory.add
                ],
                replace=[
                    (item.old_text, item.new_text)
                    for item in extraction.memory.replace
                ],
                remove=extraction.memory.remove,
            )
            await store.mark_static_update(turn_count)
            self._snapshots.pop(self._snapshot_key(agent.state), None)
        except Exception as error:  # noqa: BLE001
            logger.warning(
                "File LTM extraction failed for session_id=%s: %s",
                agent.state.session_id,
                error,
            )

    def _render_recent_dialogue(self, context: list[Msg]) -> str:
        """Render recent non-system dialogue for structured extraction."""
        rendered = []
        for message in context[-self._extraction_window :]:
            if message.name == "memory" or message.role == "system":
                continue
            text = message.get_text_content()
            if text:
                rendered.append(
                    f"{message.role.upper()} ({message.name}): {text}",
                )
        return "\n\n".join(rendered)

    @staticmethod
    def _extract_user_text(inputs: Any) -> str | None:
        """Return new user text, excluding HITL continuation events."""
        if inputs is None or isinstance(
            inputs,
            (ExternalExecutionResultEvent, UserConfirmResultEvent),
        ):
            return None
        messages = inputs if isinstance(inputs, list) else [inputs]
        texts = []
        for message in messages:
            if isinstance(message, Msg) and message.role == "user":
                text = message.get_text_content()
                if text:
                    texts.append(text)
        return "\n".join(texts) if texts else None
