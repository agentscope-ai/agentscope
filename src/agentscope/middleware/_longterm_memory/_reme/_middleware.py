# -*- coding: utf-8 -*-
"""ReMe-backed long-term memory middleware for AgentScope agents.

`ReMe <https://github.com/agentscope-ai/ReMe>`_ is a file-based memory
toolkit built on AgentScope. This middleware **embeds the ReMe
application in-process** (no separate service to run); a chat model for
ReMe's LLM-backed jobs is configured once at construction.

ReMe records memory by **listening to the conversation through the
``on_reply`` hook** — after every reply the new exchange is written back
via ReMe's ``auto_memory`` job, in *all* modes. The agent never writes
memory itself; there is no manual add tool. The ``mode`` parameter only
controls **retrieval**:

- ``"static_control"`` — search ReMe when a reply starts and inject the
  retrieved memories into context before a later reasoning step (plus the
  automatic write-back). Retrieval runs concurrently with the reply, so
  injection is best-effort: a single-shot reply may finish first.
- ``"agent_control"`` — expose a ``memory_search`` tool the agent calls
  on demand (plus the automatic write-back); no auto-retrieval.
- ``"both"`` — auto-retrieve/inject *and* expose ``memory_search``.

ReMe scopes writes by ``session_id``, which is read from
``agent.state.session_id`` at hook time (not configured on the
middleware), so it always matches the agent's own session; search runs
over the whole workspace.
"""
from __future__ import annotations

import asyncio
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Literal,
    TYPE_CHECKING,
)

from pydantic import BaseModel, Field

from ..._base import MiddlewareBase
from ...._logging import logger
from ....embedding import EmbeddingModelBase
from ....message import AssistantMsg, HintBlock, Msg
from ....model import ChatModelBase
from ._tools import _build_memory_tools
from ._utils import _extract_memory_texts, _extract_query_text

if TYPE_CHECKING:
    from ....agent import Agent
    from ....tool import ToolBase


# Jobs exposed by the ReMe application that this middleware drives.
_SEARCH_JOB = "search"
_AUTO_MEMORY_JOB = "auto_memory"

# ReMe component coordinates for the default chat-model / embedding
# backends. ReMe starts both eagerly at ``start()`` (the embedding
# component is wired in even when the default file store searches
# keyword-only), so an injected model bypasses ReMe building its own
# from credentials — the same escape hatch for both components.
_AS_LLM_COMPONENT = "as_llm"
_AS_EMBEDDING_COMPONENT = "as_embedding"
_AS_DEFAULT = "default"

# Header rendered above retrieved memories injected into context.
_MEMORY_MSG_NAME = "memory"
_MEMORY_SECTION_HEADER = "## Relevant memories from past conversations"
_MEMORY_SECTION_INTRO = (
    "The following memories about the user may be relevant. "
    "Use them only if they are pertinent to the current request."
)

# Appended to the system prompt to advertise the search tool to the LLM.
_TOOL_INSTRUCTIONS = (
    "## Long-term memory\n\n"
    "You have a `memory_search` tool available. Use it whenever the "
    "current conversation may depend on a durable fact from a past "
    "session (a preference, a name, a prior decision). Recording memory "
    "is handled automatically; there is no add tool."
)


class ReMeMiddleware(MiddlewareBase):
    """AgentScope middleware that adds long-term memory backed by
    `ReMe <https://github.com/agentscope-ai/ReMe>`_.

    ReMe is embedded **in-process** (no separate service): the middleware
    instantiates a :class:`reme.ReMe` application whose LLM-backed jobs use
    the ``chat_model`` configured at construction. The app is built and
    owned by the middleware — pass ``workspace_dir`` / ``config`` (and
    optionally a ``Parameters`` with ``chat_model`` / ``embedding_model``)
    and it is created lazily on first use. When ``chat_model`` is omitted,
    ReMe uses the LLM from its own config/credentials. Providing an
    ``embedding_model`` enables ReMe's vector store; otherwise search stays
    keyword-only.

    The model is fixed at construction (never taken from an agent), so the
    embedded app's single LLM is well-defined even when one middleware
    instance is shared across several agents. Per-conversation state — the
    ReMe ``session_id`` — is instead read live from each agent at hook
    time and never stored, keeping shared use isolated. Background
    retrieval tasks are likewise tracked per ``session_id`` so concurrent
    replies in different sessions never clobber each other's in-flight
    search.

    AgentScope middleware has no framework-managed lifecycle, so the app
    is built once and started lazily on first use (idempotent). Call
    :meth:`close` for explicit teardown of the embedded app.

    Example::

        from agentscope.middleware import ReMeMiddleware
        from agentscope.tool import Toolkit

        mw = ReMeMiddleware(
            workspace_dir=".reme",
            parameters=ReMeMiddleware.Parameters(mode="both"),
        )
        agent = Agent(
            ...,
            toolkit=Toolkit(tools=await mw.list_tools()),
            middlewares=[mw],
        )
    """

    class Parameters(BaseModel):
        """User-tunable ReMe memory parameters.

        The agent service parses this schema to render a configuration
        form. Structural wiring (``workspace_dir`` / ``config``) stays on
        the constructor.
        """

        model_config = {"arbitrary_types_allowed": True}

        chat_model: ChatModelBase | None = Field(
            default=None,
            title="Chat Model",
            description=(
                "AgentScope chat model injected into ReMe's default LLM "
                "component, fixed for the lifetime of the embedded app. "
                "When `None`, ReMe uses the LLM from its own "
                "config/credentials. Needed for `auto_memory` write-back."
            ),
        )

        embedding_model: EmbeddingModelBase | None = Field(
            default=None,
            title="Embedding Model",
            description=(
                "AgentScope embedding model injected into ReMe's default "
                "embedding component, fixed for the lifetime of the embedded "
                "app. ReMe starts this component eagerly (it is wired into "
                "the file store even when search is keyword-only), so "
                "injecting a model bypasses ReMe building its own from "
                "credentials. When `None`, ReMe uses the embedding backend "
                "from its own config/credentials."
            ),
        )

        mode: Literal["static_control", "agent_control", "both"] = Field(
            default="both",
            title="Retrieval Mode",
            description=(
                "How the agent retrieves from ReMe (write-back runs "
                "automatically in every mode). `static_control`: search and "
                "inject before each reply, no tool. `agent_control`: expose "
                "the `memory_search` tool, no auto-retrieval. `both`: "
                "auto-retrieve/inject and expose the tool."
            ),
        )

        top_k: int = Field(
            default=5,
            title="Top K",
            description=(
                "Max number of memories retrieved per search, and the "
                "default `limit` advertised by the `memory_search` tool."
            ),
        )

    def __init__(
        self,
        *,
        workspace_dir: str = ".reme",
        config: str = "default",
        parameters: Parameters | None = None,
    ) -> None:
        """Initialize the ReMe middleware.

        The ReMe ``session_id`` (which scopes write-back memory cards) is
        **not** taken here — it is read from ``agent.state.session_id`` at
        hook time (every agent has one; ``AgentState`` generates it),
        mirroring :class:`TracingMiddleware`. To pin a resumable session,
        set the id on the agent (``Agent(state=AgentState(session_id=...))``).

        Args:
            workspace_dir (`str`, optional):
                ReMe workspace (vault) directory for memory cards and
                indexes. Defaults to ``".reme"``.
            config (`str`, optional):
                ReMe config name or path. Defaults to ``"default"``
                (ReMe's bundled ``default.yaml``).
            parameters (`ReMeMiddleware.Parameters | None`, optional):
                User-tunable parameters (``chat_model`` / ``embedding_model``
                / ``mode`` / ``top_k``) whose schema the agent service
                renders as a configuration form. When ``None``, defaults are
                used. Providing an ``embedding_model`` also enables ReMe's
                vector store (otherwise search is keyword-only).
        """
        # Embedded ReMe application state. The app is built lazily and
        # started once (idempotent guards), since middleware has no
        # framework-managed lifecycle. The middleware always owns the app
        # it builds, so :meth:`close` tears it down.
        self._app: Any | None = None
        self._started = False
        self._workspace_dir = workspace_dir
        self._config = config
        self._parameters = parameters or self.Parameters()
        # In-flight background retrieval per session (started in ``on_reply``,
        # consumed/injected in ``on_reasoning``, cleaned up in ``on_reply``'s
        # finally). Keyed by ``session_id`` so one middleware shared across
        # agents keeps each session's retrieval isolated — a concurrent reply
        # in another session never clobbers this one's task.
        self._retrieval_tasks: dict[Any, asyncio.Task] = {}

    # ==================================================================
    # Embedded ReMe application lifecycle
    # ==================================================================
    def _build_app(self) -> Any:
        """Lazily build the embedded :class:`reme.ReMe` application.

        Raises:
            ImportError:
                If ``reme-ai`` is not installed.
        """
        try:
            from reme import ReMe
            from reme.config import resolve_app_config
        except ImportError as e:  # pragma: no cover - import guard
            raise ImportError(
                "ReMeMiddleware requires the `reme-ai` package. Install "
                'it with `pip install "agentscope[reme]"` (or '
                "`pip install reme-ai`).",
            ) from e

        app_kwargs: dict[str, Any] = {
            "config": self._config,
            "workspace_dir": self._workspace_dir,
            "enable_logo": False,
            "log_to_console": False,
        }
        # An embedding model turns on ReMe's vector store: the bundled
        # ``default`` config ships it off (BM25 keyword search only), so we
        # wire the file store to the default embedding store when — and
        # only when — a model is available to build the vectors.
        if self._parameters.embedding_model is not None:
            app_kwargs["components"] = {
                "file_store": {"default": {"embedding_store": "default"}},
            }
        app_config = resolve_app_config(**app_kwargs)
        return ReMe(**app_config)

    async def _ensure_started(self) -> None:
        """Build (if needed) and start the embedded app (idempotent).

        The configured ``chat_model`` / ``embedding_model`` are injected
        into ReMe's default LLM and embedding components **before** the
        one-time ``start()`` (ReMe's ``BaseAsLLM._start`` /
        ``BaseAsEmbedding._start`` skip building a model from credentials
        when ``model`` is already set). Both are fixed at construction —
        never taken from an agent — so the embedded app (which has a
        single LLM and a single embedding component) has one well-defined
        model each regardless of how many agents share this middleware.
        When either is ``None``, ReMe falls back to the backend configured
        in its own config/credentials.
        """
        if self._app is None:
            self._app = self._build_app()
        if not self._started:
            if self._parameters.chat_model is not None:
                await self._app.update_component(
                    _AS_LLM_COMPONENT,
                    _AS_DEFAULT,
                    model=self._parameters.chat_model,
                )
            if self._parameters.embedding_model is not None:
                await self._app.update_component(
                    _AS_EMBEDDING_COMPONENT,
                    _AS_DEFAULT,
                    model=self._parameters.embedding_model,
                )
            await self._app.start()
            self._started = True

    async def close(self) -> None:
        """Close the embedded ReMe app.

        AgentScope does not manage middleware lifecycle, so call this
        explicitly for clean teardown (e.g. on application shutdown).
        """
        if self._app is not None and self._started:
            await self._app.close()
        self._started = False

    @staticmethod
    def _session_id_of(agent: "Agent") -> str | None:
        """Read the ReMe ``session_id`` live from the agent.

        ReMe scopes write-back memory cards by ``session_id``. It is read
        from ``agent.state.session_id`` at hook time and threaded through
        per call — **never** stored on the middleware — so a single
        instance shared across agents keeps each conversation's writes
        isolated. Mirrors how :class:`TracingMiddleware` sources the
        session from the agent rather than from middleware config.
        """
        return getattr(getattr(agent, "state", None), "session_id", None)

    # ------------------------------------------------------------------
    # Hook: on_reply
    # ------------------------------------------------------------------
    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        session_id = self._session_id_of(agent)

        # Kick off retrieval (static_control / both only) concurrently with
        # the reply. It runs in the background while the agent ingests input
        # and starts reasoning; ``on_reasoning`` injects the result once the
        # task finishes. Write the new exchange back afterwards in every mode.
        inputs = input_kwargs.get("inputs")
        query_text = _extract_query_text(inputs)

        # Discard any stale task left for this session (a previous turn that
        # never reached its finally is unexpected, but never leak one).
        stale = self._retrieval_tasks.pop(session_id, None)
        if stale is not None and not stale.done():
            stale.cancel()
        if self._parameters.mode != "agent_control" and query_text:
            self._retrieval_tasks[session_id] = asyncio.create_task(
                self._search(query_text),
            )

        # Snapshot the context BEFORE the turn so the write-back persists
        # only this turn's *increment*. ReMe's ``auto_memory`` consumes the
        # incremental exchange, whereas ``agent.state.context`` is the full
        # accumulated history — so we diff by message id (below) rather than
        # sending the whole context, which would re-feed every prior turn.
        # Taking the increment (not just the final message) still captures
        # every step of the turn — user input, each assistant step, and
        # every tool call / tool result — which the agent records on
        # ``state.context`` via ``_save_to_context`` but does not all yield
        # on the stream (only the final answer is yielded).
        pre_ids = {m.id for m in agent.state.context if isinstance(m, Msg)}

        try:
            async for item in next_handler(**input_kwargs):
                yield item
        finally:
            # Retrieval may still be running (or its hint may never have been
            # injected — e.g. a single-shot reply that finished before the
            # task did). Consume this session's task so none is orphaned.
            task = self._retrieval_tasks.pop(session_id, None)
            if task is not None and not task.done():
                task.cancel()
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            # This turn's new messages only: everything now present that
            # was not before, minus any memory hint we injected (matched by
            # its reserved name, since injection happens in ``on_reasoning``).
            increment = [
                m
                for m in agent.state.context
                if isinstance(m, Msg)
                and m.id not in pre_ids
                and getattr(m, "name", None) != _MEMORY_MSG_NAME
            ]
            # Only persist a real exchange: a genuine user turn plus at
            # least one non-empty assistant message produced this turn.
            if query_text and any(
                m.role == "assistant" and m.get_text_content()
                for m in increment
            ):
                await self._write_back(increment, session_id)

    # ------------------------------------------------------------------
    # Hook: on_reasoning (inject retrieved memories once ready)
    # ------------------------------------------------------------------
    async def on_reasoning(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Inject background-retrieved memories before a reasoning step.

        The search started in :meth:`on_reply` runs concurrently; this hook
        polls it before each reasoning step and, once it is finished, appends
        the retrieved memories to the agent's context so the *next* model
        call sees them. This is best-effort: a single-shot reply (one model
        call) may finish before retrieval does, in which case the hint is
        skipped for that turn — the same trade-off as
        :class:`AgenticMemoryMiddleware`.
        """
        session_id = self._session_id_of(agent)
        task = self._retrieval_tasks.get(session_id)
        if task is not None and task.done():
            self._retrieval_tasks.pop(session_id, None)
            try:
                memories = task.result()
            except (asyncio.CancelledError, Exception) as e:  # noqa: BLE001
                memories = []
                logger.warning("ReMe search failed: %s", e)
            if memories:
                agent.state.context.append(
                    self._build_memory_message(memories),
                )

        async for event in next_handler(**input_kwargs):
            yield event

    # ------------------------------------------------------------------
    # Hook: on_system_prompt (advertise the search tool to the LLM)
    # ------------------------------------------------------------------
    async def on_system_prompt(
        self,
        agent: "Agent",
        current_prompt: str,
    ) -> str:
        """Append search-tool instructions to the system prompt.

        Args:
            agent (`Agent`):
                The agent whose system prompt is being transformed.
            current_prompt (`str`):
                The system prompt produced by previous middleware.

        Returns:
            `str`:
                The unchanged prompt in static-control mode, otherwise the
                prompt with the ``memory_search`` nudge appended.
        """
        if self._parameters.mode == "static_control":
            return current_prompt
        return f"{current_prompt}\n\n{_TOOL_INSTRUCTIONS}"

    async def list_tools(self) -> list["ToolBase"]:
        """List memory tools provided by this middleware.

        Returns:
            `list[ToolBase]`:
                The ``memory_search`` tool in agent-control modes
                (``"agent_control"`` / ``"both"``), otherwise an empty
                list. There is no add tool — writing is automatic.
        """
        if self._parameters.mode == "static_control":
            return []
        return _build_memory_tools(self)

    # ==================================================================
    # ReMe job helpers (shared by hooks and tools)
    # ==================================================================
    async def _run_job(self, name: str, **kwargs: Any) -> Any:
        """Start the embedded app (if needed) and run a ReMe job.

        Raises:
            RuntimeError:
                If ReMe reports ``success=False``.
        """
        await self._ensure_started()
        assert self._app is not None
        response = await self._app.run_job(name, **kwargs)
        if getattr(response, "success", True) is False:
            raise RuntimeError(
                f"ReMe {name!r} failed: {getattr(response, 'answer', '')}",
            )
        return response

    async def _search(
        self,
        query: str,
        *,
        limit: int | None = None,
    ) -> list[str]:
        """Search ReMe and return the retrieved memory texts."""
        response = await self._run_job(
            _SEARCH_JOB,
            query=query,
            limit=self._parameters.top_k if limit is None else limit,
        )
        return _extract_memory_texts(getattr(response, "metadata", {}))

    async def _write_back(
        self,
        messages: list[Msg],
        session_id: str | None,
    ) -> None:
        """Persist a completed conversation increment to ReMe.

        ``messages`` is the full slice this turn appended to the agent's
        context — the user input, every assistant step, and every tool
        call / tool result — so ReMe's ``auto_memory`` extraction sees the
        whole exchange, not just the final answer.

        ``session_id`` is passed in per call (read live from the agent),
        never stored, so a shared middleware keeps conversations isolated.
        Skipped (with a warning) when no ``session_id`` is available;
        failures are logged rather than propagated so a write never blocks
        the reply.
        """
        if not session_id:
            logger.warning(
                "ReMe write skipped: no session_id captured from the agent.",
            )
            return
        try:
            await self._run_job(
                _AUTO_MEMORY_JOB,
                messages=[m.model_dump(mode="json") for m in messages],
                session_id=session_id,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "ReMe auto_memory failed for session_id=%s: %s",
                session_id,
                e,
            )

    # ==================================================================
    # Helpers
    # ==================================================================
    @staticmethod
    def _build_memory_message(memories: list[str]) -> Msg:
        """Format retrieved ``memories`` as a synthetic hint message.

        The context entry uses an assistant-role ``Msg`` container because
        user messages cannot carry ``HintBlock`` content. Formatters convert
        the ``HintBlock`` itself into a user message before the model call.

        Args:
            memories (`list[str]`):
                Retrieved memory texts to expose to the model.
        Returns:
            `Msg`:
                An assistant-role message containing one ``HintBlock``.
        """
        bullets = "\n".join(f"- {m}" for m in memories)
        content = (
            f"{_MEMORY_SECTION_HEADER}\n"
            f"{_MEMORY_SECTION_INTRO}\n"
            f"{bullets}"
        )
        return AssistantMsg(
            name=_MEMORY_MSG_NAME,
            content=[HintBlock(hint=content)],
        )
