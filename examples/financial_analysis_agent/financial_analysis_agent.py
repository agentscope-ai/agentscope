"""
财务经营分析智能体

这是一个专门的财务经营分析智能体，能够：
1. 获取财务数据
2. 进行财务分析
3. 生成分析报告
"""

import asyncio
import os
from typing import Dict, List, Optional, Any

from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit

from .tools.data_fetcher import FinancialDataFetcher
from .tools.analyzer import FinancialAnalyzer
from .tools.report_generator import ReportGenerator


class FinancialAnalysisAgent(ReActAgent):
    """
    财务经营分析智能体
    
    专门用于企业财务经营状况的分析，包括：
    - 财务数据获取
    - 财务比率分析
    - 趋势分析
    - 报告生成
    """

    def __init__(
        self,
        name: str = "财务分析专家",
        sys_prompt: Optional[str] = None,
        model_config: Optional[Dict] = None,
        **kwargs,
    ) -> None:
        """
        初始化财务分析智能体
        
        Args:
            name: 智能体名称
            sys_prompt: 系统提示词
            model_config: 模型配置
            **kwargs: 其他参数
        """
        
        if sys_prompt is None:
            sys_prompt = """你是一名专业的财务经营分析专家，具备以下能力：

1. **数据获取**：能够获取企业的财务报表数据、股价数据等
2. **财务分析**：能够计算和分析各种财务比率，如盈利能力、偿债能力、运营能力等
3. **趋势分析**：能够分析财务数据的历史趋势和预测未来走向
4. **风险评估**：能够评估企业的经营风险和财务风险
5. **报告生成**：能够生成详细的财务分析报告

你可以使用以下工具来完成分析任务：
- 获取财务数据
- 计算财务比率
- 生成分析报告

在分析过程中，请：
- 保持专业和客观
- 提供数据支持
- 给出明确的结论和建议
- 注意风险提示
"""

        if model_config is None:
            model_config = {
                "config_name": "gpt-4",
                "model": "gpt-4",
                "api_key": os.environ.get("OPENAI_API_KEY"),
                "stream": True,
            }

        # 初始化模型
        model = OpenAIChatModel(**model_config)
        
        # 创建工具集
        toolkit = Toolkit()
        
        # 初始化工具
        data_fetcher = FinancialDataFetcher()
        analyzer = FinancialAnalyzer()
        report_generator = ReportGenerator()
        
        # 注册工具
        toolkit.register_tool_function(data_fetcher.get_financial_statements)
        toolkit.register_tool_function(data_fetcher.get_stock_price)
        toolkit.register_tool_function(analyzer.calculate_profitability_ratios)
        toolkit.register_tool_function(analyzer.calculate_solvency_ratios)
        toolkit.register_tool_function(analyzer.calculate_efficiency_ratios)
        toolkit.register_tool_function(analyzer.trend_analysis)
        toolkit.register_tool_function(report_generator.generate_financial_report)
        toolkit.register_tool_function(report_generator.create_chart)
        
        # 初始化记忆
        memory = InMemoryMemory()
        
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            memory=memory,
            toolkit=toolkit,
            **kwargs,
        )

    async def analyze_company(
        self,
        company_code: str,
        analysis_type: str = "comprehensive",
        period: str = "annual",
        years: int = 3,
    ) -> Dict[str, Any]:
        """
        对指定公司进行财务分析
        
        Args:
            company_code: 公司代码
            analysis_type: 分析类型 (comprehensive, profitability, solvency, efficiency)
            period: 报告周期 (annual, quarterly)
            years: 分析年数
            
        Returns:
            分析结果字典
        """
        
        analysis_result = {
            "company_code": company_code,
            "analysis_type": analysis_type,
            "period": period,
            "years": years,
            "timestamp": None,
            "data": {},
            "analysis": {},
            "report": "",
            "charts": [],
        }
        
        try:
            # 1. 获取财务数据
            financial_data = await self._get_financial_data(
                company_code, period, years
            )
            analysis_result["data"] = financial_data
            
            # 2. 进行财务分析
            analysis = await self._perform_analysis(
                financial_data, analysis_type
            )
            analysis_result["analysis"] = analysis
            
            # 3. 生成报告
            report = await self._generate_report(analysis_result)
            analysis_result["report"] = report
            
            # 4. 生成图表
            charts = await self._generate_charts(analysis_result)
            analysis_result["charts"] = charts
            
        except Exception as e:
            analysis_result["error"] = str(e)
            
        return analysis_result

    async def _get_financial_data(
        self, company_code: str, period: str, years: int
    ) -> Dict[str, Any]:
        """获取财务数据"""
        # 这里会调用数据获取工具
        pass

    async def _perform_analysis(
        self, data: Dict[str, Any], analysis_type: str
    ) -> Dict[str, Any]:
        """执行财务分析"""
        # 这里会调用分析工具
        pass

    async def _generate_report(self, analysis_result: Dict[str, Any]) -> str:
        """生成分析报告"""
        # 这里会调用报告生成工具
        pass

    async def _generate_charts(self, analysis_result: Dict[str, Any]) -> List[str]:
        """生成图表"""
        # 这里会调用图表生成工具
        pass