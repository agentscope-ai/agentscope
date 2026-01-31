"""
财务分析智能体技能定义

基于Anthropic Agent Skill框架定义财务分析相关的技能
"""

import json
from typing import Dict, List, Any, Optional
from agentscope.agent import ReActAgent
from agentscope.models import ModelResponse


class FinancialAnalysisSkill:
    """
    财务分析技能
    
    提供专业的财务分析能力，包括数据获取、比率计算、风险评估等
    """

    def __init__(self):
        """初始化财务分析技能"""
        self.name = "financial_analysis"
        self.description = "专业的财务分析技能，能够获取财务数据、计算财务比率、评估投资风险"
        self.version = "1.0.0"
        
        # 技能参数定义
        self.parameters = {
            "analysis_type": {
                "type": "string",
                "description": "分析类型：profitability, solvency, efficiency, comprehensive",
                "required": True,
                "enum": ["profitability", "solvency", "efficiency", "comprehensive"]
            },
            "company_symbol": {
                "type": "string", 
                "description": "公司股票代码，如AAPL, MSFT",
                "required": True
            },
            "period": {
                "type": "string",
                "description": "分析周期：annual, quarterly",
                "required": False,
                "enum": ["annual", "quarterly"],
                "default": "annual"
            },
            "years": {
                "type": "integer",
                "description": "分析年数",
                "required": False,
                "default": 3,
                "minimum": 1,
                "maximum": 10
            }
        }

    async def execute(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行财务分析技能
        
        Args:
            parameters: 技能执行参数
            
        Returns:
            分析结果
        """
        try:
            company_symbol = parameters.get("company_symbol")
            analysis_type = parameters.get("analysis_type", "comprehensive")
            period = parameters.get("period", "annual")
            years = parameters.get("years", 3)
            
            # 这里会调用实际的财务分析工具
            from .tools.data_fetcher import FinancialDataFetcher
            from .tools.analyzer import FinancialAnalyzer
            
            # 初始化工具
            data_fetcher = FinancialDataFetcher()
            analyzer = FinancialAnalyzer()
            
            # 获取财务数据
            income_data = await data_fetcher.get_financial_statements(
                company_symbol, "income_statement", period, years
            )
            balance_data = await data_fetcher.get_financial_statements(
                company_symbol, "balance_sheet", period, years
            )
            
            if not income_data.success or not balance_data.success:
                return {
                    "success": False,
                    "error": "无法获取财务数据",
                    "details": {
                        "income_error": income_data.error if not income_data.success else None,
                        "balance_error": balance_data.error if not balance_data.success else None
                    }
                }
            
            # 合并数据
            financial_data = {
                "income_statement": income_data.content["data"],
                "balance_sheet": balance_data.content["data"]
            }
            
            # 执行分析
            if analysis_type == "profitability":
                result = await analyzer.calculate_profitability_ratios(financial_data)
            elif analysis_type == "solvency":
                result = await analyzer.calculate_solvency_ratios(financial_data)
            elif analysis_type == "efficiency":
                result = await analyzer.calculate_efficiency_ratios(financial_data)
            else:  # comprehensive
                profitability = await analyzer.calculate_profitability_ratios(financial_data)
                solvency = await analyzer.calculate_solvency_ratios(financial_data)
                efficiency = await analyzer.calculate_efficiency_ratios(financial_data)
                
                result = {
                    "success": True,
                    "analysis": {
                        "profitability": profitability.content if profitability.success else {},
                        "solvency": solvency.content if solvency.success else {},
                        "efficiency": efficiency.content if efficiency.success else {}
                    }
                }
            
            return {
                "success": True,
                "skill_name": self.name,
                "parameters": parameters,
                "result": result.content if result.success else {"error": result.error},
                "timestamp": self._get_timestamp()
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"技能执行失败: {str(e)}",
                "skill_name": self.name,
                "parameters": parameters
            }

    def _get_timestamp(self) -> str:
        """获取时间戳"""
        from datetime import datetime
        return datetime.now().isoformat()


class MarketResearchSkill:
    """
    市场研究技能
    
    提供市场数据获取、趋势分析、竞品对比等能力
    """

    def __init__(self):
        """初始化市场研究技能"""
        self.name = "market_research"
        self.description = "市场研究技能，能够获取市场数据、分析行业趋势、对比竞争对手"
        self.version = "1.0.0"
        
        self.parameters = {
            "research_type": {
                "type": "string",
                "description": "研究类型：industry, competitor, market_trends",
                "required": True,
                "enum": ["industry", "competitor", "market_trends"]
            },
            "symbols": {
                "type": "array",
                "description": "股票代码列表",
                "required": True,
                "items": {"type": "string"}
            },
            "time_period": {
                "type": "string",
                "description": "时间周期：1m, 3m, 6m, 1y",
                "required": False,
                "default": "1y"
            }
        }

    async def execute(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """执行市场研究技能"""
        try:
            research_type = parameters.get("research_type")
            symbols = parameters.get("symbols", [])
            time_period = parameters.get("time_period", "1y")
            
            from .tools.data_fetcher import FinancialDataFetcher
            data_fetcher = FinancialDataFetcher()
            
            results = []
            for symbol in symbols:
                if research_type == "competitor":
                    stock_data = await data_fetcher.get_stock_price(symbol, time_period)
                    company_info = await data_fetcher.get_company_info(symbol)
                    
                    results.append({
                        "symbol": symbol,
                        "stock_data": stock_data.content if stock_data.success else {},
                        "company_info": company_info.content if company_info.success else {}
                    })
                elif research_type == "market_trends":
                    indices_data = await data_fetcher.get_market_indices()
                    results.append({
                        "indices": indices_data.content if indices_data.success else {}
                    })
            
            return {
                "success": True,
                "skill_name": self.name,
                "research_type": research_type,
                "results": results,
                "timestamp": self._get_timestamp()
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"市场研究技能执行失败: {str(e)}",
                "skill_name": self.name,
                "parameters": parameters
            }

    def _get_timestamp(self) -> str:
        """获取时间戳"""
        from datetime import datetime
        return datetime.now().isoformat()


class RiskAssessmentSkill:
    """
    风险评估技能
    
    提供投资风险识别、评估和建议
    """

    def __init__(self):
        """初始化风险评估技能"""
        self.name = "risk_assessment"
        self.description = "风险评估技能，能够识别投资风险、计算风险指标、提供风险建议"
        self.version = "1.0.0"
        
        self.parameters = {
            "assessment_type": {
                "type": "string",
                "description": "评估类型：financial, market, operational",
                "required": True,
                "enum": ["financial", "market", "operational"]
            },
            "company_symbol": {
                "type": "string",
                "description": "公司股票代码",
                "required": True
            },
            "risk_tolerance": {
                "type": "string",
                "description": "风险承受能力：low, medium, high",
                "required": False,
                "enum": ["low", "medium", "high"],
                "default": "medium"
            }
        }

    async def execute(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """执行风险评估技能"""
        try:
            assessment_type = parameters.get("assessment_type")
            company_symbol = parameters.get("company_symbol")
            risk_tolerance = parameters.get("risk_tolerance", "medium")
            
            # 实现风险评估逻辑
            risk_scores = self._calculate_risk_scores(company_symbol, assessment_type)
            risk_level = self._determine_risk_level(risk_scores, risk_tolerance)
            recommendations = self._generate_risk_recommendations(risk_level, assessment_type)
            
            return {
                "success": True,
                "skill_name": self.name,
                "assessment_type": assessment_type,
                "company_symbol": company_symbol,
                "risk_tolerance": risk_tolerance,
                "risk_scores": risk_scores,
                "risk_level": risk_level,
                "recommendations": recommendations,
                "timestamp": self._get_timestamp()
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"风险评估技能执行失败: {str(e)}",
                "skill_name": self.name,
                "parameters": parameters
            }

    def _calculate_risk_scores(self, symbol: str, assessment_type: str) -> Dict[str, float]:
        """计算风险分数"""
        # 简化实现，实际应该基于真实数据计算
        base_scores = {
            "financial": {"liquidity": 0.3, "leverage": 0.6, "profitability": 0.2},
            "market": {"volatility": 0.7, "beta": 0.5, "correlation": 0.3},
            "operational": {"management": 0.2, "industry": 0.4, "regulatory": 0.3}
        }
        return base_scores.get(assessment_type, {})

    def _determine_risk_level(self, scores: Dict[str, float], tolerance: str) -> str:
        """确定风险等级"""
        avg_score = sum(scores.values()) / len(scores) if scores else 0.5
        
        if avg_score > 0.7:
            base_level = "high"
        elif avg_score > 0.4:
            base_level = "medium"
        else:
            base_level = "low"
        
        # 根据风险承受能力调整
        if tolerance == "high":
            return base_level
        elif tolerance == "low":
            if base_level == "low":
                return "low"
            elif base_level == "medium":
                return "high"
            else:
                return "very_high"
        else:  # medium
            return base_level

    def _generate_risk_recommendations(self, risk_level: str, assessment_type: str) -> List[str]:
        """生成风险建议"""
        recommendations = {
            "financial": {
                "low": ["可考虑增加仓位", "维持现有投资策略"],
                "medium": ["适度控制仓位", "设置止损点"],
                "high": ["减少仓位", "密切监控财务指标", "考虑分散投资"]
            },
            "market": {
                "low": ["可适当增加投资", "关注市场机会"],
                "medium": ["保持谨慎乐观", "定期评估"],
                "high": ["减少投资", "等待更好时机", "考虑对冲"]
            },
            "operational": {
                "low": ["可长期持有", "关注公司发展"],
                "medium": ["定期跟踪", "关注管理层变动"],
                "high": ["谨慎投资", "深入了解运营风险", "考虑替代投资"]
            }
        }
        
        return recommendations.get(assessment_type, {}).get(risk_level, ["建议进一步研究"])

    def _get_timestamp(self) -> str:
        """获取时间戳"""
        from datetime import datetime
        return datetime.now().isoformat()


# 技能注册表
SKILL_REGISTRY = {
    "financial_analysis": FinancialAnalysisSkill,
    "market_research": MarketResearchSkill,
    "risk_assessment": RiskAssessmentSkill,
}


def get_skill(skill_name: str) -> Optional[Any]:
    """获取技能实例"""
    skill_class = SKILL_REGISTRY.get(skill_name)
    if skill_class:
        return skill_class()
    return None


def list_skills() -> List[Dict[str, str]]:
    """列出所有可用技能"""
    skills = []
    for name, skill_class in SKILL_REGISTRY.items():
        skill_instance = skill_class()
        skills.append({
            "name": skill_instance.name,
            "description": skill_instance.description,
            "version": skill_instance.version
        })
    return skills