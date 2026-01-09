# -*- coding: utf-8 -*-
"""Standalone file download skill for the browser agent."""
# flake8: noqa: E501
# pylint: disable=W0212,W0107,too-many-lines,C0301

from __future__ import annotations
import os
import copy
from typing import Any
from pydantic import BaseModel


from agentscope.memory import InMemoryMemory
from agentscope.message import Msg, TextBlock
from agentscope.tool import ToolResponse
from agentscope.agent import ReActAgent


_CURRENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir),
)

with open(
    os.path.join(
        _CURRENT_DIR,
        "build_in_prompt/browser_agent_file_download_sys_prompt.md",
    ),
    "r",
    encoding="utf-8",
) as f:
    _FILE_DOWNLOAD_AGENT_SYS_PROMPT = f.read()


class EmptyModel(BaseModel):
    """Empty structured model for default structured output requirement."""

    pass


class FileDownloadAgent(ReActAgent):
    """Lightweight helper agent that downloads files"""

    finish_function_name: str = "file_download_final_response"

    def __init__(
        self,
        browser_agent: Any,
        sys_prompt: str = _FILE_DOWNLOAD_AGENT_SYS_PROMPT,
        max_iters: int = 15,
    ) -> None:
        name = (
            f"{getattr(browser_agent, 'name', 'browser_agent')}_file_download"
        )
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model=browser_agent.model,
            formatter=browser_agent.formatter,
            memory=InMemoryMemory(),
            toolkit=browser_agent.toolkit,
            max_iters=max_iters,
        )
        # Register the finish function
        self.toolkit.register_tool_function(self.file_download_final_response)
        # Remove conflicting tool functions if they exist
        if hasattr(self.toolkit, "remove_tool_function"):
            try:
                self.toolkit.remove_tool_function("browser_pdf_save")
            except Exception:
                # Tool may not exist, ignore removal errors
                pass
            try:
                self.toolkit.remove_tool_function("file_download")
            except Exception:
                # Tool may not exist, ignore removal errors
                pass

    async def file_download_final_response(
        self,  # pylint: disable=W0613
        **kwargs: Any,  # pylint: disable=W0613
    ) -> ToolResponse:
        """Summarize the file download outcome."""
        hint_msg = Msg(
            "user",
            (
                "Provide a concise summary of the file download attempt.\n"
                "Highlight these items:\n"
                "0. The original request\n"
                "1. The element(s) interacted with and actions taken\n"
                "2. The download status or any issues encountered\n"
                "3. Any follow-up recommendations or next steps\n"
            ),
            role="user",
        )

        memory_msgs = await self.memory.get_memory()
        memory_msgs_copy = copy.deepcopy(memory_msgs)
        if memory_msgs_copy:
            last_msg = memory_msgs_copy[-1]
            last_msg.content = last_msg.get_content_blocks("text")
            memory_msgs_copy[-1] = last_msg

        prompt = await self.formatter.format(
            msgs=[
                Msg("system", self.sys_prompt, "system"),
                *memory_msgs_copy,
                hint_msg,
            ],
        )

        res = await self.model(prompt)

        if self.model.stream:
            summary_text = ""
            async for chunk in res:
                summary_text = chunk.content[0]["text"]
        else:
            summary_text = res.content[0]["text"]

        summary_text = summary_text or "No summary generated."

        structure_response = {
            "task_done": True,
            "subtask_progress_summary": summary_text,
            "generated_files": {},
        }

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="File download summary generated. " + summary_text,
                ),
            ],
            metadata={
                "success": True,
                "structured_output": structure_response,
            },
            is_last=True,
        )


def _build_initial_instruction(
    target_description: str,
    snapshot_text: str,
) -> str:
    """Compose the initial instruction for the helper agent."""
    return (
        "You must locate and trigger the download for the requested file.\n\n"
        "Target description provided by the user:\n"
        f"{target_description}\n\n"
        "Latest snapshot captured prior to your run:\n"
        f"{snapshot_text}\n\n"
        "Follow the sys prompt guidance, think step-by-step, and verify that "
        "the download action succeeded. If the download cannot be completed, "
        "explain why in the final summary."
    )


async def file_download(
    browser_agent: Any,
    target_description: str,
) -> ToolResponse:
    """
    Download the target file. The current page should
    contain download-related element.

    Args:
        target_description (str): The description of the
        target file to download.

    Returns:
        ToolResponse: A structured response containing
        the download directory.
    """
    try:
        snapshot_chunks = await browser_agent._get_snapshot_in_text()
    except Exception as exc:  # pylint: disable=broad-except
        snapshot_chunks = []
        snapshot_error = str(exc)
    else:
        snapshot_error = ""

    snapshot_text = "\n\n---\n\n".join(snapshot_chunks)
    if snapshot_error and not snapshot_text:
        snapshot_text = f"[Snapshot failed: {snapshot_error}]"

    sub_agent = FileDownloadAgent(browser_agent)
    instruction = _build_initial_instruction(
        target_description=target_description,
        snapshot_text=snapshot_text,
    )

    init_msg = Msg(
        name="user",
        role="user",
        content=instruction,
    )

    try:
        sub_agent_response_msg = await sub_agent.reply(
            init_msg,
            structured_model=EmptyModel,
        )

        text_content = ""
        if sub_agent_response_msg.content:
            first_block = sub_agent_response_msg.content[0]
            if isinstance(first_block, dict):
                text_content = first_block.get("text") or ""
            else:
                text_content = getattr(first_block, "text", "") or ""

        if not text_content:
            text_content = (
                "File download agent finished without a textual summary."
            )

        return ToolResponse(
            metadata=sub_agent_response_msg.metadata,
            content=[
                TextBlock(
                    type="text",
                    text=text_content,
                ),
            ],
        )
    except Exception as exc:  # pylint: disable=broad-except
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Tool call Error. Cannot be executed. {exc}",
                ),
            ],
            metadata={"success": False},
            is_last=True,
        )
