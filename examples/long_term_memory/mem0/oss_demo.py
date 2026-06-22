# -*- coding: utf-8 -*-
"""Mem0 middleware demo (open-source mem0, AgentScope-driven).

Drives two independent agent sessions for the same ``user_id`` so
mem0's cross-session memory effect is visible. Each turn streams
events from ``agent.reply_stream`` and prints the ones that matter:

- ``[mem0 → context (static)]`` — the memory note the middleware
  appended to ``state.context`` (fires at ``ReplyStartEvent``, before
  any tool call, so the output order matches the data flow)
- ``[tool call (agent)]`` — each ``search_memory`` / ``add_memory``
  invocation the agent makes on its own
- ``[assistant]`` — the assistant's reply, concatenated from the
  ``TextBlockDeltaEvent`` stream
- ``[context → mem0 (static)]`` — facts the middleware wrote back
  after the turn

The mode tag (``static`` / ``agent``) on each line tells you which
control path produced it.

Starts each run from a clean mem0 store (for ``user_id``) so the
demo is reproducible.

Requires:
    pip install agentscope mem0ai
    export DASHSCOPE_API_KEY=sk-...
"""
import asyncio
import logging
import os
import shutil

from mem0 import AsyncMemory
from mem0.configs.base import MemoryConfig
from mem0.vector_stores.configs import VectorStoreConfig

from agentscope.agent import Agent
from agentscope.credential import DashScopeCredential
from agentscope.embedding import DashScopeEmbeddingModel
from agentscope.event import (
    ReplyStartEvent,
    TextBlockDeltaEvent,
    ToolCallDeltaEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultTextDeltaEvent,
)
from agentscope.message import UserMsg
from agentscope.middleware import Mem0Middleware
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit


MODE = "both"  # try "static_control" or "agent_control" too


# Silence mem0's noisy init warnings (qdrant migration notice, spaCy
# missing, fastembed missing) — they're informational, not actionable.
logging.getLogger("mem0").setLevel(logging.ERROR)


async def _facts_in_mem0(client: AsyncMemory, user_id: str) -> list[str]:
    """Return mem0's current facts for ``user_id`` as plain strings."""
    res = await client.get_all(filters={"user_id": user_id})
    items = res.get("results", res) if isinstance(res, dict) else res
    out: list[str] = []
    for m in items:
        if isinstance(m, dict):
            text = m.get("memory")
            if isinstance(text, str) and text:
                out.append(text)
    return out


def _injected_memory_bullets(agent: Agent) -> list[str]:
    """Extract the bullet lines from the memory note the middleware
    appended to ``agent.state.context`` (if any) — strips the section
    header and intro so we see only the actual retrieved facts."""
    for msg in agent.state.context:
        if getattr(msg, "name", None) != "memory":
            continue
        hint_text = "\n".join(
            block.hint for block in msg.get_content_blocks("hint")
        )
        return [
            line[2:].strip()
            for line in hint_text.splitlines()
            if line.startswith("- ")
        ]
    return []


async def _run_turn(agent: Agent, user_msg: UserMsg) -> str:
    """Drive one reply turn through ``agent.reply_stream`` and print
    each middleware contribution as it happens, in the order it
    happens:

    1. ``ReplyStartEvent`` fires right after the agent ingests the new
       user input AND the middleware has appended any retrieved memory
       note to ``state.context`` (static path) — so this is the right
       place to surface ``[mem0 → context]``.
    2. ``ToolCallStartEvent`` / ``ToolResultEndEvent`` bracket each
       ``search_memory`` / ``add_memory`` invocation the agent makes
       on its own (agent path).
    3. ``TextBlockDeltaEvent`` carries the assistant's streamed reply
       text — concatenating every delta yields the final message
       content (there is no separate "final Msg" reply_stream
       withholds).

    Each printed line is tagged with the mem0 control path that
    produced it (``static`` vs ``agent``) so the demo stays readable
    in any of the three modes.
    """
    pending_args: dict[str, str] = {}
    pending_names: dict[str, str] = {}
    pending_results: dict[str, str] = {}
    text_parts: list[str] = []
    memory_announced = False

    async for ev in agent.reply_stream(inputs=user_msg):
        if isinstance(ev, ReplyStartEvent) and not memory_announced:
            injected = _injected_memory_bullets(agent)
            print(
                f"[mem0 → context (static)] retrieved "
                f"{len(injected)} memory note(s):",
            )
            for b in injected:
                print(f"  ← {b}")
            memory_announced = True
        elif isinstance(ev, ToolCallStartEvent):
            pending_names[ev.tool_call_id] = ev.tool_call_name
            pending_args[ev.tool_call_id] = ""
            pending_results[ev.tool_call_id] = ""
        elif isinstance(ev, ToolCallDeltaEvent):
            pending_args[ev.tool_call_id] += ev.delta
        elif isinstance(ev, ToolResultTextDeltaEvent):
            pending_results[ev.tool_call_id] += ev.delta
        elif isinstance(ev, ToolResultEndEvent):
            name = pending_names.pop(ev.tool_call_id, "<unknown>")
            args = pending_args.pop(ev.tool_call_id, "")
            result = pending_results.pop(ev.tool_call_id, "")
            # ev.state is a StrEnum at the type level but pydantic
            # may have deserialized it to a plain str; f-string both
            # cases yields the same value string.
            print(f"[tool call (agent)] {name}({args})  → state={ev.state}")
            for line in result.splitlines() or [""]:
                if line:
                    print(f"  → {line}")
        elif isinstance(ev, TextBlockDeltaEvent):
            text_parts.append(ev.delta)

    return "".join(text_parts)


async def main() -> None:
    """Drive two cross-session agent turns and print middleware effects."""
    api_key = os.environ["DASHSCOPE_API_KEY"]
    user_id = "alice"

    # Wipe mem0's local files BEFORE constructing the client so each
    # demo run starts clean. We don't use ``mem0_client.delete_all()``
    # because qdrant-client's local SQLite layer has a race under
    # mem0's parallel ``asyncio.gather`` deletes (sqlite3.InterfaceError).
    qdrant_path = "/tmp/qdrant"  # matches the vector_store config below
    history_db = os.path.expanduser("~/.mem0/history.db")
    print("=== resetting mem0 local state ===")
    print(f"  rm -rf {qdrant_path}")
    shutil.rmtree(qdrant_path, ignore_errors=True)
    print(f"  rm -f  {history_db}")
    try:
        os.remove(history_db)
    except FileNotFoundError:
        pass

    # `stream` can be True or False — the AgentScope→mem0 adapter
    # drains an async generator and uses the last chunk (which carries
    # the full accumulated content per AgentScope's streaming contract).
    # The agent's own `reply_stream` still emits per-delta events
    # regardless of this setting, so streaming-mode does not change the
    # demo's printed output shape.
    chat_model = DashScopeChatModel(
        credential=DashScopeCredential(api_key=api_key),
        model="qwen3.7-max",
        stream=False,
    )
    embedding_model = DashScopeEmbeddingModel(
        credential=DashScopeCredential(api_key=api_key),
        model="text-embedding-v4",
        parameters=DashScopeEmbeddingModel.Parameters(
            dimensions=1536,  # matches mem0's Qdrant default
        ),
    )

    # Explicit vector-store config (here we just spell out mem0's
    # default local Qdrant — collection ``mem0``, on-disk at
    # ``/tmp/qdrant``, 1536-d vectors). Override any of these for
    # remote Qdrant, alternate collection names, etc. Pass the
    # ``MemoryConfig`` through ``mem0_config=`` and the middleware
    # keeps everything you set, only swapping ``.llm`` and
    # ``.embedder`` to route through your AgentScope models.
    #
    # Recommended for Windows users and any production setup: run
    # Qdrant in Docker and connect over the network instead of using
    # the local on-disk backend. Local on-disk Qdrant takes an
    # exclusive file lock that's brittle on Windows (different
    # filesystem-lock semantics) and breaks under concurrent agent
    # instances. To switch:
    #
    #   docker run -p 6333:6333 -p 6334:6334 \\
    #       -v $(pwd)/qdrant_storage:/qdrant/storage \\
    #       qdrant/qdrant
    #
    #   vector_store=VectorStoreConfig(
    #       provider="qdrant",
    #       config={
    #           "collection_name": "mem0",
    #           "host": "localhost",   # Docker container
    #           "port": 6333,
    #           "embedding_model_dims": 1536,
    #       },
    #   )
    #
    # (You'd also drop the ``shutil.rmtree(qdrant_path)`` wipe above —
    # state lives in the Docker volume, not the local filesystem.)
    mem0_cfg = MemoryConfig(
        vector_store=VectorStoreConfig(
            provider="qdrant",
            config={
                "collection_name": "mem0",
                "path": qdrant_path,
                "embedding_model_dims": 1536,
                "on_disk": False,
            },
        ),
    )

    mw = Mem0Middleware(
        user_id=user_id,
        agent_id="datascope_assistant",
        chat_model=chat_model,
        embedding_model=embedding_model,
        mem0_config=mem0_cfg,
        mode=MODE,
        top_k=5,
    )
    # For the hosted mem0 Platform, swap the construction above for:
    #
    # from mem0 import AsyncMemoryClient
    # mw = Mem0Middleware(
    #       user_id=user_id,
    #       client=AsyncMemoryClient(api_key=os.environ["MEM0_API_KEY"]),
    #       mode=MODE,
    #   )
    #
    # No local Qdrant / vector_store config needed — extraction and
    # storage all happen in mem0's cloud service.
    # pylint: disable-next=protected-access
    mem0_client: AsyncMemory = mw._client  # demo only — peek at the
    # constructed client to inspect mem0 state between turns.

    # =================================================================
    # SESSION 1
    # =================================================================
    print(f"\n=== SESSION 1 (mode={MODE!r}) ===")
    user_msg_1 = (
        "Hi! For any chart, please default to dark mode and use "
        "matplotlib. Also I'm based in Hangzhou."
    )
    print(f"\n[user] {user_msg_1}\n")

    before = await _facts_in_mem0(mem0_client, user_id)

    agent = Agent(
        name="datascope_assistant",
        system_prompt=(
            "You are a helpful data-analysis assistant. Be concise. "
            "If you learn a durable user preference, save it with "
            "the add_memory tool when one is available."
        ),
        model=chat_model,
        toolkit=Toolkit(tools=await mw.list_tools()),
        middlewares=[mw],
    )
    reply_text = await _run_turn(agent, UserMsg("alice", user_msg_1))
    print(f"\n[assistant] {reply_text}")

    after = await _facts_in_mem0(mem0_client, user_id)
    new = [f for f in after if f not in before]
    print(f"\n[context → mem0 (static)] extracted {len(new)} new fact(s):")
    for f in new:
        print(f"  + {f}")

    # =================================================================
    # SESSION 2 — fresh Agent, empty chat context. mem0 should bridge.
    # =================================================================
    print(f"\n=== SESSION 2 (fresh agent, mem0 bridges; mode={MODE!r}) ===")
    user_msg_2 = (
        "Plot me a bar chart of monthly sales — pick reasonable "
        "defaults for theme and library."
    )
    print(f"\n[user] {user_msg_2}\n")

    before = await _facts_in_mem0(mem0_client, user_id)

    agent = Agent(
        name="datascope_assistant",
        system_prompt=(
            "You are a helpful data-analysis assistant. Be concise. "
            "If you learn a durable user preference, save it with "
            "the add_memory tool when one is available."
        ),
        model=chat_model,
        toolkit=Toolkit(tools=await mw.list_tools()),
        middlewares=[mw],
    )
    reply_text = await _run_turn(agent, UserMsg("alice", user_msg_2))
    print(f"\n[assistant] {reply_text}")

    after = await _facts_in_mem0(mem0_client, user_id)
    new = [f for f in after if f not in before]
    print(f"\n[context → mem0 (static)] extracted {len(new)} new fact(s):")
    for f in new:
        print(f"  + {f}")


if __name__ == "__main__":
    asyncio.run(main())
