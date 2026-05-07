"""使用 Ollama 本地模型测试"""
import agentscope
from agentscope.agent import ReActAgent
from agentscope.model import OllamaChatModel
from agentscope.formatter import OllamaChatFormatter

agentscope.init(project="ollama-local-test")

model = OllamaChatModel(
    model_name="llama3.2",
    host="http://localhost:11434"
)

agent = ReActAgent(
    name="本地助手",
    sys_prompt="你是一个有帮助的助手，用中文回答。",
    model=model,
    formatter=OllamaChatFormatter()
)

print("Agent 创建成功!")
print(f"模型: {model.model_name}")

import asyncio
async def main():
    print("\n正在测试 Ollama 本地模型...")
    try:
        result = await agent("你好，请自我介绍")
        print(f"\n=== Agent 回复 ===\n{result}")
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(main())
