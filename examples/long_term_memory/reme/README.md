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

AgentScope's minimal embedded ReMe config searches with **BM25 (keyword)
only** by default. A long-term *memory* demo wants **semantic** recall
("plot monthly sales" should find a "prefers matplotlib" card), so
`reme_demo.py` injects an `embedding_model` — which turns the vector store
on automatically; see below.

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
| provided | Injected into the embedded app's default-named LLM / embedding components at start; only a DashScope key is needed. An `embedding_model` also enables the vector store for semantic search. |
| omitted | AgentScope's minimal config supplies the LLM from ReMe's `LLM_*` environment variables and stays keyword-only without an `embedding_model`. |

> **Why inject `embedding_model`?** The minimal config omits embedding
> components in BM25-only mode. Providing an AgentScope `embedding_model`
> adds and wires those components before ReMe starts, and powers vector
> search without requiring separate ReMe embedding credentials.

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

The middleware always builds an AgentScope-owned minimal ReMe config. It keeps
the full memory lifecycle: conversation write-back, nightly/manual dream
consolidation from daily cards into digest memory, and search across both daily
and digest memory. Only the file/index jobs required by that lifecycle are
registered; ReMe's resource ingestion, independent chat, daily-paper and
operational jobs are not loaded. Search is **BM25-only** unless an
`embedding_model` is provided:

```python
ReMeMiddleware(
    workspace_dir=".reme",
    parameters=ReMeMiddleware.Parameters(
        embedding_model=my_embedding_model,  # turns the vector store on
    ),
)
```

The minimal config's `as_llm` component honors ReMe's `LLM_*` environment
variables; injecting an AgentScope `chat_model` bypasses those.

> **Note (indexing):** `auto_memory` write-back returns as soon as the
> daily card is written to disk; the card only becomes searchable once
> ReMe indexes it. The demo forces a synchronous `reindex` after each
> write so the next read deterministically sees it, rather than relying
> on ReMe's background index loop. See `_reindex` in `reme_demo.py`.
