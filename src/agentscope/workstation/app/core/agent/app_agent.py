# -*- coding: utf-8 -*-
"""App agent"""
from typing import Optional, Union, List, Dict, AsyncGenerator, Any, Callable
from loguru import logger
from agentdev.models.llm import BaseLLM, BASE_URL
from agentdev.schemas.message_schemas import (
    PromptMessageFunction,
    PromptMessageTool,
)
from agentdev.models.function_call import function_call_loop
from agentdev.base.function_tool import tool_function_factory
from openai.types.chat import ChatCompletionChunk
from app.schemas.app_agent import AgentConfig, AgentRequest, AgentParameters
from agentscope.agent import AgentBase
from agentscope.message import Msg


MODEL_PROVIDER_MAP = {
    "tongyi": {
        "base_url": BASE_URL,
    },
    "openai": {
        "base_url": None,
    },
}


def mcp_tool_execute_proxy(
    server_code: str,
    function_tool: PromptMessageTool,
    mcp_method: Callable,
) -> Callable:
    """
    Create a tool function proxy for MCP.
    """
    mcp_executor = tool_function_factory(
        function_tool,
        mcp_method,
        **{"server_code": server_code},
    )
    return mcp_executor


class AppAgent(AgentBase):
    """
    An agent class that implements all necessary function on agent
    platform with a stateless manner.
    """

    def __init__(
        self,
        name: str,
        config: Union[AgentConfig, Dict[str, Any]],
        model_config_name: str = None,
        **kwargs: Any,
    ) -> None:
        if isinstance(config, dict):
            self.config = AgentConfig(**config)
        else:
            self.config = config

        self.model = BaseLLM(**kwargs)

    def reply(self, x: Optional[Union[Msg, list[Msg]]] = None) -> Msg:
        pass

    async def astream(
        self,
        request: AgentRequest,
        parameters: AgentParameters,
        tool_functions: List[Union[PromptMessageFunction, Dict]] = None,
        mcp_tool_functions: List[Dict] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ChatCompletionChunk, Any]:
        """main logic is here"""
        if mcp_tool_functions is None:
            mcp_tool_functions = []
        if tool_functions is None:
            tool_functions = []
        messages = request.messages

        available_components = {}
        mcp_method = kwargs.get("mcp_method", None)

        # if mcp_method is None or isinstance(mcp_method, Callable):
        #     raise RuntimeError('make sure to pass the mcp method')

        # register mcp proxy
        for item in mcp_tool_functions:
            if item["function"].type == "mcp_tool_call":
                available_components.update(
                    {
                        item["function"].function.name: mcp_tool_execute_proxy(
                            server_code=item["server_code"],
                            function_tool=item["function"].function,
                            mcp_method=mcp_method,
                        ),
                    },
                )
            tool_functions.append(item["function"])

        if len(tool_functions):
            parameters.tools = tool_functions

        try:
            async for chunk in function_call_loop(
                model_cls=self.model,
                model=self.config.model,
                messages=messages,
                parameters=parameters,
                available_components=available_components,
                allow_incremental_tools_message=False,
            ):
                yield chunk
        except Exception as e:
            import traceback

            logger.error(f"error {e} with traceback {traceback.format_exc()}")
