# -*- coding: utf-8 -*-
"""Filesystem-backed long-term memory middleware.

The middleware keeps a workspace-local Markdown memory store, injects a
bounded ``MEMORY.md`` index into the system prompt, and can asynchronously
surface relevant topic files as hint blocks during the reasoning loop.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncGenerator, Callable

from pydantic import BaseModel, Field

from ..._base import MiddlewareBase
from ...._logging import logger
from ...._utils._common import _estimate_tokens, _estimate_bytes
from ....message import Msg, SystemMsg, UserMsg, HintBlock
from ....model import ChatModelBase
from ....tool import BackendBase, LocalBackend

if TYPE_CHECKING:
    from ....agent import Agent


@dataclass(slots=True)
class _MemoryFileHeader:
    """Lightweight header for one memory file, read from frontmatter only."""

    filename: str
    """Relative path under the memory directory (e.g. ``user_role.md``)."""
    path: str
    """Absolute path inside the backend."""
    description: str | None
    """One-line description from frontmatter; ``None`` when absent."""
    type: str | None
    """Memory type tag from frontmatter (user/feedback/project/reference)."""
    mtime: float | None
    """Modification time as a Unix timestamp; ``None`` when unavailable."""


DEFAULT_MEMORY_INSTRUCTIONS = """# Auto Memory

You have a persistent, file-based memory system at `{memory_dir}`. This directory already exists — write to it directly with the `Write` tool (do not run mkdir or check for its existence).

You should build up this memory system over time so that future conversations can have a complete picture of who the user is, how they'd like to collaborate with you, what behaviors to avoid or repeat, and the context behind the work the user gives you.

If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If they ask you to forget something, find and remove the relevant entry.

## Types of memory

There are several discrete types of memory that you can store in your memory system:

<types>
<type>
    <name>user</name>
    <description>Contain information about the user's role, goals, responsibilities, and knowledge. Great user memories help you tailor your future behavior to the user's preferences and perspective. Your goal in reading and writing these memories is to build up an understanding of who the user is and how you can be most helpful to them specifically. For example, you should collaborate with a senior software engineer differently than a student who is coding for the very first time. Keep in mind, that the aim here is to be helpful to the user. Avoid writing memories about the user that could be viewed as a negative judgement or that are not relevant to the work you're trying to accomplish together.</description>
    <when_to_save>When you learn any details about the user's role, preferences, responsibilities, or knowledge</when_to_save>
    <how_to_use>When your work should be informed by the user's profile or perspective. For example, if the user is asking you to explain a part of the code, you should answer that question in a way that is tailored to the specific details that they will find most valuable or that helps them build their mental model in relation to domain knowledge they already have.</how_to_use>
    <examples>
    user: I'm a data scientist investigating what logging we have in place
    assistant: [saves user memory: user is a data scientist, currently focused on observability/logging]

    user: I've been writing Go for ten years but this is my first time touching the React side of this repo
    assistant: [saves user memory: deep Go expertise, new to React and this project's frontend — frame frontend explanations in terms of backend analogues]
    </examples>
</type>
<type>
    <name>feedback</name>
    <description>Guidance the user has given you about how to approach work — both what to avoid and what to keep doing. These are a very important type of memory to read and write as they allow you to remain coherent and responsive to the way you should approach work in the project. Record from failure AND success: if you only save corrections, you will avoid past mistakes but drift away from approaches the user has already validated, and may grow overly cautious.</description>
    <when_to_save>Any time the user corrects your approach ("no not that", "don't", "stop doing X") OR confirms a non-obvious approach worked ("yes exactly", "perfect, keep doing that", accepting an unusual choice without pushback). Corrections are easy to notice; confirmations are quieter — watch for them. In both cases, save what is applicable to future conversations, especially if surprising or not obvious from the code. Include *why* so you can judge edge cases later.</when_to_save>
    <how_to_use>Let these memories guide your behavior so that the user does not need to offer the same guidance twice.</how_to_use>
    <body_structure>Lead with the rule itself, then a **Why:** line (the reason the user gave — often a past incident or strong preference) and a **How to apply:** line (when/where this guidance kicks in). Knowing *why* lets you judge edge cases instead of blindly following the rule.</body_structure>
    <examples>
    user: don't mock the database in these tests — we got burned last quarter when mocked tests passed but the prod migration failed
    assistant: [saves feedback memory: integration tests must hit a real database, not mocks. Reason: prior incident where mock/prod divergence masked a broken migration]

    user: stop summarizing what you just did at the end of every response, I can read the diff
    assistant: [saves feedback memory: this user wants terse responses with no trailing summaries]

    user: yeah the single bundled PR was the right call here, splitting this one would've just been churn
    assistant: [saves feedback memory: for refactors in this area, user prefers one bundled PR over many small ones. Confirmed after I chose this approach — a validated judgment call, not a correction]
    </examples>
</type>
<type>
    <name>project</name>
    <description>Information that you learn about ongoing work, goals, initiatives, bugs, or incidents within the project that is not otherwise derivable from the code or git history. Project memories help you understand the broader context and motivation behind the work the user is doing within this working directory.</description>
    <when_to_save>When you learn who is doing what, why, or by when. These states change relatively quickly so try to keep your understanding of this up to date. Always convert relative dates in user messages to absolute dates when saving (e.g., "Thursday" → "2026-03-05"), so the memory remains interpretable after time passes.</when_to_save>
    <how_to_use>Use these memories to more fully understand the details and nuance behind the user's request and make better informed suggestions.</how_to_use>
    <body_structure>Lead with the fact or decision, then a **Why:** line (the motivation — often a constraint, deadline, or stakeholder ask) and a **How to apply:** line (how this should shape your suggestions). Project memories decay fast, so the why helps future-you judge whether the memory is still load-bearing.</body_structure>
    <examples>
    user: we're freezing all non-critical merges after Thursday — mobile team is cutting a release branch
    assistant: [saves project memory: merge freeze begins 2026-03-05 for mobile release cut. Flag any non-critical PR work scheduled after that date]

    user: the reason we're ripping out the old auth middleware is that legal flagged it for storing session tokens in a way that doesn't meet the new compliance requirements
    assistant: [saves project memory: auth middleware rewrite is driven by legal/compliance requirements around session token storage, not tech-debt cleanup — scope decisions should favor compliance over ergonomics]
    </examples>
</type>
<type>
    <name>reference</name>
    <description>Stores pointers to where information can be found in external systems. These memories allow you to remember where to look to find up-to-date information outside of the project directory.</description>
    <when_to_save>When you learn about resources in external systems and their purpose. For example, that bugs are tracked in a specific project in Linear or that feedback can be found in a specific Slack channel.</when_to_save>
    <how_to_use>When the user references an external system or information that may be in an external system.</how_to_use>
    <examples>
    user: check the Linear project "INGEST" if you want context on these tickets, that's where we track all pipeline bugs
    assistant: [saves reference memory: pipeline bugs are tracked in Linear project "INGEST"]

    user: the Grafana board at grafana.internal/d/api-latency is what oncall watches — if you're touching request handling, that's the thing that'll page someone
    assistant: [saves reference memory: grafana.internal/d/api-latency is the oncall latency dashboard — check it when editing request-path code]
    </examples>
</type>
</types>

## What NOT to save in memory

- Code patterns, conventions, architecture, file paths, or project structure — these can be derived by reading the current project state.
- Git history, recent changes, or who-changed-what — `git log` / `git blame` are authoritative.
- Debugging solutions or fix recipes — the fix is in the code; the commit message has the context.
- Anything already documented in CLAUDE.md files.
- Ephemeral task details: in-progress work, temporary state, current conversation context.

These exclusions apply even when the user explicitly asks you to save. If they ask you to save a PR list or activity summary, ask what was *surprising* or *non-obvious* about it — that is the part worth keeping.

## How to save memories

Saving a memory is a two-step process:

**Step 1** — write the memory to its own file (e.g., `user_role.md`, `feedback_testing.md`) using this frontmatter format:

```markdown
---
name: {{memory name}}
description: {{one-line description — used to decide relevance in future conversations, so be specific}}
type: {{user, feedback, project, reference}}
---

{{memory content — for feedback/project types, structure as: rule/fact, then **Why:** and **How to apply:** lines}}
```

**Step 2** — add a pointer to that file in `MEMORY.md`. `MEMORY.md` is an index, not a memory — each entry should be one line, under ~150 characters: - [Title](file.md) — one-line hook. It has no frontmatter. Never write memory content directly into MEMORY.md.

- MEMORY.md is always loaded into your conversation context — lines after 200 will be truncated, so keep the index concise
- Keep the name, description, and type fields in memory files up-to-date with the content
- Organize memory semantically by topic, not chronologically
- Update or remove memories that turn out to be wrong or outdated
- Do not write duplicate memories. First check if there is an existing memory you can update before writing a new one.

## When to access memories
- When memories seem relevant, or the user references prior-conversation work.
- You MUST access memory when the user explicitly asks you to check, recall, or remember.
- If the user says to ignore or not use memory: proceed as if MEMORY.md were empty. Do not apply remembered facts, cite, compare against, or mention memory content.
- Memory records can become stale over time. Use memory as context for what was true at a given point in time. Before answering the user or building assumptions based solely on information in memory records, verify that the memory is still correct and up-to-date by reading the current state of the files or resources. If a recalled memory conflicts with current information, trust what you observe now — and update or remove the stale memory rather than acting on it.

## Before recommending from memory

A memory that names a specific function, file, or flag is a claim that it existed *when the memory was written*. It may have been renamed, removed, or never merged. Before recommending it:
- If the memory names a file path: check the file exists.
- If the memory names a function or flag: grep for it.
- If the user is about to act on your recommendation (not just asking about history), verify first.

"The memory says X exists" is not the same as "X exists now."

A memory that summarizes repo state (activity logs, architecture snapshots) is frozen in time. If the user asks about *recent* or *current* state, prefer `git log` or reading the code over recalling the snapshot.

## Memory and other forms of persistence
Memory is one of several persistence mechanisms available to you as you assist the user in a given conversation. The distinction is often that memory can be recalled in future conversations and should not be used for persisting information that is only useful within the scope of the current conversation.
- When to use or update a plan instead of memory: If you are about to start a non-trivial implementation task and would like to reach alignment with the user on your approach you should use a Plan rather than saving this information to memory. Similarly, if you already have a plan within the conversation and you have changed your approach persist that change by updating the plan rather than saving a memory.
- When to use or update tasks instead of memory: When you need to break your work in current conversation into discrete steps or keep track of your progress use tasks instead of saving to memory. Tasks are great for persisting information about the work that needs to be done in the current conversation, but memory should be reserved for information that will be useful in future conversations.

## Searching past context

When looking for past context:
1. Search topic files in your memory directory:
```
Grep with pattern="<search>" path="{memory_dir}" glob="*.md"</search>
# or Bash command:
grep -rn "<search term>" {memory_dir} --include="*.md"
```
Use narrow search terms (error messages, file paths, function names) rather than broad keywords.
"""  # noqa:

DEFAULT_RETRIEVAL_INSTRUCTIONS = (
    "You are selecting memory files that will be useful as context for "
    "processing a user's query. You will be given the user's query and a "
    "list of available memory files with their filenames and descriptions.\n\n"
    "Return a list of filenames for the memories that will clearly be "
    "useful (up to 5). Only include memories that you are certain will be "
    "helpful based on their name and description.\n"
    "- If you are unsure whether a memory will be useful, do not include "
    "it. Be selective and discerning.\n"
    "- If no memories would clearly be useful, return an empty list."
)


class _MemorySelection(BaseModel):
    """Structured output schema for the memory relevance selector."""

    selected_files: list[str] = Field(
        description=(
            "Filenames of the memory files to surface, relative to the "
            "memory directory (e.g. 'user_role.md'). Up to 5 entries."
        ),
    )


class AgenticMemoryMiddleware(MiddlewareBase):
    """The agentic memory, where the LLM decides when and what to save,
    together with an asyncio retrieval task in each reply. The memory is
    stored and retrieval based on the Markdown files.

    The `AgenticMemoryMiddleware` supports different backends via the
    `backend` argument in its constructor.
    """

    FILENAME_MEMORY_MD: str = "MEMORY.md"

    class Parameters(BaseModel):
        """The user-tunable filesystem parameters."""

        model_config = {"arbitrary_types_allowed": True}

        memory_max_tokens: int = Field(
            default=4_000,
            title="MEMORY.md Max Length",
            description=(
                "The maximum tokens of the MEMORY.md inserted into the system "
                "prompt."
            ),
        )

        memory_instructions: str = Field(
            default=DEFAULT_MEMORY_INSTRUCTIONS,
            title="Memory Instructions",
            description=(
                "The default instructions appended to the system prompt "
                "before the MEMORY.md snapshots."
            ),
        )

        retrieval_async: bool = Field(
            default=True,
            title="Async Retrieval",
            description=(
                "Whether to retrieve relevant memory files asynchronously "
                "during the agent reply. If `True`, an async retrieval task "
                "will be started."
            ),
        )

        retrieval_model: ChatModelBase | None = Field(
            default=None,
            title="Retrieval Model",
            description=(
                "The LLM used to retrieve relevant memory files "
                "asynchronously during the agent reply. If `None`, the "
                "agent's model will be used."
            ),
        )

        retrieval_max_tokens_per_md: int = Field(
            default=2_000,
            title="Retrieval Max Tokens Per File",
            description=(
                "Maximum tokens read from each memory file that is surfaced "
                "by the relevance retrieval step. Keeps individual files from "
                "flooding the context window."
            ),
        )

        retrieval_max_files: int = Field(
            default=200,
            title="Retrieval Max Files",
            description=(
                "The maximum number of Markdown memory files to consider "
                "during relevance selection."
            ),
        )

        retrieval_max_tokens_per_frontmatter: int = Field(
            default=256,
            title="Retrieval Max Tokens per Frontmatter",
            description=(
                "The maximum number of tokens to read from the beginning of "
                "each Markdown file when parsing frontmatter."
            ),
        )

        retrieval_instructions: str = Field(
            default=DEFAULT_RETRIEVAL_INSTRUCTIONS,
            title="Retrieval Instructions",
            description=(
                "The instructions used to select relevant memory files for a "
                "given user query in the asynchronous retrieval task."
            ),
        )

    def __init__(
        self,
        *,
        workdir: str,
        memory_dir: str = "Memory",
        parameters: Parameters | None = None,
        backend: BackendBase | None = None,
    ) -> None:
        """Initialize filesystem-backed long-term memory behavior.

        Args:
            workdir (`str`):
                The working directory of this agent, used to store the agentic
                searchable memory files.
            memory_dir (`str`, defaults to ``"Memory"``):
                The directory to store the long-term memory files, including
                ``MEMORY.md``.
            parameters (`AgenticMemoryMiddleware.Parameters | None`, \
            defaults to ``None``):
                User-tunable parameters.  When ``None``, defaults are used.
            backend (`BackendBase | None`, optional):
                The backend to switch between local and remote storage.
                When ``None``, a local filesystem is used.
        """
        self._workdir = workdir
        self._memory_dir = memory_dir
        self._parameters = parameters or self.Parameters()
        self._backend = backend or LocalBackend()

        self._cached_input: str | None = None
        # The in-flight asynchronous retrieval task started in ``on_reply``
        # and consumed in ``on_reasoning``. Kept on the instance so the
        # reasoning hook can poll for completion across iterations.
        self._retrieval_task: asyncio.Task | None = None

    @staticmethod
    def _truncate_if_needed(content: str, max_length: int) -> str:
        """Return ``content`` truncated to at most ``max_length`` tokens.

        Args:
            content (`str`):
                The content to truncate.
            max_length (`int`):
                The maximum estimated token count to keep.

        Returns:
            `str`:
                The original content when it already fits; otherwise a prefix
                that fits within the requested token budget.
        """
        if max_length <= 0:
            return ""

        n_tokens = _estimate_tokens(content)
        if n_tokens <= max_length:
            return content

        index = int((max_length / n_tokens) * len(content))
        while index > 0 and _estimate_tokens(content[:index]) > max_length:
            index = max(0, index - 10)

        return content[:index]

    async def on_system_prompt(
        self,
        agent: "Agent",
        current_prompt: str,
    ) -> str:
        """Append memory instructions and a bounded ``MEMORY.md`` snapshot.

        Args:
            agent (`Agent`):
                The executing agent. Unused, but part of the middleware
                contract.
            current_prompt (`str`):
                The system prompt produced by previous middleware.

        Returns:
            `str`:
                The prompt with filesystem memory instructions appended.
        """
        await self._ensure_layout()
        memory_md_content = await self._get_memory_md_content() or ""

        # Truncated by config
        memory_md_truncated = self._truncate_if_needed(
            memory_md_content,
            self._parameters.memory_max_tokens,
        )

        if len(memory_md_truncated) != len(memory_md_content):
            memory_md_path = self._get_memory_md_path()
            remain_lines = len(memory_md_truncated.split("\n"))
            omitted_lines = len(memory_md_content.split("\n")) - remain_lines
            memory_md_truncated += (
                "\n<<<TRUNCATED>>>\n<system-reminder>The remaining "
                f"{omitted_lines} lines have been omitted due to context "
                "length limits. Use the `Read` tool with offset "
                f"`{remain_lines}` to access the rest of '{memory_md_path}'."
                f"</system-reminder>"
            )

        if not memory_md_truncated:
            memory_md_truncated = (
                "Your MEMORY.md is currently empty. When you save new "
                "memories, they will appear here."
            )

        memory_instructions = self._parameters.memory_instructions.replace(
            "{memory_dir}",
            self._get_memory_dir(),
        )
        content = (
            f"{memory_instructions}\n" f"## MEMORY.md\n{memory_md_truncated}"
        )

        return f"{current_prompt}\n\n{content}"

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Cache the user input and kick off an asynchronous retrieval task
        that runs concurrently with the agent reply.

        Args:
            agent (`Agent`):
                The executing agent whose model may be used for retrieval.
            input_kwargs (`dict`):
                Reply input kwargs forwarded unchanged.
            next_handler (`Callable[..., AsyncGenerator]`):
                The downstream middleware or core reply logic.

        Yields:
            `Any`:
                Items yielded by ``next_handler``.
        """

        if self._parameters.retrieval_async:
            inputs = input_kwargs.get("inputs")

            msgs = None
            if isinstance(inputs, list) and all(
                isinstance(_, Msg) for _ in inputs
            ):
                msgs = inputs
            elif isinstance(inputs, Msg):
                msgs = [inputs]

            if msgs is not None:
                self._cached_input = "\n".join(
                    [
                        f"{_.name}: " + _.get_text_content()
                        for _ in msgs
                        if _.get_text_content() is not None
                    ],
                )

            # Start an asynchronous retrieval task that uses an LLM to decide
            # which memory files are relevant to the current user input. The
            # result is consumed by ``on_reasoning``.
            if self._cached_input:
                self._retrieval_task = asyncio.create_task(
                    self._retrieve_relevant_files(agent, self._cached_input),
                )

        try:
            async for _ in next_handler(**input_kwargs):
                yield _
        finally:
            # Ensure the retrieval task does not outlive the reply.
            if (
                self._retrieval_task is not None
                and not self._retrieval_task.done()
            ):
                self._retrieval_task.cancel()
                try:
                    await self._retrieval_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            self._retrieval_task = None
            self._cached_input = None

    async def on_reasoning(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Check if the retrieval finished and if yes, insert a hint block to
        the content.

        Args:
            agent (`Agent`):
                The executing agent whose context may receive a hint block.
            input_kwargs (`dict`):
                Reasoning input kwargs forwarded unchanged.
            next_handler (`Callable[..., AsyncGenerator]`):
                The downstream middleware or core reasoning logic.

        Yields:
            `Any`:
                Items yielded by ``next_handler``.
        """
        # Poll the in-flight retrieval task; if it has finished, consume its
        # result and inject it into the agent context exactly once.
        if self._retrieval_task is not None and self._retrieval_task.done():
            task = self._retrieval_task
            self._retrieval_task = None
            try:
                retrieval_result = task.result()
            except (asyncio.CancelledError, Exception):
                retrieval_result = None

            if retrieval_result:
                agent.state.append_context(
                    agent.name,
                    [
                        HintBlock(
                            hint=retrieval_result,
                        ),
                    ],
                )

        async for event in next_handler(**input_kwargs):
            yield event

    # ========================================================================
    # Helper functions
    # ========================================================================

    @staticmethod
    def _format_manifest(headers: list[_MemoryFileHeader]) -> str:
        """Format a list of memory file headers into a one-line-per-file
        manifest string suitable for the selector prompt.

        Args:
            headers (`list[_MemoryFileHeader]`):
                The memory file headers to format.

        Returns:
            `str`:
                The formatted manifest.
        """
        lines = []
        for h in headers:
            tag = f"[{h.type}] " if h.type else ""
            if h.mtime is not None:
                from datetime import datetime

                ts = datetime.fromtimestamp(h.mtime).strftime("%Y-%m-%d")
            else:
                ts = "unknown"
            desc = f": {h.description}" if h.description else ""
            lines.append(f"- {tag}{h.filename} ({ts}){desc}")
        return "\n".join(lines)

    async def _retrieve_relevant_files(
        self,
        agent: "Agent",
        query: str,
    ) -> str | None:
        """Use an LLM to identify memory files relevant to ``query`` and
        return their content as an injectable string.

        Args:
            agent (`Agent`):
                The agent whose model / memory store should be consulted.
            query (`str`):
                The cached user input used as the retrieval query.

        Returns:
            `str | None`:
                The formatted retrieval result ready to be injected into the
                context, or ``None`` when nothing relevant was found.
        """
        await self._ensure_layout()

        # 1. Scan available memory files (frontmatter only, cheap).
        headers = await self._list_md_files()
        if not headers:
            return None

        valid_filenames = {h.filename for h in headers}
        manifest = self._format_manifest(headers)

        # 2. Ask the model to select relevant files.
        model = self._parameters.retrieval_model or agent.model
        res = await model.generate_structured_output(
            [
                SystemMsg(
                    name="system",
                    content=self._parameters.retrieval_instructions,
                ),
                UserMsg(
                    name="user",
                    content=(
                        f"Query: {query}\n\n"
                        f"Available memories:\n{manifest}"
                    ),
                ),
            ],
            structured_model=_MemorySelection,
        )

        # 3. Validate: discard hallucinated filenames.
        raw_selected: list[str] = res.content.get("selected_files", [])
        selected = [f for f in raw_selected if f in valid_filenames][:5]
        if not selected:
            return None

        # 4. Read each selected file and format as an injectable string.
        # Cap each file to avoid flooding the context window.
        header_by_filename = {h.filename: h for h in headers}
        parts: list[str] = []
        for filename in selected:
            h = header_by_filename[filename]
            try:
                content = (await self._backend.read_file(h.path)).decode(
                    "utf-8",
                    errors="replace",
                )
            except Exception:
                continue

            # Truncate large files to avoid flooding the context window.
            content = self._truncate_if_needed(
                content,
                self._parameters.retrieval_max_tokens_per_md,
            )
            if h.mtime is not None:
                import time

                days = max(0, int((time.time() - h.mtime) / 86_400))
                if days == 0:
                    age = "today"
                elif days == 1:
                    age = "yesterday"
                else:
                    age = f"{days} days ago"
                header = f"Memory (saved {age}): {h.path}:"
            else:
                header = f"Memory: {h.path}:"

            parts.append(f"{header}\n\n{content}")

        if not parts:
            return None

        return "\n\n---\n\n".join(parts)

    async def _ensure_layout(self) -> None:
        """Create the memory directory and initial files idempotently.

        Existing human-edited documents are never replaced. The index file is
        created only when absent so manual edits survive restarts. The memory
        directory itself is materialized as a side effect of writing
        ``MEMORY.md`` — :meth:`BackendBase.write_file` creates parent
        directories — which avoids a platform-specific ``mkdir -p`` shell
        invocation that is not portable on Windows.
        """
        if not await self._backend.file_exists(self._get_memory_md_path()):
            logger.info(
                "Creating 'MEMORY.md' file in '%s'",
                self._workdir,
            )
            await self._backend.write_file(
                self._get_memory_md_path(),
                b"",
            )

    def _get_memory_dir(self) -> str:
        """Get the memory directory.

        Returns:
            `str`:
                The backend path of the memory directory.
        """
        return self._backend.join_path(self._workdir, self._memory_dir)

    def _get_memory_md_path(self) -> str:
        """Get the ``MEMORY.md`` path.

        Returns:
            `str`:
                The backend path of the ``MEMORY.md`` index file.
        """
        return self._backend.join_path(
            self._get_memory_dir(),
            self.FILENAME_MEMORY_MD,
        )

    async def _get_memory_md_content(self) -> str | None:
        """Get the content of the ``MEMORY.md`` file.

        Returns:
            `str | None`:
                The decoded index file content, or ``None`` when the file does
                not exist.
        """
        if not await self._backend.file_exists(self._get_memory_md_path()):
            return None

        return (
            await self._backend.read_file(self._get_memory_md_path())
        ).decode(
            "utf-8",
            errors="replace",
        )

    _FRONTMATTER_RE = re.compile(
        r"^\s*---\s*\n(?P<body>.*?)\n---\s*\n",
        re.DOTALL,
    )
    _FIELD_RE = re.compile(r"^(?P<key>\w+)\s*:\s*(?P<value>.+)$", re.MULTILINE)

    @classmethod
    def _parse_frontmatter_fields(cls, content: str) -> dict[str, str]:
        """Return a dict of YAML-like key/value pairs from the first
        frontmatter block.  Only scalar ``key: value`` lines are parsed;
        nested structures are intentionally ignored.

        Args:
            content (`str`):
                The Markdown content prefix to inspect.

        Returns:
            `dict[str, str]`:
                Parsed frontmatter fields, or an empty dict when no leading
                frontmatter block is present.
        """
        m = cls._FRONTMATTER_RE.match(content)
        if not m:
            return {}
        return {
            fm.group("key"): fm.group("value").strip()
            for fm in cls._FIELD_RE.finditer(m.group("body"))
        }

    async def _list_md_files(self) -> list[_MemoryFileHeader]:
        """Scan the memory directory for individual memory files.

        Returns:
            `list[_MemoryFileHeader]`:
                Memory file headers sorted newest-first and capped by
                ``retrieval_max_files``. The system index file
                (``MEMORY.md``) is excluded so only topic files are returned.
        """
        memory_dir = self._get_memory_dir()
        system_files = {self.FILENAME_MEMORY_MD}

        try:
            all_entries = await self._backend.list_dir(
                memory_dir,
                recursive=True,
            )
        except Exception:
            return []

        headers: list[_MemoryFileHeader] = []
        memory_dir_norm = self._backend.normpath(memory_dir)
        memory_dir_prefix = self._backend.join_path(memory_dir_norm, "")
        for entry in all_entries:
            entry_path = self._backend.normpath(entry)
            if self._backend.isabs(entry_path):
                if not entry_path.startswith(memory_dir_prefix):
                    continue
                filename = entry_path[len(memory_dir_prefix) :]
                full_path = entry_path
            else:
                filename = entry_path
                full_path = self._backend.join_path(memory_dir, filename)

            if not filename.endswith(".md") or filename in system_files:
                continue

            try:
                raw = await self._backend.read_file(full_path)
                # Only parse the leading bytes to keep this cheap.
                max_frontmatter_bytes = _estimate_bytes(
                    self._parameters.retrieval_max_tokens_per_frontmatter,
                )
                snippet = raw[:max_frontmatter_bytes].decode(
                    "utf-8",
                    errors="replace",
                )
                fields = self._parse_frontmatter_fields(snippet)
                mtime = await self._backend.stat_mtime(full_path)
                headers.append(
                    _MemoryFileHeader(
                        filename=filename,
                        path=full_path,
                        description=fields.get("description") or None,
                        type=fields.get("type") or None,
                        mtime=mtime,
                    ),
                )
            except Exception:
                continue

        headers.sort(key=lambda h: h.mtime or 0.0, reverse=True)
        return headers[: self._parameters.retrieval_max_files]
