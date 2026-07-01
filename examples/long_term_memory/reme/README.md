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
find a "prefers matplotlib" card), so `reme_demo.py` builds the app
with the vector store switched on and hands it to the middleware via
`app=`. That path needs an `embedding_model`; see below.

## Install

```bash
# reme-ai is an optional AgentScope dependency — pull it via the extra:
pip install "agentscope[reme]"
# (equivalent to `pip install agentscope reme-ai`)

export DASHSCOPE_API_KEY=sk-...
```

## Import path

`ReMeMiddleware` is exported from the middleware package:

```python
from agentscope.middleware import ReMeMiddleware
from agentscope.tool import Toolkit
```

## Two construction paths

```python
# 1. Config params — the middleware builds and embeds a reme.ReMe app
#    lazily on first use. `chat_model` is injected into ReMe's default
#    LLM component and drives the auto_memory write-back;
#    `embedding_model` is injected into its embedding component and
#    drives vector search. Both are fixed for the app's lifetime.
ReMeMiddleware(
    workspace_dir=".reme",
    chat_model=my_chat_model,
    embedding_model=my_embedding_model,
    mode="both",
)

# 2. Pre-built app — bring your own reme.ReMe to share one embedded
#    app (workspace / index) across agents, or to apply advanced
#    config the constructor does not expose (e.g. enabling the vector
#    store). `workspace_dir` / `config` are then ignored (a WARNING log
#    says so). This is the path the demo takes.
from reme import ReMe
from reme.config import resolve_app_config

app = ReMe(**resolve_app_config(
    config="default",
    workspace_dir=".reme",
    # default.yaml ships the vector store off; switch it on for
    # semantic recall.
    components={"file_store": {"default": {"embedding_store": "default"}}},
))
ReMeMiddleware(
    app=app,
    chat_model=my_chat_model,
    embedding_model=my_embedding_model,
    mode="both",
)
```

| `app` | `workspace_dir` / `config` | `chat_model` | Behavior |
|:-:|:-:|:-:|---|
| ✓ | — | any | Embed `app` as-is; `chat_model` / `embedding_model` still injected into its components at start. |
| ✓ | changed | any | Embed `app`; `workspace_dir` / `config` ignored with a `WARNING`. |
| — | any | ✓ | Build a `reme.ReMe` from `config` + `workspace_dir`, inject the models. |
| — | any | — | Build a `reme.ReMe`; ReMe uses the LLM / embedding from its own config/credentials. |

> **Why inject `embedding_model`?** ReMe starts its embedding
> component eagerly at `start()` — even under the BM25-only default —
> and builds it from credentials in its config. Injecting an
> AgentScope `embedding_model` bypasses that credential path, so the
> only key you need is a DashScope one. It is also what powers vector
> search once you enable the store (path 2 above).

## How the middleware controls memory

ReMe **always** writes the new exchange back through `auto_memory`
after each reply, in every mode — `mode` only selects how the agent
*retrieves*:

### `static_control`
The middleware does the retrieval, the agent is unaware:

1. **`on_reply` (pre)** searches ReMe with the latest user message.
2. **At `ReplyStartEvent`** — right after the agent ingests the new
   user input and before the reasoning loop — the middleware appends
   an `AssistantMsg(name="memory", ...)` `HintBlock` to
   `state.context`, immediately after the user's message.
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
`embedding_store: ""`). To enable **vector search**, either point
`config` at your own ReMe config file with the store wired up, or build
the app yourself and flip it on inline (what the demo does):

```python
resolve_app_config(
    config="default",
    workspace_dir=".reme",
    components={"file_store": {"default": {"embedding_store": "default"}}},
)
```

ReMe's `as_llm` / `as_embedding` components are otherwise driven by
environment variables (`LLM_API_KEY`, `EMBEDDING_API_KEY`, ...) from its
own config; injecting AgentScope `chat_model` / `embedding_model`
bypasses those. See ReMe's `default.yaml` for the full component set.

> **Note (indexing):** `auto_memory` write-back returns as soon as the
> daily card is written to disk; the card only becomes searchable once
> ReMe indexes it. The demo forces a synchronous `reindex` after each
> write so the next read deterministically sees it, rather than relying
> on ReMe's background index loop. See `_reindex` in `reme_demo.py`.
