# -*- coding: utf-8 -*-
"""Standalone file-based long-term memory middleware demo.

The demo creates two independent :class:`Agent` instances backed by the same
:class:`LocalWorkspace`. Session 1 contributes user, project, and daily
memory; Session 2 starts with fresh conversation state and demonstrates that
the workspace files bridge the two sessions.

File LTM does not need a dedicated chat model. This example chooses DashScope
only as the model provider for the demo Agents; static extraction reuses that
same ``Agent.model`` automatically.

During each turn the script prints:

- every agent-initiated ``memory_read`` / ``memory_search`` /
  ``memory_manage`` tool call;
- the streamed assistant response; and
- every persisted Markdown memory file after the turn.

Set ``MODE`` to ``"static"``, ``"auto"``, or ``"both"`` to compare the
three control patterns. Set ``RESET_DEMO_WORKSPACE`` to ``False`` to preserve
memory across separate executions of this script.

Requires:
    pip install agentscope
    export DASHSCOPE_API_KEY=sk-...
"""
import asyncio
import os
import shutil
from pathlib import Path

from agentscope.agent import Agent
from agentscope.credential import DashScopeCredential
from agentscope.event import (
    TextBlockDeltaEvent,
    ToolCallDeltaEvent,
    ToolCallStartEvent,
    ToolResultEndEvent,
    ToolResultTextDeltaEvent,
)
from agentscope.message import UserMsg
from agentscope.middleware import FileLongTermMemoryMiddleware
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit
from agentscope.workspace import LocalWorkspace


# ``static`` performs periodic model-driven extraction; ``auto`` lets the
# agent write through memory_manage; ``both`` enables both paths. Read and
# search tools remain available in every mode.
MODE = "auto"

# True gives every run a deterministic empty workspace. Set this to False to
# demonstrate persistence across process restarts.
RESET_DEMO_WORKSPACE = False

# Memory is stored next to this script so it is easy to inspect while running
# the example. A production application normally chooses its own workspace
# root or obtains one from a WorkspaceManager.
DEMO_WORKSPACE = Path(__file__).with_name("demo_workspace")


async def _run_turn(agent: Agent, text: str) -> str:
    """Run one streamed turn and expose memory-tool activity.

    AgentScope emits a start event, zero or more argument/result deltas, and a
    final result event for each tool call. The dictionaries below accumulate
    those deltas by ``tool_call_id`` so the demo can print one readable record
    when the call finishes. Assistant text is accumulated independently and
    returned to the caller.
    """
    tool_names: dict[str, str] = {}
    tool_args: dict[str, str] = {}
    tool_results: dict[str, str] = {}
    reply_parts: list[str] = []

    async for event in agent.reply_stream(UserMsg("alice", text)):
        if isinstance(event, ToolCallStartEvent):
            # Initialize one accumulator per tool call. Multiple calls may be
            # interleaved in the event stream, so tool_call_id is the key.
            tool_names[event.tool_call_id] = event.tool_call_name
            tool_args[event.tool_call_id] = ""
            tool_results[event.tool_call_id] = ""
        elif isinstance(event, ToolCallDeltaEvent):
            # Tool arguments arrive as serialized text deltas.
            tool_args[event.tool_call_id] += event.delta
        elif isinstance(event, ToolResultTextDeltaEvent):
            # Tool result text may also arrive in more than one chunk.
            tool_results[event.tool_call_id] += event.delta
        elif isinstance(event, ToolResultEndEvent):
            # The end event supplies the final success/error state. Printing
            # here keeps arguments, result text, and state together.
            tool_id = event.tool_call_id
            name = tool_names.pop(tool_id, "<unknown>")
            arguments = tool_args.pop(tool_id, "")
            result = tool_results.pop(tool_id, "")
            print(f"[memory tool] {name}({arguments}) -> {event.state}")
            for line in result.splitlines():
                print(f"  {line}")
        elif isinstance(event, TextBlockDeltaEvent):
            # Concatenating all assistant deltas reconstructs the final text.
            reply_parts.append(event.delta)

    return "".join(reply_parts)


def _print_memory_files() -> None:
    """Print all human-readable Markdown memory files.

    ``.ltm.meta.json`` is intentionally omitted: it is middleware bookkeeping,
    while this function focuses on the memory that developers may inspect or
    edit directly.
    """
    memory_root = DEMO_WORKSPACE / "Memory"
    print("\n[file LTM] persisted files:")
    if not memory_root.exists():
        print("  (the Memory directory has not been created yet)")
        return
    for path in sorted(memory_root.rglob("*.md")):
        relative = path.relative_to(DEMO_WORKSPACE)
        print(f"\n--- {relative} ---")
        print(path.read_text(encoding="utf-8").strip())


async def _build_agent(
    model: DashScopeChatModel,
    workspace: LocalWorkspace,
    memory: FileLongTermMemoryMiddleware,
) -> Agent:
    """Build a fresh Agent whose LTM is scoped to ``workspace``.

    Each call creates a new ``AgentState`` and therefore a new conversation
    session. The middleware instance and workspace are deliberately reused,
    which makes the Markdown files the only bridge between sessions.
    """
    return Agent(
        name="workspace_assistant",
        system_prompt=("You are a concise project assistant. "),
        model=model,
        # Middleware tools are not registered automatically. Explicit toolkit
        # composition makes it clear which capabilities the model can call.
        toolkit=Toolkit(tools=await memory.list_tools()),
        middlewares=[memory],
        # The explicit middleware workspace already determines LTM storage.
        # Supplying the same object as offloader also enables the workspace's
        # normal context/tool-result offloading behavior.
        offloader=workspace,
    )


async def main() -> None:
    """Run two fresh sessions and show workspace-scoped persistence."""
    api_key = os.environ["DASHSCOPE_API_KEY"]

    if RESET_DEMO_WORKSPACE:
        # Delete before LocalWorkspace.initialize() opens or creates anything.
        # This is demo-only behavior; production code should retain its
        # workspace directory to preserve long-term memory.
        print(f"=== resetting demo workspace: {DEMO_WORKSPACE} ===")
        shutil.rmtree(DEMO_WORKSPACE, ignore_errors=True)
    else:
        print(f"=== reusing demo workspace: {DEMO_WORKSPACE} ===")

    # DashScope is used only to give the demo Agent a concrete chat model. The
    # LTM middleware does not receive or construct a second model: static
    # extraction calls the model already attached to the active Agent.
    model = DashScopeChatModel(
        credential=DashScopeCredential(api_key=api_key),
        model="qwen3.7-max",
        stream=False,
    )
    # LocalWorkspace resolves the path, creates its standard workspace
    # layout, and exposes a LocalBackend through the common BackendBase API.
    workspace = LocalWorkspace(workdir=str(DEMO_WORKSPACE))
    await workspace.initialize()

    # extraction_interval=1 makes static extraction visible after every
    # completed turn. A production value such as 8 reduces model calls.
    # In auto mode this interval is unused because only memory_manage writes.
    # There is intentionally no model argument here: the middleware accesses
    # the active Agent's model only when static extraction is due.
    #
    # If the workspace parameter is not provided, the workspace will be
    # loaded from agent.offloader. If it is still empty, a LocalWorkspace
    # will be created locally.
    memory = FileLongTermMemoryMiddleware(
        workspace=workspace,
        mode=MODE,
        extraction_interval=1,
    )

    try:
        # SESSION 1 writes durable facts and today's episodic progress. In
        # static mode the middleware extracts after the assistant reply; in
        # auto/both mode the agent may also invoke memory_manage immediately.
        print(f"\n=== SESSION 1 (mode={MODE!r}) ===")
        message = (
            "I live in Hangzhou and prefer concise Chinese answers. For "
            "this project, we decided to keep focus on Agent Memory. "
            "Today's todo is to add an app-service demo."
        )
        print(f"[user] {message}\n")
        agent = await _build_agent(model, workspace, memory)
        reply = await _run_turn(agent, message)
        print(f"\n[assistant] {reply}")
        _print_memory_files()

        # SESSION 2 has no conversation history from Session 1. USER.md and
        # MEMORY.md are injected into its system prompt, while daily memory is
        # available through memory_search/memory_read.
        print("\n=== SESSION 2 (fresh Agent, same workspace) ===")
        message = (
            "What do you remember about me, our LTM decision, and today's "
            "unfinished work? Search memory if needed."
        )
        print(f"[user] {message}\n")
        agent = await _build_agent(model, workspace, memory)
        reply = await _run_turn(agent, message)
        print(f"\n[assistant] {reply}")
        _print_memory_files()
    finally:
        # The caller owns this explicit workspace, so it also owns lifecycle
        # cleanup. close() releases resources but does not delete local files.
        await workspace.close()


if __name__ == "__main__":
    asyncio.run(main())
