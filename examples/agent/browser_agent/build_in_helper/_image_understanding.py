# -*- coding: utf-8 -*-
"""Standalone image understanding skill for the browser agent."""
# flake8: noqa: E501
# pylint: disable=W0212
# pylint: disable=too-many-lines
# pylint: disable=C0301
from __future__ import annotations

import json
import uuid
from typing import Any

from agentscope.message import (
    Base64Source,
    ImageBlock,
    Msg,
    TextBlock,
    ToolUseBlock,
)
from agentscope.tool import ToolResponse


async def image_understanding(
    browser_agent: Any,
    object_description: str,
    task: str,
) -> ToolResponse:
    """
    Locate an element and solve a visual task on the current webpage.

    Args:
        object_description (str): The description of the object to locate.
        task (str): The specific task or question to solve about the image
        (e.g., description, object detection, activity recognition, or
        answering a question about the image's content).

    Returns:
        ToolResponse: A structured response containing the answer to
        the specified task based on the image content.
    """

    sys_prompt = (
        "You are a web page analysis expert. Given the following page "
        "snapshot and object description, "
        "identify the exact element and its reference string (ref) "
        "that matches the description. "
        "Return ONLY a JSON object: "
        '{"element": <element description>, "ref": <ref string>}'
    )

    snapshot_chunks = (
        await browser_agent._get_snapshot_in_text()  # noqa: E501 # pylint: disable=protected-access
    )
    page_snapshot = snapshot_chunks[0] if snapshot_chunks else ""
    user_prompt = (
        f"Object description: {object_description}\n"
        f"Page snapshot:\n{page_snapshot}"
    )

    prompt = await browser_agent.formatter.format(
        msgs=[
            Msg("system", sys_prompt, role="system"),
            Msg("user", user_prompt, role="user"),
        ],
    )
    res = await browser_agent.model(prompt)
    if browser_agent.model.stream:
        async for chunk in res:
            model_text = chunk.content[0]["text"]
    else:
        model_text = res.content[0]["text"]

    try:
        if "```json" in model_text:
            model_text = model_text.replace("```json", "").replace(
                "```",
                "",
            )
        element_info = json.loads(model_text)
        element = element_info.get("element", "")
        ref = element_info.get("ref", "")
    except Exception:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="Failed to parse element/ref from model output.",
                ),
            ],
            metadata={"success": False},
        )

    screenshot_tool_call = ToolUseBlock(
        id=str(uuid.uuid4()),
        name="browser_take_screenshot",
        input={"element": element, "ref": ref},
        type="tool_use",
    )
    screenshot_response = await browser_agent.toolkit.call_tool_function(
        screenshot_tool_call,
    )
    image_data = None
    async for chunk in screenshot_response:
        if (
            chunk.content
            and len(chunk.content) > 1
            and "data" in chunk.content[1]
        ):
            image_data = chunk.content[1]["data"]

    sys_prompt_task = (
        "You are a web automation expert. "
        "Given the object description, screenshot, and page context, "
        "solve the following task. Return ONLY the answer as plain text."
    )
    content_blocks = [
        TextBlock(
            type="text",
            text=(
                "Object description: "
                f"{object_description}\nTask: {task}\n"
                f"Page snapshot:\n{page_snapshot}"
            ),
        ),
    ]

    if image_data:
        image_block = ImageBlock(
            type="image",
            source=Base64Source(
                type="base64",
                media_type="image/png",
                data=image_data,
            ),
        )
        content_blocks.append(image_block)

    prompt_task = await browser_agent.formatter.format(
        msgs=[
            Msg("system", sys_prompt_task, role="system"),
            Msg("user", content_blocks, role="user"),
        ],
    )
    res_task = await browser_agent.model(prompt_task)
    if browser_agent.model.stream:
        async for chunk in res_task:
            answer_text = chunk.content[0]["text"]
    else:
        answer_text = res_task.content[0]["text"]

    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=(
                    f"Screenshot taken for element: {element}\nref: {ref}\n"
                    f"Task solution: {answer_text}"
                ),
            ),
        ],
    )
