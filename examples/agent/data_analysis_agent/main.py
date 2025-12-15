# -*- coding: utf-8 -*-
"""A data analysis agent example that uses code interpreter to analyze data."""
import os
import asyncio
from typing import Optional

from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.message import Msg
from agentscope.tool import Toolkit, execute_python_code as execute_code_tool
from agentscope.formatter import DeepSeekChatFormatter

# Define the system prompt for the data analysis agent
SYS_PROMPT = """You are a helper AI data analyst.
You have access to a tool 'execute_python_code' that can execute python code.
You are given a file 'titanic.csv' in the current directory.
Your task is to analyze the data and generate a plot to show the age distribution of passengers.
You should:
1. Write python code to read the csv file using pandas.
2. Plot the age distribution using matplotlib or seaborn.
3. Save the plot to 'age_distribution.png'.
4. Print "SUCCESS" if everything goes well.
"""


def main(api_key: Optional[str] = None) -> None:
    """Run the data analysis agent."""
    # Use the provided key or fallback to env
    if api_key is None:
        api_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")

    # 1. Initialize the toolkit and register the tool
    toolkit = Toolkit()
    toolkit.register_tool_function(execute_code_tool)

    # 2. Initialize the model
    # Use DeepSeek via OpenAIChatModel
    model = OpenAIChatModel(
        model_name="deepseek-chat",
        api_key=api_key,
        client_kwargs={"base_url": "https://api.deepseek.com"},
    )

    # 3. Initialize the agent
    agent = ReActAgent(
        name="DataAnalyst",
        sys_prompt=SYS_PROMPT,
        model=model,
        toolkit=toolkit,
        formatter=DeepSeekChatFormatter(),
    )

    # 4. Start the task
    msg = Msg(name="User", content="Please analyze the titanic.csv file and plot the age distribution.", role="user")
    
    # We use asyncio.run to execute the async agent
    return agent(msg)

async def run_async_main():
    # Use the provided key
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("⚠️  DEEPSEEK_API_KEY not found.")
        return

    toolkit = Toolkit()
    toolkit.register_tool_function(execute_code_tool)

    model = OpenAIChatModel(
        model_name="deepseek-chat",
        api_key=api_key,
        client_kwargs={"base_url": "https://api.deepseek.com"},
        stream=True
    )

    agent = ReActAgent(
        name="DataAnalyst",
        sys_prompt=SYS_PROMPT,
        model=model,
        toolkit=toolkit,
        formatter=DeepSeekChatFormatter(),
    )

    msg = Msg(name="User", content="Please analyze the titanic.csv file and plot the age distribution.", role="user")
    
    # Interact
    res = await agent(msg)
    print(f"\nFinal Result: {res.content}")

if __name__ == "__main__":
    asyncio.run(run_async_main())
