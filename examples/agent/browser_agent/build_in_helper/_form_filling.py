# -*- coding: utf-8 -*-
"""Standalone form filling skill for the browser agent."""
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
        "build_in_prompt/browser_agent_form_filling_sys_prompt.md",
    ),
    "r",
    encoding="utf-8",
) as f:
    _FORM_FILL_AGENT_SYS_PROMPT = f.read()


class EmptyModel(BaseModel):
    """Empty structured model for default structured output requirement."""

    pass


class FormFillingAgent(ReActAgent):
    """Lightweight helper agent that fills forms."""

    finish_function_name: str = "form_filling_final_response"

    def __init__(
        self,
        browser_agent: Any,
        sys_prompt: str = _FORM_FILL_AGENT_SYS_PROMPT,
        max_iters: int = 20,
    ) -> None:
        name = f"{getattr(browser_agent, 'name', 'browser_agent')}_form_fill"
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
        self.toolkit.register_tool_function(self.form_filling_final_response)

    async def form_filling_final_response(
        self,  # pylint: disable=W0613
        **kwargs: Any,  # pylint: disable=W0613
    ) -> ToolResponse:
        """Summarize the form filling outcome."""
        hint_msg = Msg(
            "user",
            (
                "Provide a concise summary of the completed form "
                "filling task.\n"
                "Highlight these items:\n"
                "0. The original task/query\n"
                "1. Which fields were filled/selected and their final values\n"
                "2. Any important observations or follow-up notes\n"
                "3. Confirmation that if the task is complete\n\n"
            ),
            role="user",
        )

        memory_msgs = await self.memory.get_memory()
        memory_msgs_copy = copy.deepcopy(memory_msgs)
        last_msg = memory_msgs_copy[-1]
        # check if the last message has tool call, if so clean the content

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

        structure_response = {
            "task_done": True,
            "subtask_progress_summary": summary_text,
            "generated_files": {},
        }

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="Form filling summary generated. " + summary_text,
                ),
            ],
            metadata={
                "success": True,
                "structured_output": structure_response,
            },
            is_last=True,
        )


def _build_initial_instruction(
    fill_information: str,
    snapshot_text: str,
) -> str:
    """Compose the initial instruction fed to the helper agent."""
    return (
        "You must complete the web form using the information "
        "provided below.\n\nFill instructions (plain text from the user):\n"
        f"{fill_information}\n\n"
        "Latest snapshot captured prior to your run:\n"
        f"{snapshot_text}\n\n"
    )


async def form_filling(
    browser_agent: Any,
    fill_information: str,
) -> ToolResponse:
    """
    Fill in a web form according to plain-text instructions.

    Args:
        fill_information (str):
            Plain-text description of the values that
            must be entered into the form,
            including any submission requirements.

    Returns:
        ToolResponse: Summary of the helper agent execution and status.
    """
    try:
        snapshot_chunks = (
            await browser_agent._get_snapshot_in_text()
        )  # pylint: disable=protected-access
    except Exception as exc:  # pylint: disable=broad-except
        snapshot_chunks = []
        snapshot_error = str(exc)
    else:
        snapshot_error = ""

    snapshot_text = "\n\n---\n\n".join(snapshot_chunks)
    if snapshot_error and not snapshot_text:
        snapshot_text = f"[Snapshot failed: {snapshot_error}]"

    sub_agent = FormFillingAgent(browser_agent)
    instruction = _build_initial_instruction(
        fill_information=fill_information,
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
                "Form filling agent finished without a textual summary."
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
    except Exception as e:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Tool call Error. Cannot be executed. {e}",
                ),
            ],
            metadata={"success": False},
            is_last=True,
        )
