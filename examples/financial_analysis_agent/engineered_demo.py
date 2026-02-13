"""
工程化财务分析智能体演示

展示如何使用集成了所有AgentScope高级特性的财务分析智能体
"""

import asyncio
import os
import json
from datetime import datetime

from agentscope.message import Msg

# 导入工程化智能体
from engineered_agent import EngineeringFinancialAgent


async def demo_basic_usage():
    """基础使用演示"""
    print("=== 基础使用演示 ===")
    
    # 创建工程化智能体
    agent = EngineeringFinancialAgent(
        name="财务分析专家",
        enable_skills=True,
        enable_mcp=True,
        enable_a2a=True,
        enable_prompt_manager=True
    )
    
    # 发送分析请求
    user_msg = Msg(
        name="user",
        content="请分析苹果公司(AAPL)的财务状况，重点关注盈利能力和风险",
        role="user"
    )
    
    try:
        response = await agent(user_msg)
        print(f"智能体回复:\n{response.content}")
    except Exception as e:
        print(f"分析失败: {str(e)}")


async def demo_skill_usage():
    """技能使用演示"""
    print("\n=== 技能使用演示 ===")
    
    agent = EngineeringFinancialAgent(enable_skills=True)
    
    # 执行财务分析技能
    result = await agent.execute_skill(
        skill_name="financial_analysis",
        company_symbol="MSFT",
        analysis_type="comprehensive",
        period="annual",
        years=3
    )
    
    print(f"技能执行结果: {json.dumps(result, indent=2, ensure_ascii=False)}")


async def demo_mcp_integration():
    """MCP集成演示"""
    print("\n=== MCP集成演示 ===")
    
    agent = EngineeringFinancialAgent(enable_mcp=True)
    
    # 通过MCP获取数据
    result = await agent.get_mcp_data(
        service_name="financial_data",
        tool_name="get_stock_price",
        symbol="GOOGL",
        period="1m"
    )
    
    print(f"MCP数据获取结果: {json.dumps(result, indent=2, ensure_ascii=False)}")


async def demo_a2a_collaboration():
    """A2A协作演示"""
    print("\n=== A2A协作演示 ===")
    
    agent = EngineeringFinancialAgent(enable_a2a=True)
    
    # 协作分析
    result = await agent.collaborative_analysis(
        company_symbol="AMZN",
        analysis_types=["profitability", "solvency", "efficiency"]
    )
    
    print(f"协作分析结果: {json.dumps(result, indent=2, ensure_ascii=False)}")


async def demo_prompt_management():
    """提示词管理演示"""
    print("\n=== 提示词管理演示 ===")
    
    agent = EngineeringFinancialAgent(enable_prompt_manager=True)
    
    # 生成上下文提示词
    prompt = await agent.generate_contextual_prompt(
        scenario="financial_analysis",
        company_name="特斯拉",
        company_symbol="TSLA",
        analysis_types=["盈利能力分析", "风险评估"],
        time_period="最近3年"
    )
    
    print(f"生成的提示词:\n{prompt}")


async def demo_comprehensive_analysis():
    """综合分析演示"""
    print("\n=== 综合分析演示 ===")
    
    agent = EngineeringFinancialAgent(
        enable_skills=True,
        enable_mcp=True,
        enable_a2a=True,
        enable_prompt_manager=True
    )
    
    # 执行综合分析
    result = await agent.comprehensive_analysis(
        company_symbol="NVDA",
        analysis_options={
            "include_risk_assessment": True,
            "include_market_comparison": True,
            "generate_report": True
        }
    )
    
    print(f"综合分析结果:")
    print(f"- 成功: {result.get('success', False)}")
    print(f"- 公司代码: {result.get('company_symbol', 'N/A')}")
    
    if result.get('success'):
        components = result.get('components', {})
        print(f"- 技能分析: {'✅' if 'skill_analysis' in components else '❌'}")
        print(f"- MCP数据: {'✅' if 'mcp_data' in components else '❌'}")
        print(f"- 协作分析: {'✅' if 'collaborative_analysis' in components else '❌'}")
        
        if 'comprehensive_report' in result:
            print(f"\n综合报告预览:")
            print(result['comprehensive_report'][:500] + "...")
    else:
        print(f"- 错误: {result.get('error', 'Unknown error')}")


async def demo_configuration_management():
    """配置管理演示"""
    print("\n=== 配置管理演示 ===")
    
    # 使用自定义配置
    config = {
        "model": {
            "config_name": "gpt-3.5-turbo",
            "model": "gpt-3.5-turbo",
            "stream": True
        },
        "tools": {
            "enable_builtin": True,
            "enable_mcp": False,  # 禁用MCP
            "enable_skills": True
        },
        "logging": {
            "level": "DEBUG"
        }
    }
    
    # 保存临时配置文件
    config_file = "temp_agent_config.json"
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    
    try:
        # 使用自定义配置创建智能体
        agent = EngineeringFinancialAgent(
            name="定制财务分析师",
            config_file=config_file
        )
        
        print(f"智能体配置:")
        print(f"- 启用技能: {agent.enable_skills}")
        print(f"- 启用MCP: {agent.enable_mcp}")
        print(f"- 启用A2A: {agent.enable_a2a}")
        print(f"- 启用提示词管理: {agent.enable_prompt_manager}")
        
        # 测试简单对话
        test_msg = Msg(
            name="user",
            content="你好，请介绍一下你的能力",
            role="user"
        )
        
        response = await agent(test_msg)
        print(f"\n测试回复:\n{response.content[:200]}...")
        
    finally:
        # 清理临时配置文件
        if os.path.exists(config_file):
            os.remove(config_file)


async def demo_error_handling():
    """错误处理演示"""
    print("\n=== 错误处理演示 ===")
    
    agent = EngineeringFinancialAgent()
    
    # 测试技能执行错误
    print("1. 测试不存在的技能:")
    result = await agent.execute_skill("nonexistent_skill")
    print(f"结果: {result}")
    
    # 测试MCP错误
    print("\n2. 测试MCP服务错误:")
    result = await agent.get_mcp_data("invalid_service", "invalid_tool")
    print(f"结果: {result}")
    
    # 测试A2A错误
    print("\n3. 测试A2A错误（功能未启用）:")
    agent_a2a_disabled = EngineeringFinancialAgent(enable_a2a=False)
    result = await agent_a2a_disabled.collaborative_analysis("AAPL")
    print(f"结果: {result}")


async def main():
    """主演示函数"""
    print("工程化财务分析智能体演示程序")
    print("=" * 50)
    
    # 检查环境变量
    required_env_vars = ["OPENAI_API_KEY"]
    missing_vars = [var for var in required_env_vars if not os.environ.get(var)]
    
    if missing_vars:
        print(f"警告: 缺少环境变量: {', '.join(missing_vars)}")
        print("某些功能可能无法正常工作")
        print()
    
    try:
        # 运行各种演示
        await demo_basic_usage()
        await demo_skill_usage()
        await demo_mcp_integration()
        await demo_a2a_collaboration()
        await demo_prompt_management()
        await demo_comprehensive_analysis()
        await demo_configuration_management()
        await demo_error_handling()
        
        print("\n" + "=" * 50)
        print("所有演示完成!")
        
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    except Exception as e:
        print(f"\n程序运行出错: {str(e)}")


if __name__ == "__main__":
    # 运行演示
    asyncio.run(main())