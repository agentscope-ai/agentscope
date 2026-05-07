"""第一课实验脚本 - 验证代码结构，不调用实际API"""

import agentscope

# 1. 测试初始化
print("1. 测试 agentscope.init()...")
try:
    # 使用临时项目名称，不涉及实际API调用
    agentscope.init(project="test-project")
    print("   agentscope.init() - OK")
except Exception as e:
    print(f"   agentscope.init() - 错误: {e}")

# 2. 测试导入
print("\n2. 测试导入...")
try:
    from agentscope.agent import ReActAgent
    from agentscope.model import OpenAIChatModel
    from agentscope.formatter import OpenAIChatFormatter
    print("   ReActAgent - OK")
    print("   OpenAIChatModel - OK")
    print("   OpenAIChatFormatter - OK")
except Exception as e:
    print(f"   导入错误: {e}")

# 3. 测试 Agent 实例化（不实际调用）
print("\n3. 测试 Agent 实例化...")
try:
    # 使用 dummy API key 格式，仅验证代码结构
    import os
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy-key-for-structure-check")

    agent = ReActAgent(
        name="助手",
        sys_prompt="你是一个有帮助的助手。",
        model=OpenAIChatModel(
            model_name="gpt-4o",
            api_key="sk-test-dummy-key",  # 结构验证用
        ),
        formatter=OpenAIChatFormatter(),
    )
    print("   ReActAgent 实例化 - OK")
    print(f"   Agent 名称: {agent.name}")
    print(f"   Agent 系统提示: {agent.sys_prompt}")
except Exception as e:
    print(f"   ReActAgent 实例化 - 错误: {e}")
    import traceback
    traceback.print_exc()

print("\n=== 结构验证完成 ===")
print("如需实际运行，请设置 OPENAI_API_KEY 环境变量")
