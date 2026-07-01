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

- ``"static_control"`` — search ReMe before each reply and inject the
  retrieved memories into context (plus the automatic write-back).
- ``"agent_control"`` — expose a ``memory_search`` tool the agent calls
  on demand (plus the automatic write-back); no auto-retrieval.
- ``"both"`` — auto-retrieve/inject *and* expose ``memory_search``.

ReMe scopes writes by ``session_id``, which is read from
``agent.state.session_id`` at hook time (not configured on the
middleware), so it always matches the agent's own session; search runs
over the whole workspace.
"""
from __future__ import annotations

from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Literal,
    TYPE_CHECKING,
)

from ..._base import MiddlewareBase
from ...._logging import logger
from ....event import ReplyStartEvent
from ....message import AssistantMsg, HintBlock, Msg
from ._tools import _build_memory_tools
from ._utils import _extract_memory_texts, _extract_query_text

if TYPE_CHECKING:
    from ....agent import Agent
    from ....model import ChatModelBase
    from ....embedding import EmbeddingModelBase
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
    optionally ``chat_model`` / ``embedding_model``) and it is created
    lazily on first use. When ``chat_model`` is omitted, ReMe uses the LLM
    from its own config/credentials. For advanced settings the dedicated
    parameters do not expose (e.g. enabling the vector store), pass a
    deep-merged ``config_overrides`` mapping.

    The model is fixed at construction (never taken from an agent), so the
    embedded app's single LLM is well-defined even when one middleware
    instance is shared across several agents. Per-conversation state — the
    ReMe ``session_id`` — is instead read live from each agent at hook
    time and never stored, keeping shared use isolated.

    AgentScope middleware has no framework-managed lifecycle, so the app
    is built once and started lazily on first use (idempotent). Call
    :meth:`close` for explicit teardown of the embedded app.

    Example::

        from agentscope.middleware import ReMeMiddleware
        from agentscope.tool import Toolkit

        mw = ReMeMiddleware(workspace_dir=".reme", mode="both")
        agent = Agent(
            ...,
            toolkit=Toolkit(tools=await mw.list_tools()),
            middlewares=[mw],
        )
    """

    def __init__(
        self,
        *,
        workspace_dir: str = ".reme",
        config: str = "default",
        config_overrides: dict[str, Any] | None = None,
        chat_model: "ChatModelBase | None" = None,
        embedding_model: "EmbeddingModelBase | None" = None,
        mode: Literal["static_control", "agent_control", "both"] = "both",
        top_k: int = 5,
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
            config_overrides (`dict[str, Any] | None`, optional):
                Extra keyword arguments deep-merged into
                ``reme.config.resolve_app_config`` when the app is built,
                for advanced settings the dedicated parameters do not
                expose. For example, enable the vector store (the bundled
                ``default`` config ships it off) with
                ``{"components": {"file_store": {"default": ``
                ``{"embedding_store": "default"}}}}``. The middleware's own
                ``workspace_dir`` / ``config`` (and internal ``enable_logo``
                / ``log_to_console``) always take precedence over keys of
                the same name here.
            chat_model (`ChatModelBase | None`, optional):
                AgentScope chat model injected into ReMe's default LLM
                component, fixed for the lifetime of the embedded app.
                When ``None``, ReMe uses the LLM from its own
                config/credentials. Needed for ``auto_memory`` write-back.
            embedding_model (`EmbeddingModelBase | None`, optional):
                AgentScope embedding model injected into ReMe's default
                embedding component, fixed for the lifetime of the
                embedded app. ReMe starts this component eagerly (it is
                wired into the file store even when search is
                keyword-only), so injecting a model bypasses ReMe
                building its own from credentials. When ``None``, ReMe
                uses the embedding backend from its own config/credentials.
            mode (`Literal["static_control", "agent_control", "both"]`, \
            optional):
                How the agent *retrieves* from ReMe. Write-back runs
                automatically in every mode (ReMe listens to the
                conversation via the reply hook), so ``mode`` only
                governs retrieval:

                - ``"static_control"``: search ReMe before each reply and
                  append the results to ``agent.state.context`` as an
                  ``AssistantMsg(name="memory")``; no tool is exposed.
                - ``"agent_control"``: expose a ``memory_search`` tool for
                  the agent to invoke on demand; no auto-retrieval.
                - ``"both"``: auto-retrieve/inject *and* expose the tool.

                Defaults to ``"both"``.
            top_k (`int`, optional):
                Max number of memories retrieved per search (and the
                default ``limit`` for the ``memory_search`` tool).
                Defaults to ``5``.
        """
        if mode not in ("static_control", "agent_control", "both"):
            raise ValueError(
                f"Unknown mode {mode!r}; expected one of "
                f"'static_control', 'agent_control', 'both'.",
            )

        # Embedded ReMe application state. The app is built lazily and
        # started once (idempotent guards), since middleware has no
        # framework-managed lifecycle. The middleware always owns the app
        # it builds, so :meth:`close` tears it down.
        self._app: Any | None = None
        self._started = False
        self._workspace_dir = workspace_dir
        self._config = config
        self._config_overrides = config_overrides

        self._chat_model = chat_model
        self._embedding_model = embedding_model
        self._mode = mode
        self._top_k = top_k

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

        # Reserved keys always win over config_overrides so a caller
        # cannot accidentally redirect the workspace or re-enable logging.
        app_kwargs: dict[str, Any] = dict(self._config_overrides or {})
        app_kwargs.update(
            config=self._config,
            workspace_dir=self._workspace_dir,
            enable_logo=False,
            log_to_console=False,
        )
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
            if self._chat_model is not None:
                await self._app.update_component(
                    _AS_LLM_COMPONENT,
                    _AS_DEFAULT,
                    model=self._chat_model,
                )
            if self._embedding_model is not None:
                await self._app.update_component(
                    _AS_EMBEDDING_COMPONENT,
                    _AS_DEFAULT,
                    model=self._embedding_model,
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

        # Retrieve (static_control / both only) and inject before the
        # reply; write the new exchange back afterwards in every mode.
        inputs = input_kwargs.get("inputs")
        query_text = _extract_query_text(inputs)

        memories: list[str] = []
        if self._mode != "agent_control" and query_text:
            try:
                memories = await self._search(query_text)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "ReMe search failed for session_id=%s: %s",
                    session_id,
                    e,
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

        memory_msg: Msg | None = None
        try:
            async for item in next_handler(**input_kwargs):
                if (
                    memory_msg is None
                    and memories
                    and isinstance(item, ReplyStartEvent)
                ):
                    memory_msg = self._build_memory_message(memories)
                    agent.state.context.append(memory_msg)
                yield item
        finally:
            # This turn's new messages only: everything now present that
            # was not before, minus our own injected hint note.
            increment = [
                m
                for m in agent.state.context
                if isinstance(m, Msg)
                and m.id not in pre_ids
                and m is not memory_msg
            ]
            # Only persist a real exchange: a genuine user turn plus at
            # least one non-empty assistant message produced this turn.
            if query_text and any(
                m.role == "assistant" and m.get_text_content()
                for m in increment
            ):
                await self._write_back(increment, session_id)

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
        if self._mode == "static_control":
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
        if self._mode == "static_control":
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
            limit=self._top_k if limit is None else limit,
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
        """
        bullets = "\n".join(f"- {m}" for m in memories)
        content = (
            f"{_MEMORY_SECTION_HEADER}\n"
            f"{_MEMORY_SECTION_INTRO}\n"
            f"{bullets}"
        )
        return AssistantMsg(
            name="memory",
            content=[HintBlock(hint=content)],
        )
