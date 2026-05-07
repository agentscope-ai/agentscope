import asyncio
import agentscope
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter

async def main():
    agentscope.init(project="my-first-agent")

    my_agent = ReActAgent(
        name="助手",
        sys_prompt="你是一个有帮助的编程助手。",
        model=OpenAIChatModel(model_name="gpt-4o"),
        formatter=OpenAIChatFormatter(),
    )

    response = await my_agent("请用 Python 写一个快速排序算法")
    print(response)

if __name__ == "__main__":
    asyncio.run(main())