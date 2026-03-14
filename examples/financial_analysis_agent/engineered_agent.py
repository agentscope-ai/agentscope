"""
工程化财务分析智能体

集成所有高级特性，提供完整的工程化解决方案
"""

import asyncio
import os
import json
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime

from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit
from agentscope.message import Msg

# 导入高级特性
from .skills import get_skill, list_skills
from .mcp import create_mcp_toolkit, get_mcp_client
from .a2a import get_a2a_registry, FinancialAnalysisWorkflow
from .prompts import get_financial_prompt_manager
from .tools.manager import get_tool_manager, tool_function

from .tools.data_fetcher import FinancialDataFetcher
from .tools.analyzer import FinancialAnalyzer
from .tools.report_generator import ReportGenerator


class EngineeringFinancialAgent(ReActAgent):
    """
    工程化财务分析智能体
    
    集成了所有AgentScope高级特性的财务分析智能体：
    - Anthropic Agent Skill支持
    - MCP (Model Context Protocol) 集成
    - A2A (Agent-to-Agent) 通信
    - 提示词管理系统
    - 工具管理和配置
    - 日志和监控
    """

    def __init__(
        self,
        name: str = "工程化财务分析师",
        config_file: str = None,
        enable_skills: bool = True,
        enable_mcp: bool = True,
        enable_a2a: bool = True,
        enable_prompt_manager: bool = True,
        **kwargs
    ):
        """
        初始化工程化财务分析智能体
        
        Args:
            name: 智能体名称
            config_file: 配置文件路径
            enable_skills: 是否启用技能
            enable_mcp: 是否启用MCP
            enable_a2a: 是否启用A2A
            enable_prompt_manager: 是否启用提示词管理
            **kwargs: 其他参数
        """
        
        # 加载配置
        self.config = self._load_config(config_file)
        
        # 初始化模型
        model = OpenAIChatModel(
            config_name=self.config.get("model", {}).get("config_name", "gpt-4"),
            model=self.config.get("model", {}).get("model", "gpt-4"),
            api_key=os.environ.get("OPENAI_API_KEY"),
            stream=self.config.get("model", {}).get("stream", True),
        )
        
        # 初始化记忆
        memory = InMemoryMemory()
        
        # 创建工具集
        toolkit = self._create_toolkit(
            enable_skills=enable_skills,
            enable_mcp=enable_mcp
        )
        
        # 生成系统提示词
        sys_prompt = self._generate_sys_prompt(
            enable_skills=enable_skills,
            enable_mcp=enable_mcp,
            enable_a2a=enable_a2a
        )
        
        # 初始化父类
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            memory=memory,
            toolkit=toolkit,
            **kwargs
        )
        
        # 初始化高级特性
        self.enable_skills = enable_skills
        self.enable_mcp = enable_mcp
        self.enable_a2a = enable_a2a
        self.enable_prompt_manager = enable_prompt_manager
        
        # 初始化组件
        if enable_prompt_manager:
            self.prompt_manager = get_financial_prompt_manager()
        
        if enable_a2a:
            self.a2a_registry = get_a2a_registry()
            self.a2a_registry.register_agent(
                agent_id="financial_analyst",
                agent=self,
                capabilities=[
                    "financial_analysis",
                    "risk_assessment", 
                    "report_generation",
                    "data_collection"
                ],
                group="financial_services"
            )
            
            self.workflow = FinancialAnalysisWorkflow()
        
        # 设置日志
        self._setup_logging()
        
        self.logger.info(f"工程化财务分析智能体 {name} 初始化完成")

    def _load_config(self, config_file: str = None) -> Dict[str, Any]:
        """加载配置文件"""
        if config_file is None:
            config_file = os.path.join(
                os.path.dirname(__file__), "..", "config", "agent_config.json"
            )
        
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"加载配置文件失败: {str(e)}")
        
        # 默认配置
        return {
            "model": {
                "config_name": "gpt-4",
                "model": "gpt-4",
                "stream": True
            },
            "tools": {
                "enable_builtin": True,
                "enable_mcp": True,
                "enable_skills": True
            },
            "a2a": {
                "enabled": True,
                "auto_register": True
            },
            "logging": {
                "level": "INFO",
                "file": "logs/financial_agent.log"
            }
        }

    def _create_toolkit(self, enable_skills: bool = True, enable_mcp: bool = True) -> Toolkit:
        """创建工具集"""
        toolkit = Toolkit()
        
        # 获取工具管理器
        tool_manager = get_tool_manager()
        
        # 注册内置工具
        from .tools import data_fetcher, analyzer, report_generator
        
        # 数据获取工具
        data_fetcher = FinancialDataFetcher()
        toolkit.register_tool_function(data_fetcher.get_financial_statements)
        toolkit.register_tool_function(data_fetcher.get_stock_price)
        toolkit.register_tool_function(data_fetcher.get_company_info)
        
        # 分析工具
        analyzer = FinancialAnalyzer()
        toolkit.register_tool_function(analyzer.calculate_profitability_ratios)
        toolkit.register_tool_function(analyzer.calculate_solvency_ratios)
        toolkit.register_tool_function(analyzer.calculate_efficiency_ratios)
        toolkit.register_tool_function(analyzer.trend_analysis)
        
        # 报告工具
        report_generator = ReportGenerator()
        toolkit.register_tool_function(report_generator.generate_financial_report)
        toolkit.register_tool_function(report_generator.create_chart)
        
        # 注册技能工具
        if enable_skills:
            self._register_skill_tools(toolkit)
        
        # 注册MCP工具
        if enable_mcp:
            try:
                mcp_toolkit = asyncio.run(create_mcp_toolkit())
                for tool_func in mcp_toolkit.tools.values():
                    toolkit.register_tool_function(tool_func)
            except Exception as e:
                print(f"注册MCP工具失败: {str(e)}")
        
        return toolkit

    def _register_skill_tools(self, toolkit: Toolkit):
        """注册技能工具"""
        skills = list_skills()
        
        for skill_info in skills:
            skill_name = skill_info["name"]
            skill = get_skill(skill_name)
            
            if skill:
                async def skill_wrapper(*args, **kwargs):
                    # 提取技能参数
                    skill_params = {}
                    for param in skill.parameters:
                        if param in kwargs:
                            skill_params[param] = kwargs[param]
                    
                    # 执行技能
                    result = await skill.execute(skill_params)
                    
                    if result.get("success", False):
                        return result.get("result", {})
                    else:
                        raise Exception(result.get("error", "技能执行失败"))
                
                # 注册技能工具
                toolkit.register_tool_function(skill_wrapper)

    def _generate_sys_prompt(
        self,
        enable_skills: bool = True,
        enable_mcp: bool = True,
        enable_a2a: bool = True
    ) -> str:
        """生成系统提示词"""
        capabilities = []
        
        if enable_skills:
            capabilities.append("- **技能支持**: 具备财务分析、市场研究、风险评估等专业技能")
        
        if enable_mcp:
            capabilities.append("- **MCP集成**: 可通过MCP协议访问外部数据源和服务")
        
        if enable_a2a:
            capabilities.append("- **智能体协作**: 支持与其他智能体进行协作分析")
        
        capabilities.extend([
            "- **数据获取**: 获取财务报表、股价数据、市场指数",
            "- **财务分析**: 计算各种财务比率和趋势分析",
            "- **报告生成**: 生成专业的分析报告和可视化图表",
            "- **风险评估**: 识别和评估投资风险"
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
        logging_config = self.config.get("logging", {})
        
        # 创建日志目录
        log_file = logging_config.get("file", "logs/financial_agent.log")
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        
        # 配置日志
        logging.basicConfig(
            level=getattr(logging, logging_config.get("level", "INFO")),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        
        self.logger = logging.getLogger(self.name)

    async def execute_skill(
        self,
        skill_name: str,
        **parameters
    ) -> Dict[str, Any]:
        """
        执行技能
        
        Args:
            skill_name: 技能名称
            **parameters: 技能参数
            
        Returns:
            执行结果
        """
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

    async def collaborative_analysis(
        self,
        company_symbol: str,
        analysis_types: List[str] = None
    ) -> Dict[str, Any]:
        """
        协作分析（使用A2A）
        
        Args:
            company_symbol: 公司代码
            analysis_types: 分析类型列表
            
        Returns:
            协作分析结果
        """
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
        """
        生成上下文提示词
        
        Args:
            scenario: 场景名称
            **context: 上下文参数
            
        Returns:
            生成的提示词
        """
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
            self.logger.error(f"生成上下文提示词失败: {scenario}, 错误: {str(e)}")
            return f"生成提示词失败: {str(e)}"

    async def get_mcp_data(self, service_name: str, tool_name: str, **params) -> Dict[str, Any]:
        """
        获取MCP数据
        
        Args:
            service_name: 服务名称
            tool_name: 工具名称
            **params: 参数
            
        Returns:
            MCP数据
        """
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
            self.logger.error(f"MCP数据获取失败: {service_name}.{tool_name}, 错误: {str(e)}")
            return {"success": False, "error": str(e)}

    async def comprehensive_analysis(
        self,
        company_symbol: str,
        analysis_options: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        综合分析（使用所有可用功能）
        
        Args:
            company_symbol: 公司代码
            analysis_options: 分析选项
            
        Returns:
            综合分析结果
        """
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
                    analysis_type="comprehensive"
                )
                result["components"]["skill_analysis"] = skill_result
            
            # 2. 使用MCP获取外部数据
            if self.enable_mcp:
                mcp_data = await self.get_mcp_data(
                    "financial_data",
                    "get_stock_price",
                    symbol=company_symbol
                )
                result["components"]["mcp_data"] = mcp_data
            
            # 3. 使用A2A进行协作分析
            if self.enable_a2a:
                collaborative_result = await self.collaborative_analysis(company_symbol)
                result["components"]["collaborative_analysis"] = collaborative_result
            
            # 4. 生成综合报告
            report_result = await self._generate_comprehensive_report(result)
            result["comprehensive_report"] = report_result
            
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
# 综合财务分析报告

## 基本信息
- 公司代码: {analysis_result.get('company_symbol', 'N/A')}
- 分析时间: {analysis_result.get('timestamp', 'N/A')}
- 分析选项: {analysis_result.get('analysis_options', {})}

## 分析组件

"""
        
        components = analysis_result.get("components", {})
        
        if "skill_analysis" in components:
            skill_result = components["skill_analysis"]
            report += f"### 技能分析\n"
            report += f"状态: {'✅ 成功' if skill_result.get('success') else '❌ 失败'}\n"
            if skill_result.get("success"):
                report += f"结果摘要: {str(skill_result.get('result', {}))[:200]}...\n"
            report += "\n"
        
        if "mcp_data" in components:
            mcp_result = components["mcp_data"]
            report += f"### MCP数据获取\n"
            report += f"状态: {'✅ 成功' if mcp_result.get('success') else '❌ 失败'}\n"
            report += "\n"
        
        if "collaborative_analysis" in components:
            collab_result = components["collaborative_analysis"]
            report += f"### 协作分析\n"
            report += f"状态: {'✅ 成功' if collab_result.get('success') else '❌ 失败'}\n"
            if collab_result.get("success"):
                report += f"工作流ID: {collab_result.get('workflow_id', 'N/A')}\n"
                report += f"完成步骤: {len(collab_result.get('steps', {}))}\n"
            report += "\n"
        
        report += f"""
## 综合结论
基于多维度分析结果，该公司的整体表现...

## 建议
1. ...
2. ...
3. ...

## 风险提示
- ...
- ...

---
*报告由工程化财务分析智能体生成*
*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""
        
        return report

    async def __call__(self, message: Msg) -> Msg:
        """重写调用方法，添加日志记录"""
        self.logger.info(f"收到消息: {message.name} - {message.content[:100]}...")
        
        try:
            # 调用父类方法
            response = await super().__call__(message)
            
            self.logger.info(f"发送回复: {len(response.content)} 字符")
            
            return response
            
        except Exception as e:
            self.logger.error(f"处理消息失败: {str(e)}")
            return Msg(
                name=self.name,
                content=f"抱歉，处理您的请求时出现错误: {str(e)}",
                role="assistant"
            )