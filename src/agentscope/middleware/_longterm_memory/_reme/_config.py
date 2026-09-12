# -*- coding: utf-8 -*-
"""Minimal embedded ReMe configuration for AgentScope memory.

ReMe's bundled ``default`` configuration describes a standalone memory
application. It includes resource ingestion, chat, operational endpoints and
other jobs that are unrelated to :class:`ReMeMiddleware`. The middleware needs
the complete conversation-memory lifecycle: write-back, nightly dream
consolidation, search, and the small set of jobs/components those paths depend
on.

Keep this configuration as a Python dictionary rather than resolving ReMe's
``default.yaml`` so adding a new standalone ReMe feature cannot silently add
background work to an AgentScope process.
"""
from __future__ import annotations

import os
from typing import Any


_MAX_FILE_BYTES = 10 * 1024 * 1024


def _object_schema(
    properties: dict[str, Any],
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build the JSON schema used by a ReMe job."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = list(required)
    return schema


def _job(
    step: str,
    description: str,
    properties: dict[str, Any],
    required: tuple[str, ...] = (),
    **step_options: Any,
) -> dict[str, Any]:
    """Build a single-step ReMe request job."""
    return {
        "backend": "base",
        "description": description,
        "parameters": _object_schema(properties, required),
        "steps": [{"backend": step, **step_options}],
    }


def _dream_steps() -> list[dict[str, Any]]:
    """Build ReMe's daily-to-digest memory consolidation pipeline."""
    return [
        {
            "backend": "dream_extract_step",
            "file_catalog": "dream",
            "topic_session_id": "interests",
            "scan_days": 2,
            "max_units": 5,
        },
        {"backend": "dream_integrate_step"},
        {
            "backend": "dream_topics_step",
            "topic_count": 3,
            "topic_diversity_days": 7,
        },
        {
            "backend": "dream_finish_step",
            "file_catalog": "dream",
        },
    ]


def _memory_jobs() -> dict[str, Any]:
    """Return only the jobs required by AgentScope memory flows."""
    string = {"type": "string"}
    return {
        # This is the only continuous filesystem watcher in the embedded app.
        # It keeps both the daily cards from ``auto_memory`` and the digest
        # nodes from dream searchable.
        "index_update_loop": {
            "backend": "background",
            "max_file_bytes": _MAX_FILE_BYTES,
            "watch_dirs": ["daily_dir", "digest_dir"],
            "watch_suffixes": ["md"],
            "steps": [
                {
                    "backend": "init_changes_step",
                    "monitor_type": "file_store",
                    "monitor_name": "default",
                    "dispatch_steps": ["update_index_step"],
                },
                {
                    "backend": "watch_changes_step",
                    "dispatch_steps": [
                        {
                            "backend": "update_index_step",
                            "persist": False,
                        },
                    ],
                },
            ],
        },
        "search": _job(
            "search_step",
            "Search conversation memory cards.",
            {
                "query": string,
                "limit": {"type": "integer", "default": 5},
                "min_score": {"type": "number", "default": 0.0},
            },
            ("query",),
            vector_weight=0.7,
            candidate_multiplier=3.0,
            expand_links=False,
        ),
        "reindex": {
            "backend": "base",
            "description": "Rebuild the conversation-memory search index.",
            "max_file_bytes": _MAX_FILE_BYTES,
            "watch_dirs": ["daily_dir", "digest_dir"],
            "watch_suffixes": ["md"],
            "parameters": _object_schema({}),
            "steps": [
                {"backend": "clear_store_step"},
                {
                    "backend": "init_changes_step",
                    "monitor_type": "file_store",
                    "monitor_name": "default",
                    "dispatch_steps": ["update_index_step"],
                },
            ],
        },
        "auto_memory": _job(
            "auto_memory_step",
            "Record conversation facts into a daily memory card.",
            {
                "messages": {
                    "type": "array",
                    "items": {"type": "object"},
                },
                "session_id": {"type": "string", "default": ""},
                "memory_hint": string,
            },
            ("messages",),
        ),
        # ``auto_memory`` writes daily cards; dream is the downstream memory
        # consolidation phase that turns those cards into durable digest
        # nodes. Keep both the nightly schedule and an explicit job for
        # deterministic/manual execution.
        "dream_cron": {
            "backend": "cron",
            "cron": "0 23 * * *",
            "steps": _dream_steps(),
        },
        "auto_dream": {
            "backend": "base",
            "description": (
                "Consolidate daily conversation memory into digest nodes "
                "and interest topics."
            ),
            "parameters": _object_schema(
                {
                    "date": {"type": "string", "default": ""},
                    "hint": {"type": "string", "default": ""},
                    "scan_days": {"type": "integer", "default": 2},
                    "max_units": {"type": "integer", "default": 5},
                    "topic_count": {"type": "integer", "default": 3},
                    "topic_diversity_days": {
                        "type": "integer",
                        "default": 7,
                    },
                },
            ),
            "steps": _dream_steps(),
        },
        "node_search": _job(
            "node_search_step",
            "Find related digest nodes during dream consolidation.",
            {
                "query": string,
                "limit": {"type": "integer", "default": 20},
            },
            ("query",),
            vector_weight=0.7,
            candidate_multiplier=5.0,
        ),
        # ``auto_memory_step`` calls these jobs directly and exposes a subset
        # of them as tools to its internal AgentScope extraction agent. Dream
        # also uses read/write/edit/frontmatter jobs as restricted tools.
        "daily_list": _job(
            "daily_list_step",
            "List memory cards under one day.",
            {"date": {"type": "string", "default": ""}},
        ),
        "frontmatter_update": _job(
            "frontmatter_update_step",
            "Merge fields into a memory card's frontmatter.",
            {"path": string, "metadata": {"type": "object"}},
            ("path", "metadata"),
        ),
        "frontmatter_read": _job(
            "frontmatter_read_step",
            "Read a memory card's frontmatter.",
            {"path": string},
            ("path",),
        ),
        "move": _job(
            "move_step",
            "Rename a memory card.",
            {
                "src_path": string,
                "dst_path": string,
                "overwrite": {"type": "boolean", "default": False},
                "retarget": {"type": "boolean", "default": True},
            },
            ("src_path", "dst_path"),
        ),
        "read": _job(
            "read_step",
            "Read a memory card.",
            {
                "path": string,
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            },
            ("path",),
            with_neighbors=False,
        ),
        "write": _job(
            "write_step",
            "Write a memory card with frontmatter.",
            {
                "path": string,
                "name": string,
                "description": string,
                "content": string,
                "metadata": {"type": "object"},
            },
            ("path", "name", "description", "content"),
        ),
        "daily_write": _job(
            "daily_write_step",
            "Create a daily conversation memory card.",
            {
                "name": string,
                "description": string,
                "session_id": string,
                "content": string,
                "date": {"type": "string", "default": ""},
                "metadata": {"type": "object"},
            },
            ("name", "description", "session_id", "content"),
        ),
        "edit": _job(
            "edit_step",
            "Replace text in a memory card.",
            {
                "path": string,
                "old": string,
                "new": {"type": "string", "default": ""},
            },
            ("path", "old", "new"),
        ),
    }


def _memory_components(
    embedding_dimensions: int | None,
) -> dict[str, Any]:
    """Return components required by the minimal memory jobs."""
    components: dict[str, Any] = {
        "tokenizer": {"default": {"backend": "regex"}},
        # The AgentScope model is injected before ReMe starts in the usual
        # middleware path.  Environment-backed values preserve the existing
        # no-injection escape hatch without loading ReMe's default config.
        "as_llm": {
            "default": {
                "backend": os.getenv("LLM_BACKEND", "openai"),
                "model": os.getenv("LLM_MODEL_NAME", "qwen3.7-plus"),
                "stream": True,
                "context_size": 200_000,
                "max_retries": 3,
                "credential": {
                    "api_key": os.getenv("LLM_API_KEY", ""),
                    "base_url": os.getenv("LLM_BASE_URL", ""),
                },
                "parameters": {
                    "max_tokens": 65_536,
                    "thinking_enable": False,
                },
            },
        },
        "agent_wrapper": {
            "default": {
                "backend": "agentscope",
                "as_llm": "default",
                "builtin_tools": False,
                "permission_mode": "bypass",
                "react_config": {"max_iters": 30},
                "context_config": {
                    "trigger_ratio": 0.8,
                    "reserve_ratio": 0.1,
                    "tool_result_limit": 50_000,
                },
                "model_config": {"max_retries": 1},
            },
        },
        "file_graph": {"default": {"backend": "local"}},
        "file_catalog": {"dream": {"backend": "local"}},
        "file_chunker": {
            "markdown": {
                "backend": "markdown",
                "supported_extensions": ["md"],
            },
        },
        "keyword_index": {
            "default": {"backend": "bm25", "tokenizer": "default"},
        },
        "file_store": {
            "default": {
                "backend": "local",
                "store_name": "local",
                "embedding_store": "",
                "keyword_index": "default",
                "file_graph": "default",
            },
        },
    }

    if embedding_dimensions is not None:
        components.update(
            {
                "as_embedding": {
                    "default": {
                        "backend": "openai",
                        "model": "agentscope-injected",
                        "dimensions": embedding_dimensions,
                        "credential": {"api_key": "", "base_url": ""},
                        "parameters": {},
                    },
                },
                "embedding_store": {
                    "default": {
                        "backend": "local",
                        "as_embedding": "default",
                        "enable_cache": True,
                        "max_cache_size": 3_000,
                        "max_input_length": 8_192,
                        "max_batch_size": 10,
                    },
                },
            },
        )
        components["file_store"]["default"]["embedding_store"] = "default"

    return components


def _build_reme_app_config(
    *,
    workspace_dir: str,
    embedding_dimensions: int | None = None,
) -> dict[str, Any]:
    """Return a fresh, self-contained ReMe config for AgentScope memory."""
    return {
        "workspace_dir": workspace_dir,
        "enable_logo": False,
        "log_to_console": False,
        "service": {"backend": "http"},
        "jobs": _memory_jobs(),
        "components": _memory_components(embedding_dimensions),
    }
