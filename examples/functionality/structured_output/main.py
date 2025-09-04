# -*- coding: utf-8 -*-
"""The main entry point of the structured output example."""
import asyncio
import json
import os
from typing import Literal

from pydantic import BaseModel, Field

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit


class TableModel(BaseModel):
    """A simple table model for structured output."""

    name: str = Field(description="The name of the person")
    age: int = Field(description="The age of the person", ge=0, le=120)
    intro: str = Field(description="A one-sentence introduction of the person")
    honors: list[str] = Field(
        description="A list of honors received by this person",
    )


class ChoiceModel(BaseModel):
    """A simple choice model for structured output."""

    choice: Literal["apple", "banana", "orange"] = Field(
        description="Your choice of fruit",
    )


async def main() -> None:
    """The main entry point for the structured output example."""
    toolkit = Toolkit()
    agent = ReActAgent(
        name="Friday",
        sys_prompt="You are a helpful assistant named Friday.",
        model=DashScopeChatModel(
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            model_name="qwen-max",
            stream=True,
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
    )

    query_msg_1 = Msg(
        "user",
        "Please introduce Einstein",
        "user",
    )
    res = await agent(query_msg_1, structured_model=TableModel)
    print(
        "Structured Output 1:\n"
        "```\n"
        f"{json.dumps(res.metadata, indent=4)}\n"
        "```",
    )

    query_msg_2 = Msg(
        "user",
        "Choose one of your favorite fruit",
        "user",
    )
    res = await agent(query_msg_2, structured_model=ChoiceModel)
    print(
        "Structured Output 2:\n"
        "```\n"
        f"{json.dumps(res.metadata, indent=4)}\n"
        "```",
    )


asyncio.run(main())
