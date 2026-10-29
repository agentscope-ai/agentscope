# -*- coding: utf-8 -*-
"""Send one prompt to the example ACP agent and print its streamed reply."""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, spawn_agent_process, text_block
from acp.schema import (
    AllowedOutcome,
    ClientCapabilities,
    DeniedOutcome,
    PermissionOption,
    RequestPermissionResponse,
    ToolCallUpdate,
)


class TerminalClient:
    """Display agent text and ask before granting tool permissions."""

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **_: Any,
    ) -> None:
        """Print text chunks as they arrive from the agent."""
        del session_id
        if (
            update.session_update == "agent_message_chunk"
            and update.content.type == "text"
        ):
            print(update.content.text, end="", flush=True)

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: ToolCallUpdate,
        **_: Any,
    ) -> RequestPermissionResponse:
        """Grant only one execution, and treat EOF as cancellation."""
        del session_id
        try:
            answer = await asyncio.to_thread(
                input,
                f"\nAllow {tool_call.title}: {tool_call.raw_input}? [y/N] ",
            )
        except EOFError:
            return RequestPermissionResponse(
                outcome=DeniedOutcome(outcome="cancelled"),
            )
        kind = "allow_once" if answer.strip().lower() == "y" else "reject_once"
        selected = next((opt for opt in options if opt.kind == kind), None)
        return RequestPermissionResponse(
            outcome=(
                AllowedOutcome(
                    outcome="selected",
                    option_id=selected.option_id,
                )
                if selected is not None
                else DeniedOutcome(outcome="cancelled")
            ),
        )


async def run(prompt: str, cwd: Path) -> None:
    """Start the agent, send one prompt, and close the session and process."""
    async with spawn_agent_process(
        TerminalClient(),
        sys.executable,
        str(Path(__file__).with_name("agent.py").resolve()),
        cwd=cwd,
        env=dict(os.environ),
        transport_kwargs={"stderr": None},
        use_unstable_protocol=True,
    ) as (connection, _process):
        await connection.initialize(
            protocol_version=PROTOCOL_VERSION,
            capabilities=ClientCapabilities(),
        )
        session = await connection.new_session(cwd=str(cwd))
        try:
            response = await connection.prompt(
                session_id=session.session_id,
                prompt=[text_block(prompt)],
            )
            print(f"\n[stop_reason: {response.stop_reason}]")
        finally:
            await connection.close_session(session_id=session.session_id)


def main() -> None:
    """Use the current directory unless a project path is supplied."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", default="hello")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    args = parser.parse_args()
    cwd = args.cwd.expanduser().resolve()
    if not cwd.is_dir():
        parser.error(f"Project directory does not exist: {cwd}")
    if not os.environ.get("DASHSCOPE_API_KEY"):
        parser.error("Set DASHSCOPE_API_KEY before starting the client")
    asyncio.run(run(args.prompt, cwd))


if __name__ == "__main__":
    main()
