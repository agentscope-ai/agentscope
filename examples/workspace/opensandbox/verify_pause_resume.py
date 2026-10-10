# -*- coding: utf-8 -*-
"""Run a DeepSeek agent, pause its FastSandbox workspace, and resume it.

Set OPENSANDBOX_DOMAIN, OPENSANDBOX_API_KEY, OPENSANDBOX_TEMPLATE_ID,
and DEEPSEEK_API_KEY. The template must support Alpine or Debian/Ubuntu
package-manager bootstrap and have sufficient space for workspace dependencies.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import time
import uuid

from opensandbox import SandboxManager

from agentscope.agent import Agent
from agentscope.credential import DeepSeekCredential
from agentscope.message import UserMsg
from agentscope.model import DeepSeekChatModel
from agentscope.permission import PermissionMode
from agentscope.tool import Toolkit
from agentscope.workspace import OpenSandboxWorkspace


def required(name: str) -> str:
    """Read config without displaying credentials."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Set {name} before running this example")
    return value


async def main() -> None:
    """Verify agent tools and filesystem, memory, and process persistence."""
    required("OPENSANDBOX_TEMPLATE_ID")
    workspace_id = f"agentscope-fastsandbox-{uuid.uuid4().hex[:12]}"
    options = {
        "workspace_id": workspace_id,
        "domain": required("OPENSANDBOX_DOMAIN"),
        "api_key": required("OPENSANDBOX_API_KEY"),
        "protocol": os.getenv("OPENSANDBOX_PROTOCOL", "http"),
        "request_timeout_seconds": 120,
        "timeout_seconds": 1800,
    }
    workspace = OpenSandboxWorkspace(**options)
    manager = await SandboxManager.create(workspace._connection_config())
    report = {
        "workspace_id": workspace_id,
        "template_id": required("OPENSANDBOX_TEMPLATE_ID"),
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        "status": "RUNNING",
    }
    sandbox_id = None
    model = DeepSeekChatModel(
        credential=DeepSeekCredential(
            api_key=required("DEEPSEEK_API_KEY"),
            base_url=os.getenv(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
            ),
        ),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        stream=False,
        max_retries=1,
        client_kwargs={"timeout": 60},
    )

    async def run_agent(task: str) -> None:
        agent = Agent(
            name="FastSandboxWorkspaceTester",
            system_prompt=(
                "Complete the task using the workspace tools.\n"
                + await workspace.get_instructions()
            ),
            model=model,
            toolkit=Toolkit(tools=await workspace.list_tools()),
        )
        agent.state.permission_context.mode = PermissionMode.BYPASS
        response = await agent.reply(UserMsg("user", task))
        print(response.get_text_content(), flush=True)

    async def shell(command: str) -> str:
        result = await workspace.get_backend().exec_shell(
            ["sh", "-c", command]
        )
        if not result.ok():
            raise AssertionError(result.stderr.decode(errors="replace"))
        return result.stdout.decode().strip()

    try:
        started = time.perf_counter()
        print("Initializing FastSandbox workspace ...", flush=True)
        await workspace.initialize()
        sandbox_id = workspace.sandbox_id
        report["sandbox_id"] = sandbox_id
        report["initialize_seconds"] = time.perf_counter() - started
        print(f"Workspace ready: {sandbox_id}", flush=True)
        await run_agent(
            "Under /workspace/demo, use Bash to create the directory; "
            "use Write to create hello.py that prints 'hello fastsandbox'; "
            "use Read to verify the file; use Bash to run it with Python "
            "and redirect output to result.txt. Use Glob to find both "
            "files and Grep to find 'hello fastsandbox' in hello.py.",
        )
        assert (
            await workspace.get_backend().read_file(
                "/workspace/demo/result.txt",
            )
            == b"hello fastsandbox\n"
        )
        # tmpfs and the original process identity demonstrate memory restore,
        # in addition to the agent's files surviving on disk.
        await shell(
            "printf memory-preserved > /dev/shm/agentscope-marker; "
            "nohup sleep 1800 >/tmp/agentscope-bg.log 2>&1 & "
            "echo $! > /tmp/agentscope-bg.pid",
        )
        identity_command = (
            "cat /proc/sys/kernel/random/boot_id; "
            "pid=$(cat /tmp/agentscope-bg.pid); "
            "kill -0 $pid && awk '{print $1, $22}' /proc/$pid/stat"
        )
        before = await shell(identity_command)
        started = time.perf_counter()
        print("Pausing workspace until checkpoint is durable ...", flush=True)
        await workspace.close()
        paused = await manager.get_sandbox_info(sandbox_id)
        assert paused.status.state.lower() == "paused", paused.status.state
        report["pause_seconds"] = time.perf_counter() - started

        # Construct a new handle with the same ID to exercise metadata lookup.
        workspace = OpenSandboxWorkspace(**options)
        started = time.perf_counter()
        print("Reattaching and resuming the same workspace ...", flush=True)
        await workspace.initialize()
        report["resume_initialize_seconds"] = time.perf_counter() - started
        assert workspace.sandbox_id == sandbox_id
        assert await shell(identity_command) == before
        assert (
            await shell("cat /dev/shm/agentscope-marker") == "memory-preserved"
        )
        assert (
            await workspace.get_backend().read_file(
                "/workspace/demo/result.txt",
            )
            == b"hello fastsandbox\n"
        )
        await run_agent(
            "Read /workspace/demo/hello.py and use Bash to run it with "
            "Python again. Then use Write to create "
            "/workspace/demo/resumed.txt "
            "containing exactly 'workspace resumed'.",
        )
        assert (
            await workspace.get_backend().read_file(
                "/workspace/demo/resumed.txt",
            )
        ).strip() == b"workspace resumed"
        report.update(
            status="PASS",
            same_sandbox=True,
            filesystem_preserved=True,
            tmpfs_preserved=True,
            boot_and_process_identity_preserved=True,
            agent_after_resume=True,
        )
    except Exception as exc:
        report.update(status="FAIL", error_type=type(exc).__name__)
        raise
    finally:
        # The reusable workspace API pauses on close. This example explicitly
        # kills its own test sandbox so repeated runs leave no checkpoints.
        cleanup_id = sandbox_id or workspace.sandbox_id
        if cleanup_id:
            await manager.kill_sandbox(cleanup_id)
        if workspace._gateway:
            await workspace._gateway.aclose()
        if workspace._sandbox:
            await workspace._sandbox.close()
        await manager.close()
        path = os.getenv("REPORT_PATH")
        if path:
            Path(path).write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
