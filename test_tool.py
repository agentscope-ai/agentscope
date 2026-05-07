"""第三课实验 - 正确的工具注册方式"""
import os
os.environ["OPENAI_API_KEY"] = "sk-test-dummy"

import agentscope
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock

agentscope.init(project="with-tool")

# 正确的工具定义方式（不用装饰器）
def calculator(expr: str) -> ToolResponse:
    """计算数学表达式

    Args:
        expr: 数学表达式，如 "2+3"
    """
    try:
        result = str(eval(expr))
        return ToolResponse(content=[TextBlock(type="text", text=result)])
    except Exception as e:
        return ToolResponse(content=[TextBlock(type="text", text=f"计算错误: {e}")])

# 用 Toolkit 注册工具
toolkit = Toolkit()
toolkit.register_tool_function(calculator)

# Agent 挂载工具
agent = ReActAgent(
    name="计算助手",
    sys_prompt="你是计算器助手",
    model=OpenAIChatModel(model_name="gpt-4o"),
    formatter=OpenAIChatFormatter(),
    toolkit=toolkit,  # 传入 toolkit
)

print("Agent 创建成功!")
print(f"工具列表: {list(toolkit.tools.keys())}")

# 验证异步调用
import asyncio
async def main():
    print("\nagent 已准备好接受带工具的调用")
    print("实际运行需要设置 OPENAI_API_KEY")

asyncio.run(main())
