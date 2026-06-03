# -*- coding: utf-8 -*-
"""Adapters that let mem0 drive its memory extraction with the user's
existing AgentScope chat / embedding model — instead of building yet
another OpenAI / Anthropic / Ollama client just for mem0.

Two pieces:

- :class:`AgentScopeLLM` — implements ``mem0.llms.base.LLMBase`` by
  delegating to an ``agentscope.model.ChatModelBase`` instance.
- :class:`AgentScopeEmbedding` — implements
  ``mem0.embeddings.base.EmbeddingBase`` by delegating to an
  ``agentscope.embedding.EmbeddingModelBase`` instance.

mem0 calls these synchronously; AgentScope models are async. We bridge
the two by running the coroutines on a persistent background event
loop — necessary because async clients used inside AgentScope models
(e.g. Ollama's ``AsyncClient``) bind their connection pool / SSL
context to a specific loop, so spinning up a fresh ``asyncio.run`` per
call breaks reuse.

Use :func:`build_mem0_config` to produce a ``MemoryConfig`` wired to
AgentScope models:

    from mem0 import AsyncMemory
    from agentscope.middleware._longterm_memory._mem0._agentscope_adapter \\
        import build_mem0_config

    mem0_client = AsyncMemory(
        config=build_mem0_config(
            chat_model=my_chat_model,
            embedding_model=my_embedding_model,
        ),
    )
"""
from __future__ import annotations

import asyncio
import atexit
import json
import threading
from collections.abc import AsyncGenerator
from typing import Any, Coroutine, TYPE_CHECKING

from mem0.configs.embeddings.base import BaseEmbedderConfig
from mem0.configs.llms.base import BaseLlmConfig
from mem0.embeddings.base import EmbeddingBase
from mem0.llms.base import LLMBase

from ....embedding import EmbeddingModelBase
from ....message import (
    AssistantMsg,
    Msg,
    SystemMsg,
    UserMsg,
)
from ....model import ChatModelBase

if TYPE_CHECKING:
    from ....model import ChatResponse


# ----------------------------------------------------------------------
# Persistent background event loop — see module docstring for rationale.
# ----------------------------------------------------------------------


class _PersistentLoop:
    """Owns a long-lived event loop running in a daemon thread, so any
    async client bound to it stays valid across many sync calls.

    A single instance suffices for the whole process; ``atexit`` stops
    the loop and joins the thread on interpreter shutdown.
    """

    _START_TIMEOUT_S = 5.0

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._started = threading.Event()
        atexit.register(self._shutdown)

    def get(self) -> asyncio.AbstractEventLoop:
        """Return the persistent background loop, starting it on the
        first call."""
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                self._started.clear()

                def _run() -> None:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    self._loop = loop
                    self._started.set()
                    loop.run_forever()

                self._thread = threading.Thread(
                    target=_run,
                    daemon=True,
                    name="agentscope-mem0-adapter-loop",
                )
                self._thread.start()
                if not self._started.wait(self._START_TIMEOUT_S):
                    raise RuntimeError(
                        "Background event loop failed to start within "
                        f"{self._START_TIMEOUT_S}s.",
                    )
            assert self._loop is not None
            return self._loop

    def _shutdown(self) -> None:
        with self._lock:
            if self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._loop.stop)
                if self._thread is not None and self._thread.is_alive():
                    self._thread.join(timeout=self._START_TIMEOUT_S)
                self._loop.close()
                self._loop = None
                self._thread = None


_persistent_loop = _PersistentLoop()


def _run_sync(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run ``coro`` on the shared persistent loop and block for the
    result."""
    loop = _persistent_loop.get()
    return asyncio.run_coroutine_threadsafe(coro, loop).result()


# ----------------------------------------------------------------------
# LLM adapter
# ----------------------------------------------------------------------


class AgentScopeLLM(LLMBase):
    """mem0 ``LLMBase`` backed by an AgentScope ``ChatModelBase``.

    Pass your AgentScope model into ``config["model"]``; mem0's memory
    extraction calls then route through it. Both streaming and non-
    streaming AgentScope models are accepted (streaming responses are
    drained and the final chunk is used).
    """

    def __init__(
        self,
        config: BaseLlmConfig | dict | None = None,
    ) -> None:
        super().__init__(config)
        if self.config.model is None:
            raise ValueError(
                "AgentScopeLLM requires `model` in the config to be an "
                "AgentScope ChatModelBase instance.",
            )
        if not isinstance(self.config.model, ChatModelBase):
            raise TypeError(
                f"AgentScopeLLM `model` must be a ChatModelBase, got "
                f"{type(self.config.model).__name__}.",
            )
        self._agentscope_model: ChatModelBase = self.config.model

    # ----- LLMBase interface -----
    # pylint: disable=unused-argument
    def generate_response(
        self,
        messages: list[dict[str, str]],
        response_format: Any | None = None,  # mem0 contract — unused
        tools: list[dict] | None = None,
        tool_choice: str = "auto",  # mem0 contract — unused
    ) -> str | dict:
        """mem0 ``LLMBase`` entry — runs the AgentScope chat model on
        the persistent loop and returns str (or dict with tool_calls
        when ``tools`` is given)."""
        as_messages = _convert_messages_to_agentscope(messages)
        if not as_messages:
            raise ValueError(
                "AgentScopeLLM received no usable messages "
                "(empty list or all roles unrecognized).",
            )

        response = _run_sync(
            _await_chat(self._agentscope_model, as_messages, tools),
        )
        return _parse_chat_response(response, has_tool=bool(tools))


async def _await_chat(
    model: ChatModelBase,
    messages: list[Msg],
    tools: list[dict] | None,
) -> "ChatResponse":
    result = await model(messages, tools=tools)
    # Streaming model — drain the generator and keep the final chunk,
    # which carries the complete content per AgentScope's streaming
    # contract. ``isinstance`` (not ``hasattr``) — Pydantic BaseModel
    # raises KeyError instead of AttributeError on missing dunder
    # attrs, which ``hasattr`` does not catch.
    if isinstance(result, AsyncGenerator):
        last = None
        async for chunk in result:
            last = chunk
        if last is None:
            raise RuntimeError(
                "AgentScope streaming model yielded no chunks.",
            )
        return last
    return result


def _convert_messages_to_agentscope(
    messages: list[dict[str, str]],
) -> list[Msg]:
    """mem0 hands us OpenAI-style ``[{"role", "content"}, ...]`` dicts;
    AgentScope wants ``Msg`` objects."""
    out: list[Msg] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            out.append(SystemMsg(name="system", content=content))
        elif role == "user":
            out.append(UserMsg(name="user", content=content))
        elif role == "assistant":
            out.append(AssistantMsg(name="assistant", content=content))
        # unknown roles silently dropped — matches v1 behavior
    return out


def _parse_chat_response(
    response: "ChatResponse",
    has_tool: bool,
) -> str | dict:
    """Flatten an AgentScope ``ChatResponse`` into the str/dict shape
    mem0 expects from ``LLMBase.generate_response``."""
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_parts: list[dict] = []

    for block in response.content or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_parts.append(block.text or "")
        elif block_type == "thinking":
            thinking_parts.append(f"[Thinking: {block.thinking or ''}]")
        elif block_type == "tool_call":
            # AgentScope 2.0 stores tool args as a JSON string; mem0
            # expects an arguments dict.
            raw_input = block.input or "{}"
            try:
                arguments = json.loads(raw_input)
            except json.JSONDecodeError:
                arguments = raw_input
            tool_parts.append(
                {"name": block.name, "arguments": arguments},
            )
        # DataBlock and other types are not part of mem0's contract.

    text_out = "\n".join(thinking_parts + text_parts)
    if has_tool:
        return {"content": text_out, "tool_calls": tool_parts}
    return text_out


# ----------------------------------------------------------------------
# Embedding adapter
# ----------------------------------------------------------------------


class AgentScopeEmbedding(EmbeddingBase):
    """mem0 ``EmbeddingBase`` backed by an AgentScope
    ``EmbeddingModelBase``."""

    def __init__(
        self,
        config: BaseEmbedderConfig | dict | None = None,
    ) -> None:
        # mem0's EmbeddingBase (unlike LLMBase) does NOT auto-convert
        # dict configs — it stores whatever is passed. Normalize here
        # so callers can use the same dict-config style as the LLM.
        if isinstance(config, dict):
            config = BaseEmbedderConfig(**config)
        super().__init__(config)
        if self.config.model is None:
            raise ValueError(
                "AgentScopeEmbedding requires `model` in the config "
                "to be an AgentScope EmbeddingModelBase instance.",
            )
        if not isinstance(self.config.model, EmbeddingModelBase):
            raise TypeError(
                f"AgentScopeEmbedding `model` must be an "
                f"EmbeddingModelBase, got "
                f"{type(self.config.model).__name__}.",
            )
        self._agentscope_model: EmbeddingModelBase = self.config.model

    # ----- EmbeddingBase interface -----
    # pylint: disable=unused-argument
    def embed(
        self,
        text: str | list[str],
        memory_action: str | None = None,  # mem0 contract — unused
    ) -> list[float]:
        """mem0 ``EmbeddingBase`` entry — runs the AgentScope embedding
        model on the persistent loop and returns the first vector."""
        text_list = [text] if isinstance(text, str) else list(text)
        response = _run_sync(self._agentscope_model(text_list))
        if not response.embeddings:
            raise RuntimeError(
                "AgentScope embedding model returned no embeddings.",
            )
        # AgentScope EmbeddingResponse.embeddings is List[List[float]];
        # mem0 expects a single vector for a single-text call.
        return response.embeddings[0]


# ----------------------------------------------------------------------
# Build a mem0 MemoryConfig wired to AgentScope models
# ----------------------------------------------------------------------

# The provider name we register under in mem0's factory + config layer.
_AGENTSCOPE_PROVIDER = "agentscope"


def build_mem0_config(
    *,
    chat_model: ChatModelBase | None = None,
    embedding_model: EmbeddingModelBase | None = None,
    mem0_config: Any | None = None,
) -> Any:
    """Build a ``mem0.configs.base.MemoryConfig`` wired to AgentScope
    chat / embedding models.

    Three calling shapes:

    1. ``build_mem0_config(chat_model=..., embedding_model=...)``
       — build a fresh config (mem0 defaults for vector_store /
       history_db / reranker etc.) with both LLM and embedder routed
       through AgentScope.
    2. ``build_mem0_config(mem0_config=cfg, chat_model=...,
       embedding_model=...)`` — start from your customized
       ``MemoryConfig`` and override only ``.llm`` / ``.embedder`` with
       the AgentScope adapters. Use this when you want a non-default
       vector store / history DB / reranker but still want AgentScope
       to drive memory extraction. Either or both of ``chat_model``
       and ``embedding_model`` may be passed — fields you omit keep
       whatever the input config had.
    3. ``build_mem0_config(mem0_config=cfg)`` — pass-through; the
       AgentScope adapters are registered (cheap) but no fields are
       overridden.

    Why a helper? Two private layers inside mem0 reject anything
    outside its built-in provider list, so plugging in AgentScope
    requires both:

    1. Adding ``"agentscope"`` to ``LlmFactory.provider_to_class`` /
       ``EmbedderFactory.provider_to_class`` so the factory can
       construct our adapter classes.
    2. Bypassing the hardcoded provider whitelist in
       ``LlmConfig.validate_config`` / ``EmbedderConfig.validate_config``.
       Done by substituting subclasses whose validator only allows
       ``"agentscope"`` and delegates everything else to the base
       class.

    Args:
        chat_model:
            The AgentScope ``ChatModelBase`` mem0 should use for memory
            extraction. Required when ``mem0_config`` is not given.
        embedding_model:
            The AgentScope ``EmbeddingModelBase`` mem0 should use to
            embed memories. Its ``dimensions`` must match the vector
            store's expected dim (mem0's default Qdrant expects 1536).
            Required when ``mem0_config`` is not given.
        mem0_config:
            Optional pre-built ``MemoryConfig`` to use as the base.
            When given, only the LLM / embedder slots are overridden
            from ``chat_model`` / ``embedding_model`` — every other
            field (``vector_store``, ``history_db_path``, ``reranker``,
            ``custom_instructions``, ``version``) is preserved.

    Returns:
        A ``MemoryConfig`` ready to pass to ``AsyncMemory(config=...)``
        or ``Memory(config=...)``.
    """
    from mem0.configs.base import MemoryConfig

    _register_agentscope_provider()
    llm_cfg_cls, emb_cfg_cls = _agentscope_config_classes()

    if mem0_config is None:
        if chat_model is None or embedding_model is None:
            raise ValueError(
                "build_mem0_config requires `chat_model` and "
                "`embedding_model` when `mem0_config` is not given.",
            )
        return MemoryConfig(
            llm=llm_cfg_cls(
                provider=_AGENTSCOPE_PROVIDER,
                config={"model": chat_model},
            ),
            embedder=emb_cfg_cls(
                provider=_AGENTSCOPE_PROVIDER,
                config={"model": embedding_model},
            ),
        )

    # Use the user's config as base; partial-override .llm / .embedder
    # only for fields they actually passed. Pydantic v2 doesn't
    # re-validate on attribute assignment, so this sticks.
    if chat_model is not None:
        mem0_config.llm = llm_cfg_cls(
            provider=_AGENTSCOPE_PROVIDER,
            config={"model": chat_model},
        )
    if embedding_model is not None:
        mem0_config.embedder = emb_cfg_cls(
            provider=_AGENTSCOPE_PROVIDER,
            config={"model": embedding_model},
        )
    return mem0_config


def _register_agentscope_provider() -> None:
    """Plug the AgentScope adapter classes into mem0's factory dicts
    under provider name ``"agentscope"``. Idempotent."""
    from mem0.utils.factory import EmbedderFactory, LlmFactory

    LlmFactory.provider_to_class[_AGENTSCOPE_PROVIDER] = (
        f"{__name__}.AgentScopeLLM",
        BaseLlmConfig,
    )
    EmbedderFactory.provider_to_class[
        _AGENTSCOPE_PROVIDER
    ] = f"{__name__}.AgentScopeEmbedding"


def _agentscope_config_classes() -> tuple[type, type]:
    """Return ``LlmConfig`` / ``EmbedderConfig`` subclasses whose
    validator allows ``provider="agentscope"`` (and only that — every
    other provider continues to be rejected with the same error mem0
    would have raised)."""
    from pydantic import field_validator

    from mem0.embeddings.configs import EmbedderConfig
    from mem0.llms.configs import LlmConfig

    class _AgentScopeLlmConfig(LlmConfig):
        """``LlmConfig`` subclass that accepts the AgentScope provider."""

        @field_validator("config")
        @classmethod
        def validate_config(cls, v: Any, values: Any) -> Any:
            """Allow ``provider == "agentscope"``; reject everything
            else with mem0's original error."""
            provider = values.data.get("provider")
            if provider == _AGENTSCOPE_PROVIDER:
                return v
            raise ValueError(f"Unsupported LLM provider: {provider}")

    class _AgentScopeEmbedderConfig(EmbedderConfig):
        """``EmbedderConfig`` subclass that accepts the AgentScope provider."""

        @field_validator("config")
        @classmethod
        def validate_config(cls, v: Any, values: Any) -> Any:
            """Allow ``provider == "agentscope"``; reject everything
            else with mem0's original error."""
            provider = values.data.get("provider")
            if provider == _AGENTSCOPE_PROVIDER:
                return v
            raise ValueError(f"Unsupported embedding provider: {provider}")

    return _AgentScopeLlmConfig, _AgentScopeEmbedderConfig
