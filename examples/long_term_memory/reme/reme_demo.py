# -*- coding: utf-8 -*-
"""ReMe middleware demo (embedded reme-ai, AgentScope-driven).

Drives two independent agent **sessions** that share one ReMe
workspace, so ReMe's cross-session memory effect is visible: session 1
states a durable preference, the middleware writes it back
automatically, and a *fresh* session-2 agent — with an empty chat
context — recalls it through ReMe.

Each turn streams events from ``agent.reply_stream`` and prints the
ones that matter:

- ``[reme → context (static)]`` — the memory note the middleware
  appended to ``state.context``. Retrieval runs in the background and the
  note is injected on a reasoning step once the search finishes, so the
  demo surfaces it the moment it appears (best-effort: a single-shot reply
  may finish before it lands)
- ``[tool call (agent)]`` — each ``memory_search`` invocation the
  agent makes on its own (there is no add tool — writing is automatic)
- ``[assistant]`` — the assistant's reply, concatenated from the
  ``TextBlockDeltaEvent`` stream
- ``[context → reme (auto)]`` — what ReMe persisted after the turn,
  surfaced by searching the workspace

The mode tag (``static`` / ``agent``) on each line tells you which
control path produced it.

Unlike mem0, ReMe is **embedded in-process** (no separate service to
run) and records memory by **listening to the conversation** — after
every reply the new exchange is written back via ReMe's ``auto_memory``
job, in *all* modes. ``mode`` only controls *retrieval*. The agent
never writes memory itself.

ReMe drives its LLM-backed ``auto_memory`` write-back and its vector
search through AgentScope models injected here — a DashScope chat model
and embedding model — so the only credential needed is a DashScope key.

Starts each run from a clean workspace so the demo is reproducible.

Requires:
    pip install "agentscope[memory-reme]"
    export DASHSCOPE_API_KEY=sk-...
"""
import asyncio
import logging
import os
import shutil
import tempfile

from agentscope.agent import Agent
from agentscope.credential import DashScopeCredential
from agentscope.embedding import DashScopeEmbeddingModel
from agentscope.event import (
    TextBlockDeltaEvent,
    ToolCallDeltaEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultTextDeltaEvent,
)
from agentscope.message import UserMsg
from agentscope.middleware import ReMeMiddleware
from agentscope.model import DashScopeChatModel
from agentscope.state import AgentState
from agentscope.tool import Toolkit


MODE = "both"  # try "static_control" or "agent_control" too

# ReMe writes its memory cards and indexes here. Defaults to a fresh,
# empty random temp dir (created per run) so runs never collide and
# nothing lands in the repo. Override with REME_WORKSPACE_DIR (e.g.
# ".reme") to keep the cards around for inspection; a user-named dir is
# reused as-is and only wiped when you also set REME_DEMO_RESET=1 — the
# demo never silently deletes a directory you pointed it at (it could be
# a real ReMe workspace, a project dir, ~/.reme, ...).
_ENV_WORKSPACE = os.environ.get("REME_WORKSPACE_DIR")
WORKSPACE_DIR = _ENV_WORKSPACE or tempfile.mkdtemp(prefix="reme_demo_")

# Quiet ReMe's informational startup logs so the demo output stays
# focused on the middleware contributions we print ourselves.
logging.getLogger("reme").setLevel(logging.ERROR)


async def _memories_in_reme(mw: ReMeMiddleware, query: str) -> list[str]:
    """Return ReMe's persisted memories matching ``query`` as strings.

    Search is workspace-wide (it spans every session), so this is the
    natural read-path for inspecting what got written back between
    turns — analogous to listing a vector store's facts.
    """
    # pylint: disable-next=protected-access
    return await mw._search(query, limit=20)  # demo only — peek at state


async def _reindex(mw: ReMeMiddleware) -> None:
    """Synchronously rebuild ReMe's search index from disk.

    ``auto_memory`` write-back returns as soon as the daily card is
    written to the workspace, but that card only becomes *searchable*
    once ReMe indexes it. ReMe normally does this in a background watch
    loop; the demo forces a synchronous ``reindex`` instead so the very
    next read deterministically sees the freshly written memory (no
    sleeping / polling for the background loop to catch up).
    """
    # pylint: disable-next=protected-access
    await mw._run_job("reindex")  # demo only — make writes searchable now


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

    1. ``[reme → context]`` — the retrieved memory note. The middleware
       searches ReMe in the background and injects the note on a reasoning
       step once the search finishes, so we poll ``state.context`` on every
       event and surface it the first time it appears (best-effort; a
       single-shot reply may finish before it lands).
    2. ``ToolCallStartEvent`` / ``ToolResultEndEvent`` bracket each
       ``memory_search`` invocation the agent makes on its own
       (agent path).
    3. ``TextBlockDeltaEvent`` carries the assistant's streamed reply
       text — concatenating every delta yields the final message
       content.

    Each printed line is tagged with the ReMe control path that
    produced it (``static`` vs ``agent``) so the demo stays readable
    in any of the three modes.
    """
    pending_args: dict[str, str] = {}
    pending_names: dict[str, str] = {}
    pending_results: dict[str, str] = {}
    text_parts: list[str] = []
    memory_announced = False

    def _announce_memory() -> None:
        """Print the static-path memory note as soon as it lands in context.

        The middleware retrieves in the background and injects the note in
        ``on_reasoning`` once the search finishes, so we poll the context on
        every event and surface it the first time it appears (best-effort:
        a single-shot reply may never inject one)."""
        nonlocal memory_announced
        if memory_announced:
            return
        injected = _injected_memory_bullets(agent)
        if not injected:
            return
        print(
            f"[reme → context (static)] retrieved "
            f"{len(injected)} memory note(s):",
        )
        for b in injected:
            print(f"  ← {b}")
        memory_announced = True

    async for ev in agent.reply_stream(inputs=user_msg):
        _announce_memory()
        if isinstance(ev, ToolCallStartEvent):
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
            print(f"[tool call (agent)] {name}({args})  → state={ev.state}")
            for line in result.splitlines() or [""]:
                if line:
                    print(f"  → {line}")
        elif isinstance(ev, TextBlockDeltaEvent):
            text_parts.append(ev.delta)

    # The note may only land on the final reasoning step, after the last
    # event we react to above — poll once more before finishing the turn.
    _announce_memory()

    return "".join(text_parts)


def _build_agent(
    chat_model: DashScopeChatModel,
    mw: ReMeMiddleware,
    session_id: str,
    tools: list,
) -> Agent:
    """Construct a data-analysis agent pinned to ``session_id``.

    ReMe scopes write-back by ``session_id`` (read from
    ``agent.state.session_id`` at hook time), so a distinct id per
    session keeps each conversation's cards apart; search still spans
    the whole workspace, which is what bridges the two sessions.
    """
    return Agent(
        name="datascope_assistant",
        system_prompt=(
            "You are a helpful data-analysis assistant. Be concise. "
            "When the request may depend on a durable fact from a past "
            "session (a preference, a name, a prior decision), use the "
            "memory_search tool. Saving memory is automatic."
        ),
        model=chat_model,
        toolkit=Toolkit(tools=tools),
        middlewares=[mw],
        state=AgentState(session_id=session_id),
    )


async def main() -> None:
    """Drive two cross-session agent turns and print middleware effects."""
    api_key = os.environ["DASHSCOPE_API_KEY"]

    # Start from a clean workspace so the demo is reproducible. The
    # default temp dir is already fresh (mkdtemp just created it empty),
    # so there is nothing to wipe. A user-provided REME_WORKSPACE_DIR is
    # reused as-is and only reset when REME_DEMO_RESET=1 is set — we must
    # never silently delete a directory the user named.
    if _ENV_WORKSPACE and os.environ.get("REME_DEMO_RESET") == "1":
        print("=== resetting ReMe workspace (REME_DEMO_RESET=1) ===")
        print(f"  rm -rf {WORKSPACE_DIR}")
        shutil.rmtree(WORKSPACE_DIR, ignore_errors=True)
    elif _ENV_WORKSPACE:
        print(f"=== reusing ReMe workspace {WORKSPACE_DIR} ===")
        print("  (set REME_DEMO_RESET=1 to wipe it before this run)")
    else:
        print(f"=== fresh ReMe workspace {WORKSPACE_DIR} ===")

    chat_model = DashScopeChatModel(
        credential=DashScopeCredential(api_key=api_key),
        model="qwen3.7-max",
        stream=True,
    )
    embedding_model = DashScopeEmbeddingModel(
        credential=DashScopeCredential(api_key=api_key),
        model="text-embedding-v4",
        dimensions=1024,  # matches ReMe's default embedding dimension
    )

    # One middleware, shared across both sessions. ReMe is embedded
    # in-process and the middleware owns its lifecycle — it builds the
    # reme.ReMe app lazily on first use and closes it on mw.close(). The
    # chat + embedding models are fixed here (they drive the embedded
    # app's single LLM for auto_memory write-back and its vector search).
    # Per-conversation state (session_id) is read live from each agent,
    # never stored on the middleware — so sharing one instance across
    # agents/sessions is safe.
    #
    # ReMe's bundled ``default`` config searches keyword-only (its file
    # store ships with ``embedding_store: ""``). For a long-term *memory*
    # demo we want semantic recall — "plot monthly sales" should find a
    # "prefers matplotlib / dark mode" card even without shared keywords —
    # so we pass an ``embedding_model``, which the middleware uses to turn
    # ReMe's vector store on automatically when it builds the app.
    mw = ReMeMiddleware(
        workspace_dir=WORKSPACE_DIR,
        parameters=ReMeMiddleware.Parameters(
            chat_model=chat_model,
            embedding_model=embedding_model,
            mode=MODE,
            top_k=5,
        ),
    )

    try:
        tools = await mw.list_tools()

        # =============================================================
        # SESSION 1 — state a durable preference; ReMe writes it back.
        # =============================================================
        print(f"\n=== SESSION 1 (mode={MODE!r}) ===")
        user_msg_1 = (
            "Hi! For any chart, please default to dark mode and use "
            "matplotlib. Also I'm based in Hangzhou."
        )
        print(f"\n[user] {user_msg_1}\n")

        agent = _build_agent(chat_model, mw, "session-1", tools)
        reply_text = await _run_turn(agent, UserMsg("alice", user_msg_1))
        print(f"\n[assistant] {reply_text}")

        # Make session 1's write-back searchable before session 2 reads
        # (see _reindex — ReMe's background indexer is not relied upon).
        print("\n(indexing the new memory card...)")
        await _reindex(mw)
        persisted = await _memories_in_reme(mw, "chart preferences location")
        print("[context → reme (auto)] workspace now holds:")
        for m in persisted:
            print(f"  + {m}")

        # =============================================================
        # SESSION 2 — fresh agent, empty chat context. ReMe bridges.
        # =============================================================
        print(
            f"\n=== SESSION 2 (fresh agent, reme bridges; mode={MODE!r}) ===",
        )
        user_msg_2 = (
            "Plot me a bar chart of monthly sales — pick reasonable "
            "defaults for theme and library."
        )
        print(f"\n[user] {user_msg_2}\n")

        agent = _build_agent(chat_model, mw, "session-2", tools)
        reply_text = await _run_turn(agent, UserMsg("alice", user_msg_2))
        print(f"\n[assistant] {reply_text}")
    finally:
        # The middleware owns the embedded ReMe app it built, so a single
        # close() tears it down — ReMe's background jobs / thread pool
        # shut down cleanly. (AgentScope doesn't manage middleware
        # lifecycle, so this must be explicit.)
        await mw.close()


if __name__ == "__main__":
    asyncio.run(main())
