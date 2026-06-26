# -*- coding: utf-8 -*-
"""FileSystemMemory mode demo.

This demo shows how the same Agent task behaves with the three
FileSystemMemory control modes:

- ``static_control``: the middleware periodically extracts memory after a
  completed turn.
- ``agent_control``: the Agent decides when to call ``memory_manage``.
- ``both``: both write paths are enabled.

FileSystemMemory does not need a dedicated chat model. This example chooses
DashScope only as the model provider for the demo Agent; static extraction
reuses that same ``Agent.model`` automatically.

During each turn the script prints:

- the expected behavior for the selected mode;
- every agent-initiated ``memory_read`` / ``memory_search`` /
  ``memory_manage`` tool call;
- the streamed assistant response; and
- persisted Markdown memory files after each mode finishes.

Set ``MODES`` to a smaller tuple when you want to run only one mode and reduce
model calls. Set ``RESET_DEMO_WORKSPACE`` to ``False`` to preserve memory
across separate executions of this script.

Requires:
    pip install agentscope
    export DASHSCOPE_API_KEY=sk-...
"""
import asyncio
import os
import shutil
from pathlib import Path
from typing import Literal

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
from agentscope.middleware import FileSystemMemoryMiddleware
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit
from agentscope.workspace import LocalWorkspace


MemoryMode = Literal["static_control", "agent_control", "both"]


MODE_EXPECTATIONS: dict[MemoryMode, tuple[str, str, str]] = {
    "static_control": (
        "Turn 1: the Agent should answer normally. It cannot call "
        "`memory_manage`, because only read/search tools are exposed.",
        "Turn 2: the fresh Agent should still recall Hangzhou, concise "
        "Chinese answers, and the FileSystemMemory demo todo.",
        "Reason: with extraction_interval=1, static extraction runs after "
        "the first completed reply and writes durable facts into Markdown "
        "memory files.",
    ),
    "agent_control": (
        "Turn 1: the Agent is expected to call `memory_read` first, then "
        "`memory_manage` to store the durable facts.",
        "Turn 2: the fresh Agent should recall the stored facts if the first "
        "turn's tool-managed writes succeeded.",
        "Reason: there is no periodic extraction in agent_control mode. "
        "Persistence depends on the Agent choosing to use `memory_manage`.",
    ),
    "both": (
        "Turn 1: the Agent may write immediately with `memory_manage`; "
        "static extraction also runs after the reply.",
        "Turn 2: the fresh Agent should recall the stored facts, usually with "
        "the strongest persistence behavior of the three modes.",
        "Reason: both direct tool management and post-turn static extraction "
        "are enabled. Duplicate additions are ignored by normalized matching.",
    ),
}


# Run the same memory-management conversation with each control mode.
MODES: tuple[MemoryMode, ...] = (
    "static_control",
    "agent_control",
    "both",
)

# True gives every run deterministic empty memory files. Set this to False to
# observe persistence across process restarts.
RESET_DEMO_WORKSPACE = False

DEMO_ROOT = Path(__file__).with_name("demo_workspace")


async def _run_turn(agent: Agent, text: str) -> str:
    """Run one streamed turn and print memory-tool activity."""
    tool_names: dict[str, str] = {}
    tool_args: dict[str, str] = {}
    tool_results: dict[str, str] = {}
    reply_parts: list[str] = []

    async for event in agent.reply_stream(UserMsg("alice", text)):
        if isinstance(event, ToolCallStartEvent):
            tool_names[event.tool_call_id] = event.tool_call_name
            tool_args[event.tool_call_id] = ""
            tool_results[event.tool_call_id] = ""
        elif isinstance(event, ToolCallDeltaEvent):
            tool_args[event.tool_call_id] += event.delta
        elif isinstance(event, ToolResultTextDeltaEvent):
            tool_results[event.tool_call_id] += event.delta
        elif isinstance(event, ToolResultEndEvent):
            tool_id = event.tool_call_id
            name = tool_names.pop(tool_id, "<unknown>")
            arguments = tool_args.pop(tool_id, "")
            result = tool_results.pop(tool_id, "")
            print(f"[memory tool] {name}({arguments}) -> {event.state}")
            for line in result.splitlines():
                print(f"  {line}")
        elif isinstance(event, TextBlockDeltaEvent):
            reply_parts.append(event.delta)

    return "".join(reply_parts)


def _print_memory_files(workspace_root: Path, label: str) -> None:
    """Print one workspace's human-readable Markdown memory files."""
    memory_root = workspace_root / "Memory"
    print(f"\n[FileSystemMemory] {label}: {workspace_root}")
    if not memory_root.exists():
        print("  (the Memory directory has not been created yet)")
        return
    for path in sorted(memory_root.rglob("*.md")):
        relative = path.relative_to(workspace_root)
        print(f"\n--- {relative} ---")
        print(path.read_text(encoding="utf-8").strip())


def _print_mode_expectations(mode: MemoryMode) -> None:
    """Print expected model behavior and the reason for one mode."""
    turn_one, turn_two, reason = MODE_EXPECTATIONS[mode]
    print("\n[expected]")
    print(f"  {turn_one}")
    print(f"  {turn_two}")
    print(f"  {reason}\n")


async def _build_agent(
    *,
    model: DashScopeChatModel,
    workspace: LocalWorkspace,
    memory: FileSystemMemoryMiddleware,
) -> Agent:
    """Build a fresh Agent using one workspace and one memory middleware."""
    return Agent(
        name="workspace_assistant",
        system_prompt=(
            "You are a concise project assistant. Use the available memory "
            "tools when they are relevant."
        ),
        model=model,
        # Memory tools are registered explicitly so the demo makes tool access
        # visible. static_control exposes read/search only; agent_control and
        # both also expose memory_manage.
        toolkit=Toolkit(tools=await memory.list_tools()),
        middlewares=[memory],
        offloader=workspace,
    )


async def _run_mode(model: DashScopeChatModel, mode: MemoryMode) -> None:
    """Run the same two-turn memory task with one FileSystemMemory mode."""
    workspace_root = DEMO_ROOT / mode
    workspace = LocalWorkspace(workdir=str(workspace_root))
    await workspace.initialize()
    memory = FileSystemMemoryMiddleware(mode=mode, extraction_interval=1)

    print(f"\n=== MODE: {mode} ===")
    _print_mode_expectations(mode)
    try:
        agent = await _build_agent(
            model=model,
            workspace=workspace,
            memory=memory,
        )
        first_message = (
            "Please remember these durable facts for this workspace: I live "
            "in Hangzhou, I prefer concise Chinese answers, and today's "
            "project todo is to finish the FileSystemMemory demo. If you can "
            "manage memory directly, read the current memory first and then "
            "store these facts in suitable sections."
        )
        print(f"[user] {first_message}\n")
        first_reply = await _run_turn(agent, first_message)
        print(f"\n[assistant] {first_reply}")

        # A fresh Agent proves that memory was persisted in the workspace, not
        # held only in the previous AgentState.
        agent = await _build_agent(
            model=model,
            workspace=workspace,
            memory=memory,
        )
        second_message = (
            "What do you remember about my location, answer style, and "
            "today's project todo? Search or read memory if needed."
        )
        print(f"\n[user] {second_message}\n")
        second_reply = await _run_turn(agent, second_message)
        print(f"\n[assistant] {second_reply}")

        _print_memory_files(workspace_root, mode)
    finally:
        await workspace.close()


async def main() -> None:
    """Run the FileSystemMemory mode demo."""
    api_key = os.environ["DASHSCOPE_API_KEY"]

    if RESET_DEMO_WORKSPACE:
        print(f"=== resetting demo workspace: {DEMO_ROOT} ===")
        shutil.rmtree(DEMO_ROOT, ignore_errors=True)
    else:
        print(f"=== reusing demo workspace: {DEMO_ROOT} ===")

    # DashScope is used only to give the demo Agent a concrete chat model.
    # FileSystemMemory does not receive or construct a second model: static
    # extraction calls the model already attached to the active Agent.
    model = DashScopeChatModel(
        credential=DashScopeCredential(api_key=api_key),
        model="qwen3.7-max",
        stream=False,
    )

    for mode in MODES:
        await _run_mode(model, mode)


if __name__ == "__main__":
    asyncio.run(main())
