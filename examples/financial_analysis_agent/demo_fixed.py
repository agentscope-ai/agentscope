"""
财务分析智能体示例 - 修复框架兼容性

演示如何使用修复后的工程化财务分析智能体
"""

import asyncio
import os
import json
from datetime import datetime
from typing import Dict, List, Optional, Any

try:
    from agentscope.message import Msg
    from agentscope.agent import ReActAgent, AgentBase
    from agentscope.model import OpenAIChatModel
    from agentscope.memory import InMemoryMemory
except ImportError:
    print("警告: 无法导入AgentScope框架，某些功能可能无法正常工作")
    # 定义fallback类
    class Msg:
        def __init__(self, name, content, role="assistant"):
            self.name = name
            self.content = content
            self.role = role
    
    class ReActAgent:
        def __init__(self, **kwargs):
            pass
    
    class OpenAIChatModel:
        def __init__(self, **kwargs):
            pass
    
    class InMemoryMemory:
        def __init__(self):
            pass

# 导入修复后的模块
try:
    from skills.__init___fixed import get_skill, list_skills
    from mcp.__init___fixed import get_mcp_client, get_mcp_tool_manager
    from a2a.__init___fixed import get_a2a_registry, get_a2a_communication_manager
except ImportError:
    print("警告: 无法导入修复后的模块，使用fallback实现")
    
    def get_skill(name):
        return None
    
    def list_skills():
        return []
    
    def get_mcp_client(config):
        class DummyMCPClient:
            def is_available(self):
                return False
        return DummyMCPClient()
    
    def get_mcp_tool_manager(config):
        return None
    
    def get_a2a_registry():
        return None
    
    def get_a2a_communication_manager(config):
        class DummyA2AManager:
            def register_agent(self, agent_id, agent):
                pass
            def send_message(self, from_agent, to_agent, message):
                return {"success": False, "error": "模块未加载"}
        return DummyA2AManager()


async def demo_fixed_compliant_usage():
    """演示框架兼容的使用"""
    print("=== 框架兼容性演示 ===")
    
    try:
        # 创建兼容的智能体
        agent = FixedCompliantFinancialAgent(
            name="框架兼容财务分析师",
            enable_skills=True,
            enable_mcp=False,  # 简化演示，避免依赖问题
            enable_a2a=False,
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
            print(f"智能体回复:\n{response.content[:200]}...")
            
        except Exception as e:
            print(f"分析失败: {str(e)}")
    
    except Exception as e:
        print(f"初始化失败: {str(e)}")


async def demo_skill_execution():
    """演示技能执行"""
    print("\n=== 技能执行演示 ===")
    
    try:
        agent = FixedCompliantFinancialAgent(enable_skills=True)
        
        # 执行财务分析技能
        result = await agent.execute_skill(
            skill_name="financial_analysis",
            company_symbol="MSFT",
            analysis_type="comprehensive",
            period="annual",
            years=3
        )
        
        print(f"技能执行结果: {json.dumps(result, indent=2, ensure_ascii=False)}")
        
    except Exception as e:
        print(f"技能执行失败: {str(e)}")


async def demo_mcp_integration():
    """演示MCP集成"""
    print("\n=== MCP集成演示 ===")
    
    try:
        agent = FixedCompliantFinancialAgent(enable_mcp=True)
        
        # 获取MCP数据
        result = await agent.get_mcp_data(
            service_name="financial_data",
            tool_name="get_stock_price",
            symbol="GOOGL",
            period="1m"
        )
        
        print(f"MCP数据获取结果: {json.dumps(result, indent=2, ensure_ascii=False)}")
        
    except Exception as e:
        print(f"MCP集成失败: {str(e)}")


async def demo_a2a_collaboration():
    """演示A2A协作"""
    print("\n=== A2A协作演示 ===")
    
    try:
        agent = FixedCompliantFinancialAgent(enable_a2a=True)
        
        # 协作分析
        result = await agent.collaborative_analysis(
            company_symbol="AMZN",
            analysis_types=["profitability", "solvency", "efficiency"]
        )
        
        print(f"协作分析结果: {json.dumps(result, indent=2, ensure_ascii=False)}")
        
    except Exception as e:
        print(f"A2A协作失败: {str(e)}")


async def demo_prompt_management():
    """演示提示词管理"""
    print("\n=== 提示词管理演示 ===")
    
    try:
        agent = FixedCompliantFinancialAgent(enable_prompt_manager=True)
        
        # 生成上下文提示词
        prompt = await agent.generate_contextual_prompt(
            scenario="financial_analysis",
            company_name="特斯拉",
            company_symbol="TSLA",
            analysis_types=["盈利能力分析", "风险评估"],
            time_period="最近3年"
        )
        
        print(f"生成的提示词:\n{prompt[:200]}...")
        
    except Exception as e:
        print(f"提示词管理失败: {str(e)}")


class FixedCompliantFinancialAgent:
    """
    修复框架兼容性的财务分析智能体
    
    使用标准的AgentScope模式和工具函数
    """

    def __init__(
        self,
        name: str = "框架兼容财务分析师",
        config_file: str = None,
        enable_skills: bool = True,
        enable_mcp: bool = True,
        enable_a2a: bool = True,
        enable_prompt_manager: bool = True,
        **kwargs
    ):
        """初始化兼容智能体"""
        
        # 使用简化的配置，避免导入问题
        model = None
        memory = None
        toolkit = None
        
        try:
            model = OpenAIChatModel(
                config_name="gpt-4",
                model="gpt-4",
                api_key=os.environ.get("OPENAI_API_KEY"),
                stream=True,
            )
            memory = InMemoryMemory()
        except ImportError:
            print("警告: 无法初始化模型和记忆")
        
        # 生成系统提示词
        sys_prompt = self._generate_compliant_sys_prompt(
            enable_skills=enable_skills,
            enable_mcp=enable_mcp,
            enable_a2a=enable_a2a,
            enable_prompt_manager=enable_prompt_manager
        )
        
        # 初始化AgentScope智能体（如果可用）
        if 'ReActAgent' in globals() and model and memory:
            self.agent = ReActAgent(
                name=name,
                sys_prompt=sys_prompt,
                model=model,
                memory=memory,
                toolkit=toolkit,
                **kwargs
            )
        else:
            # 创建简化包装器
            self.agent = SimpleAgentWrapper(name=name, sys_prompt=sys_prompt)
        
        # 初始化特性
        self.enable_skills = enable_skills
        self.enable_mcp = enable_mcp
        self.enable_a2a = enable_a2a
        self.enable_prompt_manager = enable_prompt_manager
        
        # 初始化组件
        if enable_prompt_manager:
            from .prompts import get_financial_prompt_manager
            self.prompt_manager = get_financial_prompt_manager()
        
        if enable_a2a:
            self.a2a_registry = get_a2a_registry()
            self.a2a_comm_manager = get_a2a_communication_manager()
            
            # 注册智能体
            self.a2a_registry.register_agent(
                agent_id="financial_analyst",
                agent=self.agent,
                capabilities=[
                    "financial_analysis",
                    "risk_assessment", 
                    "report_generation",
                    "data_collection"
                ],
                group="financial_services"
            )
            
            from .a2a import FinancialAnalysisWorkflow
            self.workflow = FinancialAnalysisWorkflow(self.a2a_comm_manager)
        
        self.logger = self._setup_logging()

    def _generate_compliant_sys_prompt(
        self,
        enable_skills: bool = True,
        enable_mcp: bool = True,
        enable_a2a: bool = True,
        enable_prompt_manager: bool = True
    ) -> str:
        """生成兼容的系统提示词"""
        capabilities = []
        
        if enable_skills:
            capabilities.append("- **技能支持**: 具备财务分析、市场研究、风险评估专业技能")
        
        if enable_mcp:
            capabilities.append("- **MCP集成**: 可通过MCP协议访问外部数据源和服务")
        
        if enable_a2a:
            capabilities.append("- **智能体协作**: 支持与其他智能体进行协作分析")
            capabilities.append("- **工作流引擎**: 预定义的财务分析工作流程")
        
        if enable_prompt_manager:
            capabilities.append("- **提示词管理**: 支持动态生成和管理提示词")
        
        capabilities.extend([
            "- **数据获取**: 获取财务报表、股价数据、市场指数",
            "- **财务分析**: 计算各种财务比率和趋势分析",
            "- **报告生成**: 生成专业的分析报告和可视化图表",
            "- **风险评估**: 识别和评估投资风险"
            "- **实时更新**: 实时数据更新",
            "- **交互式分析对话**: 支持多轮对话和上下文记忆"
        ])
        
        prompt = f"""你是一名专业的财务分析专家，具备以下高级能力：

## 核心能力
{chr(10).join(capabilities)}

## 工作原则
1. **数据驱动**: 基于真实数据进行分析，避免主观臆测
2. **专业准确**: 使用标准的财务分析方法论
3. **风险意识**: 始终考虑风险因素并提供风险提示
4. **实用导向**: 提供有价值的投资建议和决策支持

## 分析流程
1. **数据收集**: 获取相关的财务数据和市场信息
2. **比率计算**: 计算关键财务比率和指标
3. **趋势分析**: 分析历史数据的变化趋势
4. **行业对比**: 与行业平均水平进行比较
5. **风险评估**: 识别主要的财务和经营风险
6. **综合判断**: 基于全面分析得出结论
7. **建议输出**: 提供具体的投资建议

## 输出要求
- 提供准确的数据和计算结果
- 给出清晰的分析逻辑和结论
- 包含具体的风险提示
- 提供可操作的投资建议
- 标注数据来源和分析时间

请基于用户需求，运用你的专业能力进行全面、准确的财务分析。"""
        
        return prompt

    def _setup_logging(self):
        """设置日志"""
        try:
            import logging
            
            # 创建日志目录
            log_file = "logs/financial_agent_compliant.log"
            log_dir = os.path.dirname(log_file)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            
            # 配置日志
            logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                handlers=[
                    logging.FileHandler(log_file, encoding='utf-8'),
                    logging.StreamHandler()
                ]
            )
            
            self.logger = logging.getLogger(self.name)
            
        except Exception as e:
            print(f"设置日志失败: {str(e)}")
            return None

    async def execute_skill(
        self,
        skill_name: str,
        **parameters
    ) -> Dict[str, Any]:
        """执行技能"""
        if not self.enable_skills:
            return {"success": False, "error": "技能功能未启用"}
        
        try:
            skill = get_skill(skill_name)
            if not skill:
                return {"success": False, "error": f"未找到技能: {skill_name}"}
            
            self.logger.info(f"执行技能: {skill_name}, 参数: {parameters}")
            
            result = await skill.execute(parameters)
            
            self.logger.info(f"技能执行完成: {skill_name}, 结果: {result.get('success', False)}")
            
            return result
            
        except Exception as e:
            self.logger.error(f"技能执行失败: {skill_name}, 错误: {str(e)}")
            return {"success": False, "error": str(e)}

    async def get_mcp_data(self, service_name: str, tool_name: str, **params) -> Dict[str, Any]:
        """获取MCP数据"""
        if not self.enable_mcp:
            return {"success": False, "error": "MCP功能未启用"}
        
        try:
            mcp_client = get_mcp_client()
            result = await mcp_client.call_tool(service_name, tool_name, params)
            
            if result.success:
                return {"success": True, "data": result.content}
            else:
                return {"success": False, "error": result.error}
                
        except Exception as e:
            return {"success": False, "error": f"MCP数据获取失败: {str(e)}"}

    async def collaborative_analysis(
        self,
        company_symbol: str,
        analysis_types: List[str] = None
    ) -> Dict[str, Any]:
        """协作分析（使用A2A）"""
        if not self.enable_a2a:
            return {"success": False, "error": "A2A功能未启用"}
        
        try:
            self.logger.info(f"开始协作分析: {company_symbol}")
            
            task_data = {
                "company_symbol": company_symbol,
                "analysis_types": analysis_types or ["profitability", "solvency", "efficiency"],
                "workflow_id": f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            }
            
            result = await self.workflow.execute_analysis_workflow(task_data)
            
            self.logger.info(f"协作分析完成: {company_symbol}, 成功: {result.get('success', False)}")
            
            return result
            
        except Exception as e:
            self.logger.error(f"协作分析失败: {company_symbol}, 错误: {str(e)}")
            return {"success": False, "error": str(e)}

    async def generate_contextual_prompt(
        self,
        scenario: str,
        **context
    ) -> str:
        """生成上下文提示词"""
        if not self.enable_prompt_manager:
            return "提示词管理功能未启用"
        
        try:
            if scenario == "financial_analysis":
                return self.prompt_manager.get_financial_analysis_prompt(**context)
            elif scenario == "profitability":
                return self.prompt_manager.get_profitability_prompt(**context)
            elif scenario == "risk_assessment":
                return self.prompt_manager.get_risk_assessment_prompt(**context)
            elif scenario == "investment_recommendation":
                return self.prompt_manager.get_investment_recommendation_prompt(**context)
            else:
                return f"未知的场景: {scenario}"
                
        except Exception as e:
            return f"生成提示词失败: {str(e)}"

    async def comprehensive_analysis(
        self,
        company_symbol: str,
        analysis_options: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """综合分析（使用所有可用功能）"""
        self.logger.info(f"开始综合分析: {company_symbol}")
        
        result = {
            "company_symbol": company_symbol,
            "analysis_options": analysis_options or {},
            "timestamp": datetime.now().isoformat(),
            "components": {}
        }
        
        try:
            # 1. 使用技能进行基础分析
            if self.enable_skills:
                skill_result = await self.execute_skill(
                    "financial_analysis",
                    company_symbol=company_symbol,
                    analysis_type="comprehensive",
                    period="annual",
                    years=3
                )
                result["components"]["skill_analysis"] = skill_result
            
            # 2. 使用MCP获取外部数据
            if self.enable_mcp:
                mcp_data = await self.get_mcp_data(
                    "financial_data",
                    "get_stock_price",
                    symbol=company_symbol,
                    period="1m"
                )
                result["components"]["mcp_data"] = mcp_data
            
            # 3. 使用A2A进行协作分析
            if self.enable_a2a:
                collab_result = await self.collaborative_analysis(company_symbol, [
                    "profitability", "solvency", "efficiency"
                ])
                result["components"]["collaborative_analysis"] = collab_result
            
            # 4. 生成综合报告
            if self.enable_prompt_manager:
                final_report = await self._generate_comprehensive_report(result)
                result["comprehensive_report"] = final_report
            
            result["success"] = True
            self.logger.info(f"综合分析完成: {company_symbol}")
            
        except Exception as e:
            result["success"] = False
            result["error"] = str(e)
            self.logger.error(f"综合分析失败: {company_symbol}, 错误: {str(e)}")
        
        return result

    async def _generate_comprehensive_report(self, analysis_result: Dict[str, Any]) -> str:
        """生成综合报告"""
        report = f"""
# 框架兼容综合财务分析报告

## 基本信息
- 公司代码: {analysis_result.get('company_symbol', 'N/A')}
- 分析选项: {analysis_result.get('analysis_options', {})}
- 分析时间: {analysis_result.get('timestamp', 'N/A')}

## 分析组件

"""
        
        components = analysis_result.get("components", {})
        
        if "skill_analysis" in components:
            skill_result = components["skill_analysis"]
            report += f"### 技能分析\n"
            report += f"状态: {'✅' if skill_result.get('success') else '❌'} 成功\n"
            if skill_result.get("success"):
                report += f"结果摘要: {str(skill_result.get('result', {}))[:200]}...\n"
        
        if "mcp_data" in components:
            mcp_result = components["mcp_data"]
            report += f"### MCP数据获取\n"
            report += f"状态: {'✅' if mcp_result.get('success') else '❌'} 成功\n"
        
        if "collaborative_analysis" in components:
            collab_result = components["collaborative_analysis"]
            report += f"### 协作分析\n"
            report += f"状态: {'✅' if collab_result.get('success') else '❌'} 成功\n"
            report += f"工作流ID: {collab_result.get('workflow_id', 'N/A')}\n"
            report += f"完成步骤: {len(collab_result.get('steps', {}))}\n"
        
        report += f"""
## 综合结论
基于修复后的框架兼容性分析结果，该公司的整体表现...

## 建议
1. ...
2. ...
3. ...

## 框架优势
- **完全兼容**: 严格遵循AgentScope框架规范
- **模块化设计**: 每个特性都是独立的模块
- **错误处理**: 健壮的异常处理和恢复
- **日志记录**: 完整的操作日志和监控

---
*报告由框架兼容财务分析智能体生成*
*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""
        
        return report

    async def __call__(self, message: Msg) -> Msg:
        """重写调用方法，添加日志记录"""
        try:
            self.logger.info(f"收到消息: {message.name} - {message.content[:100]}...")
            
            # 调用智能体（如果可用）
            if hasattr(self, 'agent') and self.agent:
                response = await self.agent(message)
                self.logger.info(f"发送回复: {len(response.content)} 字符")
                return response
            else:
                # 简化处理
                return Msg(
                    name=self.name,
                    content=f"抱歉，处理您的请求时出现错误: 消息内容: {message.content}",
                    role="assistant"
                )
                
        except Exception as e:
            self.logger.error(f"处理消息失败: {str(e)}")
            return Msg(
                name=self.name,
                content=f"抱歉，处理您的请求时出现错误: {str(e)}",
                role="assistant"
            )


class SimpleAgentWrapper:
    """简化的智能体包装器"""
    
    def __init__(self, name: str, sys_prompt: str):
        self.name = name
        self.sys_prompt = sys_prompt
    
    async def __call__(self, message):
        """简化的消息处理"""
        # 简单回复
        return Msg(
            name=self.name,
            content=f"[简化模式] {self.name} 收到: {message.content}",
            role="assistant"
        )


async def main():
    """主演示函数"""
    print("框架兼容财务分析智能体演示程序")
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
        await demo_fixed_compliant_usage()
        await demo_skill_execution()
        await demo_mcp_integration()
        await demo_a2a_collaboration()
        await demo_prompt_management()
        
        # 运行完整的综合分析演示
        print("\n" + "=" * 50)
        print("=== 综合分析演示 ===")
        await demo_fixed_compliant_usage()
        
        print("\n" + "=" * 50)
        print("所有演示完成!")
        
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    except Exception as e:
        print(f"\n程序运行出错: {str(e)}")


if __name__ == "__main__":
    # 运行演示
    asyncio.run(main())