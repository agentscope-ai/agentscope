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

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit

from agentscope.filesystem import DiskFileSystem
from agentscope.filesystem._service import FileDomainService
from dotenv import load_dotenv


def build_toolkit() -> Toolkit:
    root_dir = os.getenv("FS_ROOT_DIR")
    fs = DiskFileSystem(root_dir=root_dir) if root_dir else DiskFileSystem()
    handle = fs.create_handle(
        [
            {"prefix": "/userinput/", "ops": {"list", "file", "read_file", "read_binary", "read_re"}},
            {
                "prefix": "/workspace/",
                "ops": {"list", "file", "read_file", "read_binary", "read_re", "write", "delete"},
            },
        ]
    )
    svc = FileDomainService(handle)
    tk = Toolkit()
    for func, svc2 in fs.get_tools(svc):
        tk.register_tool_function(func, preset_kwargs={"service": svc2})
    return tk


SYS_PROMPT = (
    "你是一个严格遵循工具调用的文件系统代理。\n"
    "- 只能使用提供的工具，禁止绕过工具直接访问文件。\n"
    "- 路径规范：所有 path 必须是逻辑绝对路径，以 /userinput/ 或 /workspace/ 开头；严格区分大小写；"
    "禁止 '..'、'*'、'?'、'\\\\'、'//'；结尾空格视为路径一部分。\n"
    "- 域与权限：/userinput/ 只读；/workspace/ 允许读/写/删；/internal/ 不在可见范围。越权操作需明确报错。\n"
    "- 输出要求：不得泄露任何 OS 物理路径，仅可在回复与工具结果中使用逻辑路径。\n"
    "- 错误处理：准确回显 AccessDenied/InvalidPath/NotFound，并在更正参数后重试。\n"
    "- 大文件读取：必须使用 start_line/read_lines 分片读取，禁止一次性全文读取。\n"
    "- 工具用法：\n"
    "  * list_allowed_directories()：返回可用根 ['/userinput/','/workspace/']。\n"
    "  * list_directory(path) / list_directory_with_sizes(path, sortBy?='name')：列出直接子项；"
    "with_sizes 展示每项 size 与总大小 total_size；sortBy 支持 name|size。\n"
    "  * search_files(path, pattern, excludePatterns?)：在完整逻辑路径上进行 glob/子串匹配；excludePatterns 同样作用于完整逻辑路径；按路径排序返回。\n"
    "  * get_file_info(path)：返回 {path,size,updated_at}（逻辑信息）。\n"
    "  * read_text_file(path, start_line?=1, read_lines?)：UTF-8 文本读取；从第 1 行起按行切片；不做截断/脱敏。\n"
    "  * read_multiple_files(paths)：逐项容错；为每个 path 返回 ok/error 与内容或原因。\n"
    "  * write_file(path, content)：自动创建父目录并覆盖写入；仅允许 /workspace/。\n"
    "  * edit_file(path, edits[{oldText,newText}])：顺序子串替换后覆盖写入；无 dry-run/diff；仅允许 /workspace/。\n"
    "  * delete_file(path)：删除逻辑文件；/workspace/ 允许，/userinput/ 拒绝，/internal/ 默认拒绝。\n"
    "- 你应依据上述约束，自主选择合适的工具与调用顺序完成用户目标。\n"
    "- 预想过程（示例性、非强制，不作为验收真理）：\n"
    "  0) 若存在历史产物：get_file_info('/workspace/summary.md') 成功则先 delete_file('/workspace/summary.md') 再写入；\n"
    "  1) list_allowed_directories → list_directory('/userinput/')（必要时 list_directory_with_sizes）；\n"
    "  2) search_files('/userinput/', pattern=关键词, excludePatterns=[] )；\n"
    "  3) get_file_info（可选，用于排序/筛选）；\n"
    "  4) read_text_file（对命中文件分片读取，必要时多次；或小集用 read_multiple_files）；\n"
    "  5) 生成摘要并 write_file('/workspace/summary.md', 内容)；\n"
    "  6) 最后必须列出 /workspace/ 目录（list_directory 或 list_directory_with_sizes）并回显结果。"
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
        generate_kwargs={"tool_choice": "auto", "max_tokens": 1024, "temperature": 0.2},
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
            f"请完成与“{topic}”相关的资料整理与总结。请先了解可用内容，"
            "对大文件需分片读取；如存在 /workspace/summary.md 请先 get_file_info 成功后 delete_file 再写入；"
            "完成后必须调用 list_directory('/workspace/') 或 list_directory_with_sizes('/workspace/', sortBy='name') 并回显结果；"
            "随后（如存在）请删除 /workspace/execution_code.md，且在删除前与删除后各列出一次 /workspace/ 目录，用于确认。"
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
