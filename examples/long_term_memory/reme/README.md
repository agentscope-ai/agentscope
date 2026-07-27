# ReMe middleware example

One runnable demo (`reme_demo.py`) showing the
[ReMe](https://github.com/agentscope-ai/ReMe) middleware plugged into
an `agentscope.agent.Agent`. Drives two consecutive agent **sessions**
that share one ReMe workspace so ReMe's cross-session memory effect is
visible, and prints each middleware contribution (retrieval / tool
call / write-back) inline so you can see when each path fires.

ReMe is the AgentScope team's own file-based memory toolkit. Unlike
mem0, it is **embedded in-process** — there is no separate service to
run — and it records memory by **listening to the conversation**:
after every reply the new exchange is written back automatically via
ReMe's `auto_memory` job. The agent never saves memory itself; there
is no add tool. The demo drives ReMe with AgentScope's own DashScope
chat model (LLM-backed `auto_memory` write-back) and DashScope
embedding model (vector search), both injected into the embedded app.

ReMe's bundled `default` config searches with **BM25 (keyword) only** —
its file store ships with the vector store disabled. A long-term
*memory* demo wants **semantic** recall ("plot monthly sales" should
find a "prefers matplotlib" card), so `reme_demo.py` injects an
`embedding_model` — which turns ReMe's vector store on automatically;
see below.

## Install

```bash
# reme-ai is an optional AgentScope dependency — pull it via the extra:
pip install "agentscope[memory-reme]"
# (equivalent to `pip install agentscope reme-ai`)

export DASHSCOPE_API_KEY=sk-...
```

## Import path

`ReMeMiddleware` is exported from the middleware package:

```python
from agentscope.middleware import ReMeMiddleware
from agentscope.tool import Toolkit
```

## Construction

The middleware builds and **owns** an embedded `reme.ReMe` app — it is
created lazily on first use and torn down by `await mw.close()`. You
configure it with plain parameters; there is no external app to manage.
User-tunable settings live on a nested `Parameters` model (the agent
service renders its JSON schema as a form):

```python
ReMeMiddleware(
    workspace_dir=".reme",
    parameters=ReMeMiddleware.Parameters(
        chat_model=my_chat_model,        # injected into ReMe's LLM component,
                                         #   drives auto_memory write-back
        embedding_model=my_embedding_model,  # injected into its embedding
                                             #   component; also turns ReMe's
                                             #   vector store ON automatically
        mode="both",
        top_k=5,
    ),
)
```

Both models are fixed for the app's lifetime (never taken from an
agent), so the embedded app's single LLM / embedding component is
well-defined even when one middleware instance is shared across agents.

| `chat_model` / `embedding_model` | Behavior |
|:-:|---|
| provided | Injected into ReMe's default LLM / embedding components at start; only a DashScope key is needed. An `embedding_model` also enables the vector store for semantic search. |
| omitted | ReMe uses the LLM / embedding backend from its own config/credentials; search stays keyword-only. |

> **Why inject `embedding_model`?** ReMe starts its embedding
> component eagerly at `start()` — even under the BM25-only default —
> and builds it from credentials in its config. Injecting an
> AgentScope `embedding_model` bypasses that credential path, so the
> only key you need is a DashScope one. It is also what powers vector
> search: providing it flips ReMe's file store from BM25-only to the
> vector store automatically.

## How the middleware controls memory

ReMe **always** writes the new exchange back through `auto_memory`
after each reply, in every mode — `mode` only selects how the agent
*retrieves*:

### `static_control`
The middleware does the retrieval, the agent is unaware:

1. **`on_reply` (pre)** starts a background `asyncio` task that searches
   ReMe with the latest user message, running concurrently with the reply.
2. **`on_reasoning`** polls that task before each reasoning step; once it
   has finished, the middleware appends an
   `AssistantMsg(name="memory", ...)` `HintBlock` to `state.context` so
   the *next* model call sees it. Injection is **best-effort**: a
   single-shot reply (one model call) may finish before retrieval does, so
   the hint lands on a later step or is skipped for that turn — the same
   trade-off as `AgenticMemoryMiddleware`. Turns with a tool call (two or
   more reasoning steps) inject reliably.
3. **`on_reply` (post)** writes the new `(user, assistant)` exchange
   back via `auto_memory`.

The injected memory message **persists** in the agent's context across
turns. If long sessions accumulate too many, post-process with
`compress_context` or a custom middleware.

### `agent_control`
The middleware lists a single `memory_search(query, limit)` tool and
otherwise stays out of the way (auto write-back still runs). Pass it
into the agent's toolkit explicitly:

```python
mw = ReMeMiddleware(..., mode="agent_control")
agent = Agent(
    ...,
    toolkit=Toolkit(tools=await mw.list_tools()),
    middlewares=[mw],
)
```

The system prompt gets a short nudge telling the agent the search tool
exists; per-tool usage guidance comes through the standard tool schema.
No automatic retrieval.

### `both` (default)
Both retrieval paths are active: memories are auto-retrieved and
appended to the agent's context as an assistant note, AND the
`memory_search` tool (with its system-prompt hint) is exposed for
explicit on-demand search.

## Memory scoping (`session_id`)

ReMe scopes write-back by **`session_id`**, read live from
`agent.state.session_id` at hook time — never stored on the
middleware. Search runs **workspace-wide** (across every session),
which is what lets a later session recall an earlier one's memories
even with a different `session_id`. To pin a resumable session, set
the id on the agent:

```python
from agentscope.state import AgentState

agent = Agent(..., state=AgentState(session_id="alice-main"))
```

The demo does exactly this — `session-1` writes the preference,
`session-2` (a fresh agent, empty chat context) recalls it through the
shared workspace.

## Sharing one middleware across agents

Because the `session_id` is read per call (not stored) and the chat
model is fixed at construction (tied to the embedded app's single
LLM), **one** `ReMeMiddleware` can be safely shared across many agents
and sessions — build it once and pass it to each agent:

```python
mw = ReMeMiddleware(
    workspace_dir=".reme",
    chat_model=chat_model,
    embedding_model=embedding_model,
    mode="both",
)
agent_a = Agent(..., middlewares=[mw], state=AgentState(session_id="a"))
agent_b = Agent(..., middlewares=[mw], state=AgentState(session_id="b"))
```

This is what the demo does. Call `await mw.close()` on shutdown to tear
down the embedded app (AgentScope doesn't manage middleware lifecycle).

## Configuration

`config` selects a ReMe config (defaults to the bundled `"default"`,
which is auto-memory + **BM25-only** search — its file store ships with
`embedding_store: ""`). To enable **vector search**, provide an
`embedding_model` (what the demo does) — the middleware then wires
ReMe's file store to the default embedding store automatically:

```python
ReMeMiddleware(
    workspace_dir=".reme",
    parameters=ReMeMiddleware.Parameters(
        embedding_model=my_embedding_model,  # turns the vector store on
    ),
)
```

ReMe's `as_llm` / `as_embedding` components are otherwise driven by
environment variables (`LLM_API_KEY`, `EMBEDDING_API_KEY`, ...) from its
own config; injecting AgentScope `chat_model` / `embedding_model`
bypasses those. If you need a config the dedicated parameters don't
expose, point `config` at your own ReMe config file. See ReMe's
`default.yaml` for the full component set.

> **Note (indexing):** `auto_memory` write-back returns as soon as the
> daily card is written to disk; the card only becomes searchable once
> ReMe indexes it. The demo forces a synchronous `reindex` after each
> write so the next read deterministically sees it, rather than relying
> on ReMe's background index loop. See `_reindex` in `reme_demo.py`.
