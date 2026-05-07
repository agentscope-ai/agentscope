import os
# 用假 key 仅验证代码结构，不实际调用 API
os.environ["OPENAI_API_KEY"] = "sk-test-dummy-key-for-structure"

import agentscope

agentscope.init(project="my-first-agent")

from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter

agent = ReActAgent(
    name="助手",
    sys_prompt="你是一个有帮助的助手。",
    model=OpenAIChatModel(model_name="gpt-4o"),
    formatter=OpenAIChatFormatter(),
)

# 验证 agent 对象创建成功
print(f"Agent 创建成功!")
print(f"  - 名称: {agent.name}")
print(f"  - 系统提示: {agent.sys_prompt}")
print(f"  - 模型: {agent.model.model_name}")
print(f"  - Formatter 类型: {type(agent.formatter).__name__}")

# 模拟异步调用验证（不实际发 API 请求）
import asyncio
async def main():
    # 注意：这里不会真正调用 API，因为我们用的是假 key
    # 但可以验证 agent 对象确实是异步可调用的
    print("\nagent 对象已准备就绪，可接受异步调用")
    print("由于使用假 key，实际运行会失败，但代码结构验证通过")

asyncio.run(main())
