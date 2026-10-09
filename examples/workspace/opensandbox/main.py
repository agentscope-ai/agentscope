# -*- coding: utf-8 -*-
"""Use an AgentScope agent with a prepared FastSandbox workspace."""

import argparse
import asyncio
from datetime import timedelta
import os
import time
import uuid

from opensandbox import SandboxManager
from opensandbox.config.connection import ConnectionConfig

from agentscope.agent import Agent
from agentscope.credential import DeepSeekCredential
from agentscope.message import UserMsg
from agentscope.model import DeepSeekChatModel
from agentscope.permission import PermissionMode
from agentscope.tool import Toolkit
from agentscope.workspace import OpenSandboxWorkspace


def required(name: str) -> str:
    """Read a required setting without displaying its value."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Set {name} before running the demo")
    return value


async def ask(workspace: OpenSandboxWorkspace, task: str) -> None:
    """Bind the agent's tools to the current sandbox backend."""
    agent = Agent(
        name="FastSandboxDemo",
        system_prompt=(
            "Complete the task using the workspace tools.\n"
            + await workspace.get_instructions()
        ),
        model=DeepSeekChatModel(
            credential=DeepSeekCredential(
                api_key=required("DEEPSEEK_API_KEY"),
                base_url=os.getenv(
                    "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
                ),
            ),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            stream=False,
            client_kwargs={"timeout": 60},
        ),
        toolkit=Toolkit(tools=await workspace.list_tools()),
        offloader=workspace,
    )
    # This demo runs two fixed tasks without interactive approval prompts.
    agent.state.permission_context.mode = PermissionMode.BYPASS
    response = await agent.reply(UserMsg("user", task))
    print(response.get_text_content(), flush=True)


async def verify_paused(sandbox_id: str) -> None:
    """Confirm that close completed a durable pause, not just a local close."""
    config = ConnectionConfig(
        domain=required("OPENSANDBOX_DOMAIN"),
        api_key=required("OPENSANDBOX_API_KEY"),
        protocol=os.getenv("OPENSANDBOX_PROTOCOL", "http"),
        request_timeout=timedelta(seconds=120),
    )
    async with await SandboxManager.create(config) as manager:
        info = await manager.get_sandbox_info(sandbox_id)
        if info.status.state.lower() != "paused":
            raise RuntimeError(f"Pause incomplete: {info.status.state}")


async def run(workspace_id: str) -> None:
    """Create, pause, and reattach a workspace using only public APIs."""
    required("DEEPSEEK_API_KEY")
    options = {
        "workspace_id": workspace_id,
        "template_id": required("OPENSANDBOX_TEMPLATE_ID"),
        "domain": required("OPENSANDBOX_DOMAIN"),
        "api_key": required("OPENSANDBOX_API_KEY"),
        "protocol": os.getenv("OPENSANDBOX_PROTOCOL", "http"),
        "timeout_seconds": 1800,
        "request_timeout_seconds": 120,
    }
    print(f"workspace_id: {workspace_id}", flush=True)
    workspace = OpenSandboxWorkspace(**options)
    try:
        started = time.perf_counter()
        await workspace.initialize()
        sandbox_id = workspace.sandbox_id
        assert sandbox_id is not None
        print(
            f"Workspace ready in {time.perf_counter() - started:.3f}s; "
            f"sandbox_id: {sandbox_id}",
            flush=True,
        )
        await ask(
            workspace,
            "Use Write to create /workspace/data/hello.py containing "
            "print('hello fast-sandbox'). Use Bash to run it with python3 "
            "and save stdout to /workspace/data/result.txt.",
        )
        result = await workspace.get_backend().read_file(
            "/workspace/data/result.txt"
        )
        assert result.strip() == b"hello fast-sandbox", result

        print("Pausing and publishing the checkpoint ...", flush=True)
        await workspace.close()
        await verify_paused(sandbox_id)

        # A fresh Python object finds and resumes the same sandbox by its ID.
        # Conversation memory is separate; the second agent reads saved files.
        workspace = OpenSandboxWorkspace(**options)
        started = time.perf_counter()
        await workspace.initialize()
        assert workspace.sandbox_id == sandbox_id
        assert (
            await workspace.get_backend().read_file(
                "/workspace/data/result.txt"
            )
            == result
        )
        print(
            f"Same sandbox resumed in "
            f"{time.perf_counter() - started:.3f}s; file preserved.",
            flush=True,
        )
        await ask(
            workspace,
            "Read /workspace/data/result.txt and hello.py. Use Bash "
            "to run hello.py again, then report the saved and new output.",
        )
    finally:
        # Keep the user's workspace paused for later reattachment.
        cleanup_id = workspace.sandbox_id
        await workspace.close()
        if cleanup_id:
            await verify_paused(cleanup_id)
    print(
        "Done. The workspace is retained; reuse --workspace-id "
        f"{workspace_id} to reattach before the sandbox expires.",
        flush=True,
    )


def main() -> None:
    """Parse the stable workspace identifier and start the demo."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-id",
        default=f"fastsandbox-demo-{uuid.uuid4().hex[:12]}",
        help="Reuse this identifier to resume an existing workspace.",
    )
    args = parser.parse_args()
    asyncio.run(run(args.workspace_id))


if __name__ == "__main__":
    main()
