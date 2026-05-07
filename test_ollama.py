"""使用 Ollama 本地模型测试"""
import agentscope
from agentscope.agent import ReActAgent
from agentscope.model import OllamaChatModel
from agentscope.formatter import OllamaChatFormatter

agentscope.init(project="ollama-test")

model = OllamaChatModel(
    model_name="qwen3.5:397b-cloud",
    host="http://localhost:11434"
)

agent = ReActAgent(
    name="测试助手",
    sys_prompt="你是一个有帮助的助手，用中文回答。",
    model=model,
    formatter=OllamaChatFormatter()
)

print("Agent 创建成功!")
print(f"模型: {model.model_name}")

import asyncio
async def main():
    print("\n正在测试 Ollama 连接...")
    try:
        result = await agent("你好，请自我介绍")
        print(f"结果: {result}")
    except Exception as e:
        print(f"错误: {e}")

asyncio.run(main())
