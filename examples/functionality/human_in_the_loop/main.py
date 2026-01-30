# -*- coding: utf-8 -*-
""""""
import asyncio
import os
from abc import abstractmethod
from dataclasses import dataclass
from typing import Type, Literal

from anthropic.types import ToolUseBlock
from fastapi import FastAPI
from pydantic import BaseModel
from sympy import false

from agentscope.agent import ReActAgent, AgentBase, ReActAgentBase
from agentscope.formatter import DashScopeChatFormatter, FormatterBase
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel, ChatModelBase
from agentscope.module import StateModule
from agentscope.pipeline import stream_printing_messages
from agentscope.session import JSONSession
from agentscope.tool import Toolkit, ToolResponse


class ASAskUserPermissionEvent(Exception):
    """Exception raised when the agent needs to ask user permission."""

    def __init__(self, agent_name: str, agent_id: str, tool_call: ToolUseBlock):
        self.agent_name = agent_name
        self.agent_id = agent_id
        self.tool_call = tool_call
        super().__init__("Agent requests user permission.")


@dataclass
class ActingAction:
    tool_calls: list[ToolUseBlock]
    type: Literal['acting'] = 'acting'

@dataclass
class ReasoningAction:
    instructions: list[str]
    tool_choice: Literal['auto', 'none', 'required'] | str | None = None
app = FastAPI()

class AgentEventBase:
    priority: int

    @abstractmethod
    def get_resource(self) -> ReasoningResource | ActingResource:
        @dataclass
        class ReasoningResource:
            instruction: list[str]

            tools: list[dict]

            tool_choice: Literal[
                             'auto', 'none', 'required'] | str | None = None

            type: Literal['reasoning'] = 'reasoning'

        @dataclass
        class ActingResource:
            tool_call: ToolUseBlock

            type: Literal['acting'] = 'acting'


class StructuredOutputEvent(AgentEventBase):
    priority: int = 0

    def __init__(
        self,
        structured_model: Type[BaseModel],
        require_text_output: bool = True,
    ) -> None:
        self.structured_model = structured_model
        self._has_structured_output = False
        self.require_text_output = require_text_output

    def get_resource(self, agent: ReActAgent):
        agent.toolkit.register_tool_function(self.generate_response)
        agent.toolkit.set_extended_model(self.structured_model)


    def clean_end(self, msg: Msg) -> bool:
        if self._has_structured_output and (
            self.require_text_output and msg.get_content_blocks('text') or not self.require_text_output
        ):
            return True

        return False




    def generate_response(self) -> ToolResponse:
        ...
        self._has_structured_output = True
        return ToolResponse(
            content=[],
        )


class QueryRewriteEvent(AgentEventBase):
    priority: int = 0


class PlanningEvent(AgentEventBase):
    priority: int = 0


class Action:
    type: Literal['reasoning', 'acting']


class ReActAgent(ReActAgentBase):
    def __init__(
        self,
        name: str,
        sys_prompt: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        toolkit: Toolkit | None = None,
    ) -> None:
        super().__init__()
        self.name = name
        self._sys_prompt = sys_prompt
        self.model = model
        self.formatter = formatter
        self.toolkit = toolkit
        self.memory = InMemoryMemory()
        self.max_iters = 10

        self._events = []

    def _get_action(self) -> Action:


    async def reply(self, msg: Msg | list[Msg] | None, structured_model: Type[BaseModel]) -> Msg:

        for i in range(self.max_iters):
            action = self._get_action(i)

            # 1. 下一步的行动
            if action.type == 'reasoning':
                instructions = action.instructions,
                tool_choice = action.tool_choice
                tools = action.tools

                await self.memory.add(Msg('user', instructions, 'user'))

                msg_reasoning = await self._call_model(tools=action.tools, tool_choice=tool_choice)

                futures = [
                    self._acting(tool_call)
                    for tool_call in msg_reasoning.get_content_blocks(
                        "tool_use",
                    )
                ]
                # Parallel tool calls or not
                if self.parallel_tool_calls:
                    await asyncio.gather(*futures)
                else:
                    # Sequential tool calls
                    [await _ for _ in futures]

            if action.type == 'acting':
                tool_calls = action.tool_calls

                futures = [self._acting(_) for _ in tool_calls]
                # Parallel tool calls or not
                if self.parallel_tool_calls:
                    await asyncio.gather(*futures)
                else:
                    # Sequential tool calls
                    [await _ for _ in futures]





    def _reasoning(self) -> None:
        pass
    def _acting(self) -> None:
        pass


@app.get("/")
async def chat_endpoint(session_id: str, user_id: str, ):
    agent = ReActAgent(
        name="Friday",
        sys_prompt="You are a helpful assistant named Friday.",
        model=DashScopeChatModel(
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            model_name="qwen3-max",
        ),
        formatter=DashScopeChatFormatter(),
    )


    sub_agent = ReActAgent(
        name="SubFriday",
        sys_prompt="You are a helpful assistant named SubFriday.",
        model=DashScopeChatModel(
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            model_name="qwen3-max",
        ),
        formatter=DashScopeChatFormatter(),
    )

    session = JSONSession()
    await session.load_session_state(session_id=session_id, agent=agent)

    try:
        async for msg, last in stream_printing_messages(
            agents=[agent],
            coroutine_task=agent(
                Msg("user", "Hello, who are you?", "user"),
            ),
        ):
            yield msg

    except ASAskUserPermissionEvent as e:
        # 可以获取 e.tool_call
        print("error:", e)

    finally:
        await session.save_session_state(session_id=session_id, agent=agent)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="localhost",
        port=8000,
    )