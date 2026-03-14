"""
财务分析智能体使用示例

演示如何使用财务分析智能体进行公司财务分析
"""

import asyncio
import os
from agentscope.message import Msg

# 导入财务分析智能体
from financial_analysis_agent import FinancialAnalysisAgent


async def basic_financial_analysis_example():
    """基础财务分析示例"""
    
    print("=== 基础财务分析示例 ===")
    
    # 创建财务分析智能体
    agent = FinancialAnalysisAgent(
        name="财务分析专家",
        model_config={
            "config_name": "gpt-4",
            "model": "gpt-4",
            "api_key": os.environ.get("OPENAI_API_KEY"),
            "stream": True,
        }
    )
    
    # 创建用户消息
    user_msg = Msg(
        name="user",
        content="请分析苹果公司(AAPL)的财务状况，包括盈利能力、偿债能力和运营效率",
        role="user"
    )
    
    try:
        # 获取智能体响应
        response = await agent(user_msg)
        print(f"智能体回复: {response.content}")
        
    except Exception as e:
        print(f"分析失败: {str(e)}")


async def comprehensive_analysis_example():
    """综合财务分析示例"""
    
    print("\n=== 综合财务分析示例 ===")
    
    # 创建智能体
    agent = FinancialAnalysisAgent()
    
    # 进行综合分析
    result = await agent.analyze_company(
        company_code="MSFT",  # 微软
        analysis_type="comprehensive",
        period="annual",
        years=3
    )
    
    # 显示分析结果
    print(f"公司代码: {result['company_code']}")
    print(f"分析类型: {result['analysis_type']}")
    
    if "error" in result:
        print(f"分析失败: {result['error']}")
    else:
        print("=== 盈利能力分析 ===")
        if "profitability" in result.get("analysis", {}):
            profitability = result["analysis"]["profitability"]
            print(f"毛利率: {profitability.get('ratios', {}).get('gross_margin', 0):.2%}")
            print(f"净利率: {profitability.get('ratios', {}).get('net_margin', 0):.2%}")
            print(f"ROE: {profitability.get('ratios', {}).get('roe', 0):.2%}")
        
        print("\n=== 偿债能力分析 ===")
        if "solvency" in result.get("analysis", {}):
            solvency = result["analysis"]["solvency"]
            print(f"负债权益比: {solvency.get('ratios', {}).get('debt_to_equity', 0):.2f}")
            print(f"流动比率: {solvency.get('ratios', {}).get('current_ratio', 0):.2f}")
        
        print("\n=== 运营效率分析 ===")
        if "efficiency" in result.get("analysis", {}):
            efficiency = result["analysis"]["efficiency"]
            print(f"资产周转率: {efficiency.get('ratios', {}).get('asset_turnover', 0):.2f}")


async def batch_analysis_example():
    """批量分析示例"""
    
    print("\n=== 批量分析示例 ===")
    
    # 要分析的公司列表
    companies = ["AAPL", "MSFT", "GOOGL", "AMZN"]
    
    agent = FinancialAnalysisAgent()
    
    for company in companies:
        print(f"\n分析 {company}:")
        try:
            result = await agent.analyze_company(
                company_code=company,
                analysis_type="summary",
                period="annual",
                years=2
            )
            
            if "error" not in result:
                print(f"✓ {company} 分析完成")
            else:
                print(f"✗ {company} 分析失败: {result['error']}")
                
        except Exception as e:
            print(f"✗ {company} 分析异常: {str(e)}")


async def interactive_analysis_example():
    """交互式分析示例"""
    
    print("\n=== 交互式财务分析 ===")
    
    agent = FinancialAnalysisAgent()
    
    while True:
        print("\n请选择操作:")
        print("1. 分析公司财务")
        print("2. 获取股价信息")
        print("3. 生成分析报告")
        print("4. 退出")
        
        choice = input("请输入选择 (1-4): ").strip()
        
        if choice == "1":
            company = input("请输入公司代码 (如 AAPL): ").strip().upper()
            if company:
                print(f"正在分析 {company}...")
                result = await agent.analyze_company(company_code=company)
                
                if "error" not in result:
                    print(f"✓ {company} 分析完成")
                    print(f"分析结果包含 {len(result.get('analysis', {}))} 个分析维度")
                else:
                    print(f"✗ 分析失败: {result['error']}")
        
        elif choice == "2":
            company = input("请输入公司代码: ").strip().upper()
            if company:
                # 这里可以直接调用数据获取工具
                print(f"获取 {company} 股价信息...")
                # 实际实现中会调用相应的工具函数
        
        elif choice == "3":
            print("生成分析报告功能...")
            # 实际实现中会调用报告生成工具
        
        elif choice == "4":
            print("退出分析")
            break
        
        else:
            print("无效选择，请重新输入")


async def main():
    """主函数"""
    print("财务分析智能体示例程序")
    print("=" * 50)
    
    # 检查环境变量
    if not os.environ.get("OPENAI_API_KEY"):
        print("警告: 未设置 OPENAI_API_KEY 环境变量")
        print("请设置您的 OpenAI API 密钥以使用完整功能")
        return
    
    try:
        # 运行基础示例
        await basic_financial_analysis_example()
        
        # 运行综合分析示例
        await comprehensive_analysis_example()
        
        # 运行批量分析示例
        await batch_analysis_example()
        
        # 运行交互式示例（可选）
        run_interactive = input("\n是否运行交互式分析? (y/n): ").strip().lower()
        if run_interactive == 'y':
            await interactive_analysis_example()
        
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    except Exception as e:
        print(f"\n程序运行出错: {str(e)}")


if __name__ == "__main__":
    # 运行示例
    asyncio.run(main())