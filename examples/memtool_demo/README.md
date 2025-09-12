MemTool-style Dynamic Tool Management (AgentScope)

Overview
- Demonstrates a MemTool-inspired workflow in AgentScope: deterministically prune → search → execute with a curated toolset per-turn.
- Components:
  - Orchestrator: runs the MemTool workflow and delegates execution to a Worker.
  - ToolManager: maintains `active_tools` and selects relevant tools from a simple Tool KB.
  - Worker: a `ReActAgent` equipped only with the curated tool subset.

Autonomous Agent Mode (Full Agency)
- Includes an autonomous variant with `search_tools` and `remove_tools` exposed to the agent. The agent searches a Tool KB to equip specific tools and removes stale ones inside its reasoning loop (no external orchestrator).

Prerequisites
- Python dependencies are the same as this repo.
- Configure a chat model by environment variable:
  - DashScope: set `DASHSCOPE_API_KEY`, uses `qwen-max` by default.
  - OpenAI: set `OPENAI_API_KEY`, uses `gpt-4o-mini` by default.
 - Optional tuning:
   - `AGENT_DOMAIN_EXPERTISE` to set domain (e.g., "Finance Analysis").
   - `AGENT_MAX_TOOL_COUNT` to set the memory budget (default 6).

Run
- Workflow mode:
  - `python examples/memtool_demo/main.py "请计算 2+2 和 3*7 并把结果保存到 notes.txt"`
- Autonomous (full agency) mode:
  - `python -m examples.memtool_demo.agentic_mode "请计算 2+2 并把结果保存到 notes.txt"`

What it shows
- For each user query, the Orchestrator:
  - prunes irrelevant tools from previous turn’s `active_tools`;
  - searches the Tool KB for new relevant tools (lexical scoring);
  - instantiates a Worker agent with only the active tools registered;
  - the Worker solves the task via tool calls and returns the final response.

- In Autonomous mode, the agent:
  - calls `Search_Tools(["math", "write file"])` to add tools like `calculate_expression`, `write_text_file` into context;
  - calls `Remove_Tools(["..."])` to prune tools and keep within budget;
  - then executes equipped tools and finally calls `generate_response`.

Output requirement
- The finish function enforces a schema with `used_tools: list[str]` (and optional `sources`). Include the tools used in your final `generate_response` call.

Notes
- The search/prune logic is intentionally simple (lexical overlap) to keep the example self-contained and offline-friendly.
- To expand, plug in embeddings (e.g., DashScope/OpenAI) to rank tools or store tool-usage traces as long-term memory.

Embedding + RRF
- If `OPENAI_API_KEY` (and optional `OPENAI_EMBEDDING_MODEL`, default `text-embedding-3-small`) or `DASHSCOPE_API_KEY` is present, Autonomous mode performs Reciprocal Rank Fusion (RRF) over:
  - Lexical rank (token overlap on name/description/tags)
  - Embedding rank (cosine similarity between query and tool docs)
- Otherwise, it falls back to lexical-only ranking.

Embedding cache
- Uses a file-based cache for embeddings to reduce API calls.
- Env controls:
  - `EMBEDDING_CACHE_DIR` (default: `examples/memtool_demo/.cache/embeddings`)
  - `EMBEDDING_CACHE_MAX_FILES` (optional max number of files)
  - `EMBEDDING_CACHE_MAX_MB` (optional max size in MB)

Tracing add/remove events
- The autonomous agent logs add/remove operations via the Agentscope `logger` and emits OpenTelemetry spans when tracing is enabled.
- To enable OTel, initialize once in your entry file:
  - `from agentscope.tracing import setup_tracing`
  - `setup_tracing(otlp_endpoint="http://localhost:4318/v1/traces")`
