# -*- coding: utf-8 -*-
"""The main entry point of the Deep Research agent example."""
import asyncio
import os

from deep_research_agent import DeepResearchAgent

from agentscope import logger
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.model import DashScopeChatModel
from agentscope.message import Msg
from agentscope.mcp import StdIOStatefulClient


async def main(user_query: str) -> None:
    """The main entry point for the Deep Research agent example."""
    logger.setLevel("DEBUG")

    tavily_search_client = StdIOStatefulClient(
        name="tavily_mcp",
        command="npx",
        args=["-y", "tavily-mcp@latest"],
        env={"TAVILY_API_KEY": os.getenv("TAVILY_API_KEY", "")},
    )

    default_working_dir = os.path.join(
        os.path.dirname(__file__),
        "deepresearch_agent_demo_env",
    )
    agent_working_dir = os.getenv(
        "AGENT_OPERATION_DIR",
        default_working_dir,
    )
    os.makedirs(agent_working_dir, exist_ok=True)

    try:
        await tavily_search_client.connect()
        agent = DeepResearchAgent(
            name="Friday",
            sys_prompt="You are a helpful assistant named Friday.",
            model=DashScopeChatModel(
                api_key=os.environ.get("DASHSCOPE_API_KEY"),
                model_name="qwen-max",
                enable_thinking=False,
                stream=True,
            ),
            formatter=DashScopeChatFormatter(),
            memory=InMemoryMemory(),
            search_mcp_client=tavily_search_client,
            tmp_file_storage_dir=agent_working_dir,
        )
        user_name = "Bob"
        msg = Msg(
            user_name,
            content=user_query,
            role="user",
        )
        result = await agent(msg)
        logger.info(result)

    except Exception as err:
        logger.exception(err)
    finally:
        await tavily_search_client.close()


if __name__ == "__main__":
    query = (
        "If Eliud Kipchoge could maintain his record-making "
        "marathon pace indefinitely, how many thousand hours "
        "would it take him to run the distance between the "
        "Earth and the Moon its closest approach? Please use "
        "the minimum perigee value on the Wikipedia page for "
        "the Moon when carrying out your calculation. Round "
        "your result to the nearest 1000 hours and do not use "
        "any comma separators if necessary."
    )
    try:
        asyncio.run(main(query))
    except Exception as e:
        logger.exception(e)
