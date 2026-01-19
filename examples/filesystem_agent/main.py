# -*- coding: utf-8 -*-
"""LLM-driven example using DiskFileSystem tools.

Environment variables:
- OPENAI_API_KEY (required)
- OPENAI_MODEL (optional, default: gpt-4o-mini)
- OPENAI_BASE_URL (optional, default: https://api.openai.com/v1)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
from pathlib import Path

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit

from agentscope.filesystem import DiskFileSystem
from agentscope.filesystem._service import FileDomainService
from dotenv import load_dotenv


def _seed_project_inputs(fs: DiskFileSystem) -> None:
    """Populate logical filesystem with deterministic fixtures for the demo."""
    repo_root = Path(__file__).resolve().parents[2]
    readme_src = repo_root / "README.md"

    userinput_dir = Path(
        getattr(
            fs,
            "_userinput_dir",
            os.path.join(repo_root, "userinput"),
        ),
    )
    workspace_dir = Path(
        getattr(
            fs,
            "_workspace_dir",
            os.path.join(repo_root, "workspace"),
        ),
    )

    userinput_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    if readme_src.exists():
        shutil.copy2(readme_src, userinput_dir / "project_readme.md")

    (workspace_dir / "summary.md").write_text(
        "Legacy summary from previous run.\n",
        encoding="utf-8",
    )

    (workspace_dir / "execution_script.sh").write_text(
        "#!/bin/bash\n# Legacy auto-generated script placeholder.\n"
        "echo 'legacy execution script'\n",
        encoding="utf-8",
    )


def build_toolkit() -> Toolkit:
    # Always use default run-root: ./output/{mmddHHMM}/ (auto-created)
    fs = DiskFileSystem()
    _seed_project_inputs(fs)
    handle = fs.create_handle(
        [
            {
                "prefix": "/userinput/",
                "ops": {
                    "list",
                    "file",
                    "read_file",
                    "read_binary",
                    "read_re",
                },
            },
            {
                "prefix": "/workspace/",
                "ops": {
                    "list",
                    "file",
                    "read_file",
                    "read_binary",
                    "read_re",
                    "write",
                    "delete",
                },
            },
        ],
    )
    svc = FileDomainService(handle)
    tk = Toolkit()
    for func, svc2 in fs.get_tools(svc):
        tk.register_tool_function(func, preset_kwargs={"service": svc2})
    return tk


SYS_PROMPT = (
    "环境约定：/userinput/ 为用户提供的只读目录；/workspace/ 为助手可写目录（仅在此创建/更新/删除）。\n"
    "所有 path 必须是逻辑绝对路径，禁止包含 ..、*、?、\\、//。\n"
    "严禁在 /userinput/ 写入或删除，严禁在 /workspace/ 之外写入。\n"
    "如路径或意图不明确，先向用户澄清后再执行。"
)


def build_agent(toolkit: Toolkit) -> ReActAgent:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required")
    model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    model = OpenAIChatModel(
        model_name=model_name,
        api_key=api_key,
        stream=False,
        client_args={"base_url": base_url},
        generate_kwargs={
            "tool_choice": "auto",
            "max_tokens": 1024,
            "temperature": 0.2,
        },
    )
    formatter = OpenAIChatFormatter()
    return ReActAgent(
        name="fs_agent",
        sys_prompt=SYS_PROMPT,
        model=model,
        formatter=formatter,
        toolkit=toolkit,
        parallel_tool_calls=False,
        max_iters=12,
    )


async def run(topic: str) -> None:
    # Auto-load .env using python-dotenv (does not override existing vars)
    load_dotenv(override=False)
    tk = build_toolkit()
    agent = build_agent(tk)
    user_msg = Msg(
        name="user",
        content=(
            f"请阅读提供的资料，重点提炼 README 中与“{topic}”及 Python 环境安装、依赖配置相关的内容；"
            "如材料较长，请按需分段理解。"
            "在工作目录写入新的总结前，请先处理任何遗留的 summary；完成后保存最新 summary 并说明当前文件结构。"
            "若发现先前生成的执行脚本等临时产物，请一并清理并在汇报中说明。"
        ),
        role="user",
    )
    await agent(user_msg)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", required=True, help="summarization topic")
    args = parser.parse_args()
    asyncio.run(run(args.topic))


if __name__ == "__main__":
    main()
