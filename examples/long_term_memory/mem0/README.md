# mem0 middleware example

One runnable demo (`oss_demo.py`) showing the
[mem0](https://github.com/mem0ai/mem0) middleware plugged into an
`agentscope.agent.Agent`. Drives two consecutive agent sessions for
the same `user_id` so mem0's cross-session memory effect is visible,
and prints each middleware contribution (retrieval / tool call /
write-back) inline so you can see when each path fires.

The demo defaults to the **OSS backend** (open-source mem0,
self-hosted via local Qdrant) with mem0 driven by AgentScope's own
DashScope chat + embedding model — no separate OpenAI key needed
by mem0. To run it against the hosted **mem0
Platform** instead, swap the `Mem0Middleware(...)` construction for
the alternative shown inline (look for the
``# For the hosted mem0 Platform, swap …`` comment in `oss_demo.py`)
— the rest of the demo is identical.

## Install

```bash
# mem0 is an optional AgentScope dependency — pull it via the extra:
pip install "agentscope[memory-mem0]"      # resolves to mem0ai>=2.0.0,<3.0.0
# (equivalent to `pip install agentscope mem0ai>=2.0.0,<3.0.0`)

export DASHSCOPE_API_KEY=sk-...     # OSS path
# Platform path (only if you switch):
# export MEM0_API_KEY=m0-...
# export OPENAI_API_KEY=sk-...      # only needed if your agent's chat model is OpenAI
```

## Import path

`Mem0Middleware` is exported from the middleware package:

```python
from agentscope.middleware import Mem0Middleware
from agentscope.tool import Toolkit
```

## Three construction paths

```python
# 1. Models — build a local OSS AsyncMemory wired to your AgentScope
#    chat + embedding model. mem0 defaults for everything else.
Mem0Middleware(
    user_id="alice",
    chat_model=my_chat_model,
    embedding_model=my_embedding_model,
    mode="both",
)

# 2. Models + custom mem0_config — same as (1), but start from your
#    customized MemoryConfig (custom vector store, history DB,
#    reranker, ...). `chat_model` / `embedding_model` always WIN:
#    if mem0_config already specifies an .llm or .embedder, it gets
#    OVERWRITTEN by the AgentScope adapter built from your model.
#    Every other field of mem0_config (vector_store, history_db_path,
#    reranker, etc.) is preserved as-is.
Mem0Middleware(
    user_id="alice",
    chat_model=my_chat_model,
    embedding_model=my_embedding_model,
    mem0_config=MemoryConfig(
        vector_store=VectorStoreConfig(
            provider="qdrant",
            config={"host": "my-qdrant", "port": 6333},
        ),
        history_db_path="/data/mem0_history.db",
    ),
    mode="both",
)

# 3. Client — bring your own pre-built mem0 client. Accepts EITHER
#    backend: `mem0.AsyncMemory` (open-source / self-hosted) or
#    `mem0.AsyncMemoryClient` (hosted Platform). Use this when you
#    want full control over the mem0 setup — custom subclass, a
#    pre-warmed client shared across many agents, exotic config
#    that doesn't fit the `build_mem0_config` helper, etc.
#
# OSS backend (you assemble the AsyncMemory yourself):
Mem0Middleware(
    user_id="alice",
    client=AsyncMemory(),  # or AsyncMemory.from_config({...})
    mode="both",
)

# Hosted Platform backend:
Mem0Middleware(
    user_id="alice",
    client=AsyncMemoryClient(api_key="m0-..."),
    mode="both",
)
```

Precedence and validation matrix:

| `client` | `mem0_config` | `chat_model` | `embedding_model` | Behavior |
|:-:|:-:|:-:|:-:|---|
| ✓ | — | — | — | Use `client` as-is. |
| ✓ | any | any | any | Use `client`; the other three are ignored, and a `WARNING` log lists which kwargs got dropped. |
| — | ✓ | — | — | Wrap `mem0_config` in an `AsyncMemory`, no overrides. |
| — | ✓ | ✓ | — | Wrap + override `.llm` with the AgentScope adapter; keep `.embedder` from `mem0_config`. |
| — | ✓ | — | ✓ | Wrap + override `.embedder` only; keep `.llm` from `mem0_config`. |
| — | ✓ | ✓ | ✓ | Wrap + override both `.llm` and `.embedder` (other fields of `mem0_config` preserved). |
| — | — | ✓ | ✓ | Build a fresh `MemoryConfig` (mem0 defaults for vector store / history DB) with the AgentScope adapters wired in. |
| — | — | ✓ | — | ❌ `ValueError` — `chat_model` and `embedding_model` must be passed together when `mem0_config` is omitted. |
| — | — | — | ✓ | ❌ Same. |
| — | — | — | — | ❌ `ValueError` — need one of: `client`, `mem0_config`, or both `chat_model` + `embedding_model`. |

Why the "client wins" and "config override" paths exist:

- **`client` wins** lets one `Mem0Middleware(...)` call
  shape work for both library callers (who pass AgentScope models)
  and production setups (who supply a pre-built `client`). The
  `WARNING` log makes any mismatch visible without crashing.
- **Config override of `mem0_config.llm` / `.embedder`** lets you
  keep one canonical `MemoryConfig` template (custom vector store,
  history DB, reranker, …) and swap just the LLM / embedder per
  call site by passing `chat_model` / `embedding_model`.

## How the middleware controls memory

The `mode` parameter selects one of three patterns. They differ by
**what the LLM sees** and **what fires automatically**:

### `static_control`
The middleware does the work, the agent is unaware. Mirroring
AgentScope 1.x's `ReActAgent._retrieve_from_long_term_memory`:

1. **`on_reply` (pre)** queries mem0 with the latest user message
   and pre-fetches the results.
2. **At `ReplyStartEvent`** — which fires right after the agent has
   ingested the new user input into `state.context` and before the
   reasoning loop starts — the middleware appends an
   `AssistantMsg(name="memory", ...)` to `state.context`. This puts
   the memory note IMMEDIATELY after the user's new message, matching
   v1's placement (it ran right after `self.memory.add(msg)`).
3. **`on_reply` (post)** writes the new `(user, assistant)` exchange
   back to mem0.

The injected memory message **persists** in the agent's context
across turns. Long sessions accumulate one per turn that retrieved
anything; if that becomes a token concern, post-process with
`compress_context` or write your own middleware to pop them.

### `agent_control`
The middleware lists two tools — `search_memory(keywords, limit)` and
`add_memory(thinking, content)` — and otherwise stays out of the way.
Pass them into the agent's toolkit explicitly when constructing the
agent:

```python
mw = Mem0Middleware(..., mode="agent_control")
agent = Agent(
    ...,
    toolkit=Toolkit(tools=await mw.list_tools()),
    middlewares=[mw],
)
```

The system prompt gets a short nudge telling the agent that memory
tools exist; the actual per-tool usage guidance comes through the
standard tool schema. No automatic retrieval or write-back.

### `both` (default)
Both patterns are active simultaneously: memories are auto-retrieved
and appended to the agent's context as an assistant note, AND the
tools (with their system-prompt hint) are exposed for explicit
on-demand search / save. This matches AgentScope 1.x's
`ReActAgent.long_term_memory_mode` default.

## Sharing one middleware across agents

The local OSS mem0 backend uses on-disk Qdrant by default, and Qdrant
takes an **exclusive lock** on the storage folder
(``/tmp/qdrant`` by default). Two ``Mem0Middleware`` instances each
built from ``chat_model`` + ``embedding_model`` would each construct
their own ``AsyncMemory`` → second one crashes on the lock:

```
RuntimeError: Storage folder /tmp/qdrant is already accessed by
another instance of Qdrant client.
```

Fix: build **one** ``Mem0Middleware`` instance and pass it to every
agent that should share the same memory namespace:

```python
mw = Mem0Middleware(
    user_id="alice",
    chat_model=chat_model,
    embedding_model=embedding_model,
    mode="both",
)
agent_a = Agent(
    ...,
    toolkit=Toolkit(tools=await mw.list_tools()),
    middlewares=[mw],
)
agent_b = Agent(
    ...,
    toolkit=Toolkit(tools=await mw.list_tools()),
    middlewares=[mw],
)
```

This is what the demo does. The memory tools receive the live
`AgentState` at call time, and the middleware resolves the active
agent by `state.session_id`, so sharing one middleware across agents
is safe.

If you genuinely need a separate Qdrant store per agent, pass a
``mem0_config`` with a distinct ``vector_store.config.path`` or
``collection_name`` for each one.

### Recommended: run Qdrant in Docker (especially on Windows)

The local on-disk Qdrant works for single-process demos but is
brittle in real deployments — and **outright painful on Windows**,
where the filesystem-lock semantics differ from Unix and the
exclusive-lock failure mode is harder to recover from. For anything
beyond a single-process Linux/macOS sandbox, run Qdrant as a service:

```bash
docker run -p 6333:6333 -p 6334:6334 \
    -v $(pwd)/qdrant_storage:/qdrant/storage \
    qdrant/qdrant
```

Then point mem0 at it instead of the on-disk path:

```python
from mem0.configs.base import MemoryConfig
from mem0.vector_stores.configs import VectorStoreConfig

mem0_cfg = MemoryConfig(
    vector_store=VectorStoreConfig(
        provider="qdrant",
        config={
            "collection_name": "mem0",
            "host": "localhost",     # the Docker container
            "port": 6333,
            "embedding_model_dims": 1536,
        },
    ),
)
Mem0Middleware(
    user_id="alice",
    chat_model=chat_model,
    embedding_model=embedding_model,
    mem0_config=mem0_cfg,
)
```

Benefits over on-disk:

- No file-lock contention — multiple Python processes can connect.
- Survives across runs without manual file cleanup.
- Same shape works for remote Qdrant (Qdrant Cloud, your own
  Kubernetes deployment) — just change ``host`` / ``port`` /
  ``api_key``.

## Memory scoping (`user_id` × `agent_id`)

mem0 tags every stored memory with the `user_id` and `agent_id`
filter values passed at `add` time, and searches by AND-matching those
tags. The middleware exposes the agent dimension via the
`scope_search_by_agent` flag (default `True`):

| `scope_search_by_agent` | What `add` tags the memory with | What `search` filters by | Effect |
| --- | --- | --- | --- |
| `True` (default) | `user_id` + `agent_id` | `user_id` + `agent_id` | Strict per-agent silos. Agent A's memories invisible to agent B for the same user. |
| `False` | `user_id` + `agent_id` (unchanged) | `user_id` only | Read-broad, write-narrow. All agents for the same user share a memory pool, but each memory still records which agent wrote it (visible in mem0 metadata). |

`agent_id` defaults to `agent.name`. Override via `agent_id="..."` or
`agent_id=lambda agent: ...` on the middleware constructor.

When to relax `scope_search_by_agent`:

- One user has multiple specialized agents (research / coding /
  scheduling) that should benefit from each other's discoveries about
  the user.
- An agent's `name` might change across deployments but you want the
  memory to persist across name changes.

### A note on agent-centric extraction (currently unreachable)

mem0 v2's extraction prompt
([`ADDITIVE_EXTRACTION_PROMPT`](https://github.com/mem0ai/mem0/blob/main/mem0/configs/prompts.py))
has a conditional suffix that switches framing from user-centric
("User stated X") to **agent-centric** ("Agent was informed of X" /
"Agent recommended Y"). It's gated on
`is_agent_scoped = bool(filters.agent_id) and not filters.user_id` —
i.e. only when `agent_id` is provided *without* `user_id`. The
middleware always passes `user_id` (it's a required constructor arg),
so this agent-centric suffix is unreachable through `Mem0Middleware`
today. In practice that's fine — agent persona / configuration is
usually expressed via system prompt rather than long-term memory.

## Service-mode integration (`agentscope.app`)

The demos above use the **library mode** — you construct `Agent`
yourself and pass `Mem0Middleware` into its `middlewares=[...]`. For
production deployments via `agentscope.app` (the FastAPI service
layer), the `user_id` already flows through the framework from the
`X-User-ID` HTTP header. Hook in through the
[`extra_agent_middlewares`](../../../../src/agentscope/app/_types.py)
factory:

```python
from agentscope.app import create_app
from agentscope.middleware import Mem0Middleware
from agentscope.middleware._longterm_memory._mem0._agentscope_adapter \
    import build_mem0_config
from mem0 import AsyncMemory

# Build the mem0 client ONCE at module scope — local OSS Qdrant
# takes an exclusive lock on its storage folder; per-request
# construction would deadlock under concurrent traffic.
chat_model = ...     # shared AgentScope ChatModelBase
emb_model  = ...     # shared AgentScope EmbeddingModelBase
mem0_client = AsyncMemory(
    config=build_mem0_config(
        chat_model=chat_model,
        embedding_model=emb_model,
    ),
)


async def long_term_memory_factory(
    user_id: str,         # ← from the authenticated X-User-ID header
    agent_id: str,
    session_id: str,
) -> list:
    return [
        Mem0Middleware(
            user_id=user_id,
            client=mem0_client,    # shared across all requests
            mode="both",
        ),
    ]


app = create_app(
    ...,
    extra_agent_middlewares=long_term_memory_factory,
)
```

Key points:

- The factory is `async (user_id, agent_id, session_id) ->
  list[MiddlewareBase]`, called **once per agent assembly**
  (i.e. per chat turn / scheduled trigger). It returns fresh
  `Mem0Middleware` instances each time, but they share a single
  underlying mem0 client.
- `user_id` is the authenticated caller, injected by `agentscope.app`
  via `get_current_user_id` (currently from `X-User-ID` header; will
  become JWT-based when auth lands upstream). You forward it straight
  to `Mem0Middleware(user_id=user_id, ...)` — no resolver callable
  needed.
- For hosted mem0 Platform, swap the `AsyncMemory(config=...)`
  construction for `AsyncMemoryClient(api_key=...)` — same factory
  shape, no Qdrant lock concern.

## Notes on the AgentScope-as-mem0-backend path

When you pass `chat_model` + `embedding_model`, the middleware
internally:

1. Registers `AgentScopeLLM` / `AgentScopeEmbedding` in mem0's factory
   dicts under provider name `"agentscope"`.
2. Substitutes `LlmConfig` / `EmbedderConfig` with subclasses whose
   validator allows `"agentscope"` (mem0's stock validator hardcodes a
   whitelist that doesn't include us). Other providers continue to be
   rejected with mem0's original error.
3. Builds an `AsyncMemory` whose `.llm` and `.embedding_model` route
   through the AgentScope adapters.
4. Bridges mem0's sync API onto AgentScope's async models via a
   persistent background event loop, so async clients (e.g. Ollama's
   `AsyncClient`) keep their connection pool across calls.

Your embedding model's `dimensions` must match the vector store's
expected dim — mem0's default Qdrant expects 1536, which matches
DashScope's `text-embedding-v2` at `dimensions=1536` (the value used
in `oss_demo.py`).
