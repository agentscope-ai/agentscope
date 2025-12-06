# -*- coding: utf-8 -*-
"""MemTool-style components implemented with AgentScope.

Implements a simple MemTool Workflow mode:
- prune → search → execute with a curated toolset for a Worker agent.

The retrieval logic is lexical to keep the demo offline-friendly. Swap in
an embedding retriever for better quality.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple
import os
import re

from agentscope.agent import ReActAgent
from agentscope.formatter import (
    OpenAIChatFormatter,
    DashScopeChatFormatter,
    GeminiChatFormatter,
)
from agentscope.message import Msg
from agentscope.model import (
    OpenAIChatModel,
    DashScopeChatModel,
    GeminiChatModel,
)
from agentscope.tool import (
    Toolkit,
    execute_python_code,
    execute_shell_command,
    write_text_file,
    view_text_file,
)

try:
    # When imported as a package: examples.memtool_demo.memtool_components
    from .tools import calculate_expression, search_in_file  # type: ignore
except Exception:  # pragma: no cover - fallback for script mode
    # When run as a script from the folder
    from tools import calculate_expression, search_in_file  # type: ignore


# ----------------------------
# Tool KB and metadata
# ----------------------------


ToolFn = Callable[..., object]


@dataclass
class ToolMeta:
    name: str
    description: str
    tags: List[str]
    fn: ToolFn


class ToolKB:
    """A simple in-memory KB to store tool metadata and functions.

    The `search` method returns tool names ranked by lexical overlap with the
    query. This can be replaced by an embedding-based search for stronger recall.
    """

    def __init__(self, tools: List[ToolMeta]) -> None:
        self._tools: Dict[str, ToolMeta] = {t.name: t for t in tools}

    def get(self, name: str) -> ToolMeta | None:
        return self._tools.get(name)

    def all_names(self) -> List[str]:
        return list(self._tools.keys())

    def search(self, query: str, top_k: int = 5) -> List[str]:
        tokens = _tokenize(query)
        scored: List[Tuple[str, int]] = []
        for name, meta in self._tools.items():
            score = _lexical_score(tokens, meta)
            if score > 0:
                scored.append((name, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [n for n, _ in scored[:top_k]]

    # Keep explicit lexical search for clarity
    def search_lexical(self, query: str, top_k: int = 5) -> List[str]:
        return self.search(query, top_k)

    async def search_rrf(self, query: str, top_k: int = 5) -> List[str]:
        """Reciprocal Rank Fusion (RRF) over lexical and optional embedding ranks.

        If an embedding model is available via env (OpenAI or DashScope), we
        compute an embedding ranking and fuse it with the lexical ranking using
        RRF. Otherwise, returns the lexical ranking.
        """
        lex = self.search_lexical(query, top_k=max(top_k * 2, 5))

        emb_model = _pick_embedding_model_if_available()
        if not emb_model:
            return lex[:top_k]

        # Build texts to embed: one query + all tool docs
        tool_names = list(self._tools.keys())
        tool_docs = [
            _compose_tool_doc(self._tools[name]) for name in tool_names
        ]

        try:
            emb_res = await emb_model([query, *tool_docs])
        except Exception:
            # If embedding fails, fall back to lexical only
            return lex[:top_k]

        q_vec = emb_res.embeddings[0]
        doc_vecs = emb_res.embeddings[1:]
        emb_scores = {
            name: _cosine_similarity(q_vec, vec)
            for name, vec in zip(tool_names, doc_vecs)
        }
        emb_rank = [
            name
            for name, _ in sorted(
                emb_scores.items(), key=lambda x: x[1], reverse=True
            )
        ][: max(top_k * 2, 5)]

        fused = _reciprocal_rank_fusion([lex, emb_rank])
        return fused[:top_k]


def _tokenize(text: str) -> List[str]:
    # Basic tokenizer splitting on non-word chars; keeps CJK as separate chars
    # and lowercases ASCII words
    ws = re.split(r"[^\w\u4e00-\u9fff]+", text.lower())
    return [w for w in ws if w]


def _lexical_score(query_tokens: List[str], meta: ToolMeta) -> int:
    hay = " ".join([meta.description] + meta.tags).lower()
    score = 0
    for t in query_tokens:
        if t in hay:
            score += 2
    # Light boost if tool name appears
    if any(t in meta.name.lower() for t in query_tokens):
        score += 1
    return score


def _compose_tool_doc(meta: ToolMeta) -> str:
    return f"{meta.name}\n{meta.description}\nTags: {' '.join(meta.tags)}"


def _cosine_similarity(a: List[float] | tuple, b: List[float] | tuple) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _reciprocal_rank_fusion(ranked_lists: List[List[str]], k: int = 60) -> List[str]:
    """Standard Reciprocal Rank Fusion (RRF).

    score(d) = sum_i 1 / (k + rank_i(d)), where rank_i starts from 1.
    """
    scores: Dict[str, float] = {}
    for rl in ranked_lists:
        for r, name in enumerate(rl, start=1):
            scores[name] = scores.get(name, 0.0) + 1.0 / (k + r)
    return [
        name for name, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)
    ]


def _pick_embedding_model_if_available():
    """Instantiate an embedding model if API keys exist; otherwise None.

    - OpenAI: OPENAI_API_KEY + OPENAI_EMBEDDING_MODEL (default text-embedding-3-small)
    - DashScope: DASHSCOPE_API_KEY + model text-embedding-v2
    """
    try:
        from agentscope.embedding import (
            OpenAITextEmbedding,
            DashScopeTextEmbedding,
            GeminiTextEmbedding,
            FileEmbeddingCache,
        )
    except Exception:
        return None

    openai_key = os.getenv("OPENAI_API_KEY")
    dash_key = os.getenv("DASHSCOPE_API_KEY")

    if openai_key:
        model_name = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        try:
            cache = _build_file_embedding_cache()
            return OpenAITextEmbedding(
                api_key=openai_key,
                model_name=model_name,
                embedding_cache=cache,
            )
        except Exception:
            return None

    if dash_key:
        try:
            cache = _build_file_embedding_cache()
            return DashScopeTextEmbedding(
                api_key=dash_key,
                model_name="text-embedding-v2",
                embedding_cache=cache,
            )
        except Exception:
            return None

    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if gemini_key:
        try:
            cache = _build_file_embedding_cache()
            return GeminiTextEmbedding(
                api_key=gemini_key,
                model_name=os.getenv("GEMINI_EMBEDDING_MODEL", "text-embedding-004"),
                embedding_cache=cache,
            )
        except Exception:
            return None

    return None


def _build_file_embedding_cache():
    """Construct a FileEmbeddingCache using env overrides.

    Env vars:
    - EMBEDDING_CACHE_DIR (default: examples/memtool_demo/.cache/embeddings)
    - EMBEDDING_CACHE_MAX_FILES (int, optional)
    - EMBEDDING_CACHE_MAX_MB (int, optional)
    """
    try:
        from agentscope.embedding import FileEmbeddingCache
    except Exception:
        return None

    default_dir = os.path.join(
        os.path.dirname(__file__),
        ".cache",
        "embeddings",
    )
    cache_dir = os.getenv("EMBEDDING_CACHE_DIR", default_dir)
    max_files = os.getenv("EMBEDDING_CACHE_MAX_FILES")
    max_mb = os.getenv("EMBEDDING_CACHE_MAX_MB")

    max_files_int = int(max_files) if max_files and max_files.isdigit() else None
    max_mb_int = int(max_mb) if max_mb and max_mb.isdigit() else None

    return FileEmbeddingCache(
        cache_dir=cache_dir,
        max_file_number=max_files_int,
        max_cache_size=max_mb_int,
    )


def build_default_tool_kb() -> ToolKB:
    """Construct a default KB with built-ins and custom demo tools."""
    tools = [
        ToolMeta(
            name="calculate_expression",
            description=(
                "Safely evaluate arithmetic expressions like '2+2' or '3*(7+1)'."
            ),
            tags=["math", "arithmetic", "calc", "计算", "数学"],
            fn=calculate_expression,
        ),
        ToolMeta(
            name="write_text_file",
            description=("Write plain text to a file at the given path."),
            tags=["file", "write", "save", "note", "文本", "保存"],
            fn=write_text_file,
        ),
        ToolMeta(
            name="view_text_file",
            description=("View or preview a text file's content."),
            tags=["file", "read", "open", "查看", "读取"],
            fn=view_text_file,
        ),
        ToolMeta(
            name="execute_python_code",
            description=("Execute short Python code for computation or scripting."),
            tags=["python", "code", "script", "compute"],
            fn=execute_python_code,
        ),
        ToolMeta(
            name="execute_shell_command",
            description=("Run shell commands for automation or environment ops."),
            tags=["shell", "command", "系统", "终端"],
            fn=execute_shell_command,
        ),
        ToolMeta(
            name="search_in_file",
            description=(
                "Search a text file for lines containing a query (case-insensitive)."
            ),
            tags=["search", "file", "grep", "查找"],
            fn=search_in_file,
        ),
    ]
    return ToolKB(tools)


# ----------------------------
# MemTool Manager and Orchestrator
# ----------------------------


class MemToolManager:
    """Maintain the active toolset and update it per-turn (Workflow mode)."""

    def __init__(self, kb: ToolKB, budget: int = 5) -> None:
        self.kb = kb
        self.budget = budget
        self.active_tools: list[str] = []

    def prune(self, user_query: str) -> None:
        """Remove tools that don't match the new query (basic lexical rule)."""
        tokens = _tokenize(user_query)
        kept: list[str] = []
        for name in self.active_tools:
            meta = self.kb.get(name)
            if not meta:
                continue
            if _lexical_score(tokens, meta) > 0:
                kept.append(name)
        self.active_tools = kept[: self.budget]

    def search(self, user_query: str) -> None:
        """Find new relevant tools to fill remaining budget."""
        remain = self.budget - len(self.active_tools)
        if remain <= 0:
            return
        candidates = self.kb.search(user_query, top_k=self.budget * 2)
        for name in candidates:
            if name not in self.active_tools:
                self.active_tools.append(name)
                if len(self.active_tools) >= self.budget:
                    break

    def get_active(self) -> List[ToolMeta]:
        return [self.kb.get(n) for n in self.active_tools if self.kb.get(n)]  # type: ignore[list-item]


def _pick_model_and_formatter():
    """Select a model + formatter based on available env vars."""
    if os.getenv("OPENAI_API_KEY"):
        model = OpenAIChatModel(
            model_name=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=os.getenv("OPENAI_API_KEY"),
            stream=True,
        )
        formatter = OpenAIChatFormatter()
        return model, formatter

    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if gemini_key:
        model = GeminiChatModel(
            model_name=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            api_key=gemini_key,
            stream=True,
        )
        formatter = GeminiChatFormatter()
        return model, formatter

    # Default to DashScope
    model = DashScopeChatModel(
        model_name=os.getenv("DASHSCOPE_MODEL", "qwen-max"),
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        stream=True,
    )
    formatter = DashScopeChatFormatter()
    return model, formatter


def build_worker_agent(active_tools: List[ToolMeta]) -> ReActAgent:
    """Create a ReActAgent equipped only with the curated tools."""
    toolkit = Toolkit()
    for meta in active_tools:
        toolkit.register_tool_function(meta.fn)

    model, formatter = _pick_model_and_formatter()

    sys_prompt = (
        "You are a Worker agent operating with a curated, limited toolset.\n"
        "- Use the registered tools to solve the task efficiently.\n"
        "- When done, call `generate_response` to return the final answer.\n"
        "- Do not assume tools that are not listed are available."
    )

    agent = ReActAgent(
        name="Worker",
        sys_prompt=sys_prompt,
        model=model,
        formatter=formatter,
        toolkit=toolkit,
        max_iters=8,
        enable_meta_tool=True,  # allow agent to suggest tool resets if needed
    )
    return agent


class Orchestrator:
    """MemTool Workflow Orchestrator: prune → search → execute."""

    def __init__(self, manager: MemToolManager) -> None:
        self.manager = manager

    async def run(self, user_query: str) -> Msg:
        # MemTool Workflow mode: deterministic control
        self.manager.prune(user_query)
        self.manager.search(user_query)

        worker = build_worker_agent(self.manager.get_active())
        msg = Msg("user", user_query, role="user")
        res = await worker(msg)
        return res
