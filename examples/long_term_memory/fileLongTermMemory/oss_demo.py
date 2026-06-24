# -*- coding: utf-8 -*-
"""Standalone file-based long-term memory middleware demo.

The demo covers the main non-app sharing and isolation topologies: Agents can
share one middleware and workspace, share a middleware across isolated
workspaces, omit offloaders and share the middleware-owned LocalWorkspace, or
use separate middleware instances over the same files sequentially.

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
MODE = "both"

# Run all important non-app ownership combinations. Remove entries when you
# only want to inspect one case and reduce model calls.
SCENARIOS = (
    "shared_workspace",
    "isolated_workspaces",
    "implicit_workspace",
    "separate_middlewares_shared_workspace",
)

# True gives every run deterministic empty workspaces. Set this to False to
# demonstrate persistence across process restarts.
RESET_DEMO_WORKSPACE = False

# Every scenario stores files below this root so their isolation is visible.
DEMO_ROOT = Path(__file__).with_name("demo_workspace")
SHARED_WORKSPACE = DEMO_ROOT / "shared"
ISOLATED_WORKSPACE_A = DEMO_ROOT / "isolated_a"
ISOLATED_WORKSPACE_B = DEMO_ROOT / "isolated_b"
IMPLICIT_WORKSPACE = DEMO_ROOT / "implicit"
SEPARATE_MIDDLEWARE_WORKSPACE = DEMO_ROOT / "separate_middlewares"


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


def _print_memory_files(workspace_root: Path, label: str) -> None:
    """Print one workspace's human-readable Markdown memory files.

    ``.ltm.meta.json`` is intentionally omitted: it is middleware bookkeeping,
    while this function focuses on the memory that developers may inspect or
    edit directly.
    """
    memory_root = workspace_root / "Memory"
    print(f"\n[file LTM] {label}: {workspace_root}")
    if not memory_root.exists():
        print("  (the Memory directory has not been created yet)")
        return
    for path in sorted(memory_root.rglob("*.md")):
        relative = path.relative_to(workspace_root)
        print(f"\n--- {relative} ---")
        print(path.read_text(encoding="utf-8").strip())


async def _build_agent(
    model: DashScopeChatModel,
    workspace: LocalWorkspace | None,
    memory: FileLongTermMemoryMiddleware,
) -> Agent:
    """Build a fresh Agent for an explicit or middleware-owned workspace.

    Each call creates a new ``AgentState`` and therefore a new conversation
    session. Passing ``None`` leaves ``Agent.offloader`` empty, causing the
    middleware to create and reuse its configured LocalWorkspace.
    """
    return Agent(
        name="workspace_assistant",
        system_prompt=("You are a concise project assistant. "),
        model=model,
        # Middleware tools are not registered automatically. Explicit toolkit
        # composition makes it clear which capabilities the model can call.
        toolkit=Toolkit(tools=await memory.list_tools()),
        middlewares=[memory],
        # With an explicit workspace, LTM resolves it from Agent.offloader.
        # None exercises the middleware-owned LocalWorkspace fallback.
        offloader=workspace,
    )


async def _run_session(
    *,
    label: str,
    model: DashScopeChatModel,
    memory: FileLongTermMemoryMiddleware,
    workspace: LocalWorkspace | None,
    message: str,
    workspace_root: Path,
) -> None:
    """Run one fresh Agent session and print its resulting memory files."""
    print(f"\n--- {label} ---")
    print(f"[user] {message}\n")
    agent = await _build_agent(model, workspace, memory)
    reply = await _run_turn(agent, message)
    print(f"\n[assistant] {reply}")
    _print_memory_files(workspace_root, label)


async def _demo_shared_workspace(model: DashScopeChatModel) -> None:
    """Two Agents share both middleware and explicit workspace."""
    print("\n=== SAME MIDDLEWARE + SAME WORKSPACE ===")
    workspace = LocalWorkspace(workdir=str(SHARED_WORKSPACE))
    await workspace.initialize()
    memory = FileLongTermMemoryMiddleware(mode=MODE, extraction_interval=1)
    try:
        await _run_session(
            label="shared / session A",
            model=model,
            memory=memory,
            workspace=workspace,
            workspace_root=SHARED_WORKSPACE,
            message=(
                "I live in Hangzhou and prefer concise Chinese answers. "
                "Our project focuses on Agent Memory. Today's todo is to "
                "finish the OSS sharing demo. Remember this."
            ),
        )
        await _run_session(
            label="shared / session B",
            model=model,
            memory=memory,
            workspace=workspace,
            workspace_root=SHARED_WORKSPACE,
            message=(
                "What do you remember about me, this project, and today's "
                "todo? Search daily memory if needed."
            ),
        )
    finally:
        await workspace.close()


async def _demo_isolated_workspaces(model: DashScopeChatModel) -> None:
    """One middleware routes two Agents to independent workspace stores."""
    print("\n=== SAME MIDDLEWARE + DIFFERENT WORKSPACES ===")
    workspace_a = LocalWorkspace(workdir=str(ISOLATED_WORKSPACE_A))
    workspace_b = LocalWorkspace(workdir=str(ISOLATED_WORKSPACE_B))
    await workspace_a.initialize()
    await workspace_b.initialize()
    memory = FileLongTermMemoryMiddleware(mode=MODE, extraction_interval=1)
    try:
        await _run_session(
            label="isolated workspace A",
            model=model,
            memory=memory,
            workspace=workspace_a,
            workspace_root=ISOLATED_WORKSPACE_A,
            message="Workspace A uses codename Alpha. Remember it here.",
        )
        await _run_session(
            label="isolated workspace B",
            model=model,
            memory=memory,
            workspace=workspace_b,
            workspace_root=ISOLATED_WORKSPACE_B,
            message="Workspace B uses codename Beta. Remember it here.",
        )
    finally:
        await workspace_a.close()
        await workspace_b.close()


async def _demo_implicit_workspace(model: DashScopeChatModel) -> None:
    """Agents without offloaders share one middleware-owned workspace."""
    print("\n=== SAME MIDDLEWARE + NO OFFLOADER ===")
    memory = FileLongTermMemoryMiddleware(
        mode=MODE,
        extraction_interval=1,
        local_workspace_dir=str(IMPLICIT_WORKSPACE),
    )
    try:
        await _run_session(
            label="implicit / session A",
            model=model,
            memory=memory,
            workspace=None,
            workspace_root=IMPLICIT_WORKSPACE,
            message=(
                "Our fallback workspace decision is File LTM. Remember it."
            ),
        )
        await _run_session(
            label="implicit / session B",
            model=model,
            memory=memory,
            workspace=None,
            workspace_root=IMPLICIT_WORKSPACE,
            message="What decision was stored in our fallback workspace?",
        )
    finally:
        # This middleware owns the fallback LocalWorkspace and its lifecycle.
        await memory.close()


async def _demo_separate_middlewares_shared_workspace(
    model: DashScopeChatModel,
) -> None:
    """Show shared files but separate caches/locks; calls stay sequential."""
    print("\n=== DIFFERENT MIDDLEWARES + SAME WORKSPACE (SEQUENTIAL) ===")
    workspace = LocalWorkspace(workdir=str(SEPARATE_MIDDLEWARE_WORKSPACE))
    await workspace.initialize()
    memory_a = FileLongTermMemoryMiddleware(mode=MODE, extraction_interval=1)
    memory_b = FileLongTermMemoryMiddleware(mode=MODE, extraction_interval=1)
    try:
        await _run_session(
            label="separate middleware A",
            model=model,
            memory=memory_a,
            workspace=workspace,
            workspace_root=SEPARATE_MIDDLEWARE_WORKSPACE,
            message="Record that this shared filesystem uses codename Gamma.",
        )
        await _run_session(
            label="separate middleware B",
            model=model,
            memory=memory_b,
            workspace=workspace,
            workspace_root=SEPARATE_MIDDLEWARE_WORKSPACE,
            message="Read the shared files and tell me their codename.",
        )
        print(
            "\n[note] These middleware instances share files but not their "
            "async locks or caches; concurrent writes are not recommended.",
        )
    finally:
        await workspace.close()


async def main() -> None:
    """Run the selected non-app workspace sharing scenarios."""
    api_key = os.environ["DASHSCOPE_API_KEY"]

    if RESET_DEMO_WORKSPACE:
        print(f"=== resetting demo workspaces: {DEMO_ROOT} ===")
        shutil.rmtree(DEMO_ROOT, ignore_errors=True)
    else:
        print(f"=== reusing demo workspaces: {DEMO_ROOT} ===")

    # DashScope is used only to give the demo Agent a concrete chat model. The
    # LTM middleware does not receive or construct a second model: static
    # extraction calls the model already attached to the active Agent.
    model = DashScopeChatModel(
        credential=DashScopeCredential(api_key=api_key),
        model="qwen3.7-max",
        stream=False,
    )
    runners = {
        "shared_workspace": _demo_shared_workspace,
        "isolated_workspaces": _demo_isolated_workspaces,
        "implicit_workspace": _demo_implicit_workspace,
        "separate_middlewares_shared_workspace": (
            _demo_separate_middlewares_shared_workspace
        ),
    }
    for scenario in SCENARIOS:
        if scenario not in runners:
            raise ValueError(f"Unknown demo scenario: {scenario}")
        await runners[scenario](model)


if __name__ == "__main__":
    asyncio.run(main())
