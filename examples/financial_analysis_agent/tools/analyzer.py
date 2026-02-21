"""
财务分析计算工具

提供各种财务比率计算和分析功能，包括：
- 盈利能力分析
- 偿债能力分析
- 运营效率分析
- 成长性分析
- 趋势分析
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
import warnings

from agentscope.tools.tool_response import ToolResponse

warnings.filterwarnings('ignore')


class FinancialAnalyzer:
    """
    财务分析器
    
    提供全面的财务分析功能，包括各种财务比率的计算和解释
    """

    def __init__(self):
        """初始化财务分析器"""
        self.industry_benchmarks = {
            "technology": {
                "gross_margin": 0.65,
                "net_margin": 0.15,
                "roe": 0.18,
                "debt_to_equity": 0.3,
            },
            "manufacturing": {
                "gross_margin": 0.35,
                "net_margin": 0.08,
                "roe": 0.12,
                "debt_to_equity": 0.6,
            },
            "retail": {
                "gross_margin": 0.25,
                "net_margin": 0.05,
                "roe": 0.15,
                "debt_to_equity": 0.8,
            },
        }

    async def calculate_profitability_ratios(
        self, financial_data: Dict[str, Any]
    ) -> ToolResponse:
        """
        计算盈利能力比率
        
        Args:
            financial_data: 财务数据，包含收入、成本、利润等
            
        Returns:
            盈利能力分析结果
        """
        try:
            income_statement = financial_data.get("income_statement", {})
            balance_sheet = financial_data.get("balance_sheet", {})
            
            if not income_statement:
                return ToolResponse.error("缺少利润表数据")
            
            # 提取关键数据
            revenue = self._get_latest_value(income_statement, "Revenue")
            cost_of_revenue = self._get_latest_value(income_statement, "Cost of Revenue")
            gross_profit = self._get_latest_value(income_statement, "Gross Profit")
            operating_income = self._get_latest_value(income_statement, "Operating Income")
            net_income = self._get_latest_value(income_statement, "Net Income")
            
            # 计算盈利能力比率
            ratios = {}
            
            if revenue and revenue > 0:
                if gross_profit:
                    ratios["gross_margin"] = gross_profit / revenue
                if operating_income:
                    ratios["operating_margin"] = operating_income / revenue
                if net_income:
                    ratios["net_margin"] = net_income / revenue
                ratios["revenue_growth"] = self._calculate_growth_rate(
                    income_statement, "Revenue"
                )
            
            # ROE计算
            shareholders_equity = self._get_latest_value(
                balance_sheet, "Shareholders' Equity"
            )
            if net_income and shareholders_equity and shareholders_equity > 0:
                ratios["roe"] = net_income / shareholders_equity
            
            # ROA计算
            total_assets = self._get_latest_value(balance_sheet, "Total Assets")
            if net_income and total_assets and total_assets > 0:
                ratios["roa"] = net_income / total_assets
            
            # 分析和评级
            analysis = self._analyze_profitability(ratios)
            
            result = {
                "ratios": ratios,
                "analysis": analysis,
                "benchmark_comparison": self._compare_with_benchmarks(
                    ratios, "technology"
                ),
                "calculated_at": datetime.now().isoformat(),
            }
            
            return ToolResponse.success(result)
            
        except Exception as e:
            return ToolResponse.error(f"计算盈利能力比率失败: {str(e)}")

    async def calculate_solvency_ratios(
        self, financial_data: Dict[str, Any]
    ) -> ToolResponse:
        """
        计算偿债能力比率
        
        Args:
            financial_data: 财务数据，包含资产、负债等
            
        Returns:
            偿债能力分析结果
        """
        try:
            balance_sheet = financial_data.get("balance_sheet", {})
            income_statement = financial_data.get("income_statement", {})
            
            if not balance_sheet:
                return ToolResponse.error("缺少资产负债表数据")
            
            # 提取关键数据
            total_assets = self._get_latest_value(balance_sheet, "Total Assets")
            current_assets = self._get_latest_value(balance_sheet, "Current Assets")
            total_liabilities = self._get_latest_value(balance_sheet, "Total Liabilities")
            current_liabilities = self._get_latest_value(balance_sheet, "Current Liabilities")
            
            # 计算偿债能力比率
            ratios = {}
            
            if total_assets and total_liabilities and total_assets > 0:
                ratios["debt_to_assets"] = total_liabilities / total_assets
            
            shareholders_equity = self._get_latest_value(
                balance_sheet, "Shareholders' Equity"
            )
            if total_liabilities and shareholders_equity and shareholders_equity > 0:
                ratios["debt_to_equity"] = total_liabilities / shareholders_equity
            
            if current_assets and current_liabilities and current_liabilities > 0:
                ratios["current_ratio"] = current_assets / current_liabilities
            
            # 速动比率（假设存货是流动资产的一部分）
            inventory = self._get_latest_value(balance_sheet, "Inventory")
            if current_assets and inventory and current_liabilities and current_liabilities > 0:
                quick_assets = current_assets - inventory
                ratios["quick_ratio"] = quick_assets / current_liabilities
            
            # 利息保障倍数
            operating_income = self._get_latest_value(income_statement, "Operating Income")
            interest_expense = self._get_latest_value(income_statement, "Interest Expense")
            if operating_income and interest_expense and interest_expense > 0:
                ratios["interest_coverage"] = operating_income / interest_expense
            
            # 分析和评级
            analysis = self._analyze_solvency(ratios)
            
            result = {
                "ratios": ratios,
                "analysis": analysis,
                "benchmark_comparison": self._compare_with_benchmarks(
                    ratios, "technology"
                ),
                "calculated_at": datetime.now().isoformat(),
            }
            
            return ToolResponse.success(result)
            
        except Exception as e:
            return ToolResponse.error(f"计算偿债能力比率失败: {str(e)}")

    async def calculate_efficiency_ratios(
        self, financial_data: Dict[str, Any]
    ) -> ToolResponse:
        """
        计算运营效率比率
        
        Args:
            financial_data: 财务数据
            
        Returns:
            运营效率分析结果
        """
        try:
            income_statement = financial_data.get("income_statement", {})
            balance_sheet = financial_data.get("balance_sheet", {})
            
            if not income_statement or not balance_sheet:
                return ToolResponse.error("缺少财务数据")
            
            # 提取关键数据
            revenue = self._get_latest_value(income_statement, "Revenue")
            cost_of_revenue = self._get_latest_value(income_statement, "Cost of Revenue")
            
            # 平均资产计算（简单使用最新值）
            total_assets = self._get_latest_value(balance_sheet, "Total Assets")
            inventory = self._get_latest_value(balance_sheet, "Inventory")
            current_assets = self._get_latest_value(balance_sheet, "Current Assets")
            current_liabilities = self._get_latest_value(balance_sheet, "Current Liabilities")
            
            # 计算运营效率比率
            ratios = {}
            
            # 资产周转率
            if revenue and total_assets and total_assets > 0:
                ratios["asset_turnover"] = revenue / total_assets
            
            # 存货周转率
            if cost_of_revenue and inventory and inventory > 0:
                ratios["inventory_turnover"] = cost_of_revenue / inventory
                if ratios["inventory_turnover"] > 0:
                    ratios["days_inventory"] = 365 / ratios["inventory_turnover"]
            
            # 应收账款周转率（假设有应收账款数据）
            accounts_receivable = self._get_latest_value(balance_sheet, "Accounts Receivable")
            if revenue and accounts_receivable and accounts_receivable > 0:
                ratios["receivables_turnover"] = revenue / accounts_receivable
                if ratios["receivables_turnover"] > 0:
                    ratios["days_sales_outstanding"] = 365 / ratios["receivables_turnover"]
            
            # 运营周期
            if "days_inventory" in ratios and "days_sales_outstanding" in ratios:
                ratios["operating_cycle"] = (
                    ratios["days_inventory"] + ratios["days_sales_outstanding"]
                )
            
            # 分析和评级
            analysis = self._analyze_efficiency(ratios)
            
            result = {
                "ratios": ratios,
                "analysis": analysis,
                "benchmark_comparison": self._compare_with_benchmarks(
                    ratios, "technology"
                ),
                "calculated_at": datetime.now().isoformat(),
            }
            
            return ToolResponse.success(result)
            
        except Exception as e:
            return ToolResponse.error(f"计算运营效率比率失败: {str(e)}")

    async def trend_analysis(
        self, historical_data: Dict[str, Any], years: int = 3
    ) -> ToolResponse:
        """
        趋势分析
        
        Args:
            historical_data: 历史财务数据
            years: 分析年数
            
        Returns:
            趋势分析结果
        """
        try:
            trend_results = {}
            
            # 收入趋势
            revenue_data = self._extract_trend_data(historical_data, "Revenue")
            if revenue_data:
                trend_results["revenue"] = self._analyze_trend(revenue_data, "收入")
            
            # 净利润趋势
            net_income_data = self._extract_trend_data(historical_data, "Net Income")
            if net_income_data:
                trend_results["net_income"] = self._analyze_trend(net_income_data, "净利润")
            
            # 资产趋势
            assets_data = self._extract_trend_data(historical_data, "Total Assets")
            if assets_data:
                trend_results["assets"] = self._analyze_trend(assets_data, "总资产")
            
            # 负债趋势
            liabilities_data = self._extract_trend_data(historical_data, "Total Liabilities")
            if liabilities_data:
                trend_results["liabilities"] = self._analyze_trend(liabilities_data, "总负债")
            
            # 整体趋势分析
            overall_analysis = self._overall_trend_analysis(trend_results)
            
            result = {
                "trend_analysis": trend_results,
                "overall_analysis": overall_analysis,
                "years_analyzed": years,
                "calculated_at": datetime.now().isoformat(),
            }
            
            return ToolResponse.success(result)
            
        except Exception as e:
            return ToolResponse.error(f"趋势分析失败: {str(e)}")

    def _get_latest_value(self, data: Dict, key: str) -> Optional[float]:
        """获取数据的最新值"""
        if key in data and isinstance(data[key], dict):
            values = list(data[key].values())
            # 过滤非数值
            numeric_values = [v for v in values if isinstance(v, (int, float)) and v > 0]
            return numeric_values[0] if numeric_values else None
        return None

    def _calculate_growth_rate(self, data: Dict, key: str) -> Optional[float]:
        """计算增长率"""
        if key in data and isinstance(data[key], dict):
            years = list(data[key].keys())
            if len(years) >= 2:
                try:
                    current = data[key][years[0]]
                    previous = data[key][years[1]]
                    if current and previous and previous > 0:
                        return (current - previous) / previous
                except (KeyError, TypeError, ZeroDivisionError):
                    pass
        return None

    def _analyze_profitability(self, ratios: Dict[str, float]) -> Dict[str, str]:
        """分析盈利能力"""
        analysis = {}
        
        if "gross_margin" in ratios:
            gm = ratios["gross_margin"]
            if gm >= 0.5:
                analysis["gross_margin"] = "优秀 - 毛利率很高"
            elif gm >= 0.3:
                analysis["gross_margin"] = "良好 - 毛利率较高"
            elif gm >= 0.15:
                analysis["gross_margin"] = "一般 - 毛利率适中"
            else:
                analysis["gross_margin"] = "较差 - 毛利率较低"
        
        if "net_margin" in ratios:
            nm = ratios["net_margin"]
            if nm >= 0.15:
                analysis["net_margin"] = "优秀 - 净利率很高"
            elif nm >= 0.08:
                analysis["net_margin"] = "良好 - 净利率较高"
            elif nm >= 0.03:
                analysis["net_margin"] = "一般 - 净利率适中"
            else:
                analysis["net_margin"] = "较差 - 净利率较低"
        
        if "roe" in ratios:
            roe = ratios["roe"]
            if roe >= 0.15:
                analysis["roe"] = "优秀 - ROE很高"
            elif roe >= 0.10:
                analysis["roe"] = "良好 - ROE较高"
            elif roe >= 0.05:
                analysis["roe"] = "一般 - ROE适中"
            else:
                analysis["roe"] = "较差 - ROE较低"
        
        return analysis

    def _analyze_solvency(self, ratios: Dict[str, float]) -> Dict[str, str]:
        """分析偿债能力"""
        analysis = {}
        
        if "debt_to_equity" in ratios:
            de = ratios["debt_to_equity"]
            if de <= 0.3:
                analysis["debt_to_equity"] = "优秀 - 负债率很低"
            elif de <= 0.6:
                analysis["debt_to_equity"] = "良好 - 负债率适中"
            elif de <= 1.0:
                analysis["debt_to_equity"] = "一般 - 负债率较高"
            else:
                analysis["debt_to_equity"] = "较差 - 负债率过高"
        
        if "current_ratio" in ratios:
            cr = ratios["current_ratio"]
            if cr >= 2.0:
                analysis["current_ratio"] = "优秀 - 流动性很强"
            elif cr >= 1.5:
                analysis["current_ratio"] = "良好 - 流动性较好"
            elif cr >= 1.0:
                analysis["current_ratio"] = "一般 - 流动性适中"
            else:
                analysis["current_ratio"] = "较差 - 流动性不足"
        
        return analysis

    def _analyze_efficiency(self, ratios: Dict[str, float]) -> Dict[str, str]:
        """分析运营效率"""
        analysis = {}
        
        if "asset_turnover" in ratios:
            at = ratios["asset_turnover"]
            if at >= 1.5:
                analysis["asset_turnover"] = "优秀 - 资产利用率很高"
            elif at >= 1.0:
                analysis["asset_turnover"] = "良好 - 资产利用率较高"
            elif at >= 0.5:
                analysis["asset_turnover"] = "一般 - 资产利用率适中"
            else:
                analysis["asset_turnover"] = "较差 - 资产利用率较低"
        
        if "inventory_turnover" in ratios:
            it = ratios["inventory_turnover"]
            if it >= 6:
                analysis["inventory_turnover"] = "优秀 - 存货周转很快"
            elif it >= 4:
                analysis["inventory_turnover"] = "良好 - 存货周转较快"
            elif it >= 2:
                analysis["inventory_turnover"] = "一般 - 存货周转适中"
            else:
                analysis["inventory_turnover"] = "较差 - 存货周转较慢"
        
        return analysis

    def _compare_with_benchmarks(
        self, ratios: Dict[str, float], industry: str
    ) -> Dict[str, str]:
        """与行业基准比较"""
        if industry not in self.industry_benchmarks:
            return {"message": f"暂无{industry}行业基准数据"}
        
        benchmark = self.industry_benchmarks[industry]
        comparison = {}
        
        for ratio, value in ratios.items():
            if ratio in benchmark:
                bench_value = benchmark[ratio]
                if value >= bench_value * 1.2:
                    comparison[ratio] = "远超行业平均"
                elif value >= bench_value * 0.8:
                    comparison[ratio] = "接近行业平均"
                elif value >= bench_value * 0.5:
                    comparison[ratio] = "低于行业平均"
                else:
                    comparison[ratio] = "远低于行业平均"
        
        return comparison

    def _extract_trend_data(self, data: Dict, key: str) -> List[Tuple[str, float]]:
        """提取趋势数据"""
        trend_data = []
        
        # 从不同的财务报表中提取数据
        for sheet_name, sheet_data in data.items():
            if isinstance(sheet_data, dict) and key in sheet_data:
                for year, value in sheet_data[key].items():
                    if isinstance(value, (int, float)):
                        trend_data.append((str(year), value))
        
        # 排序并去重
        trend_data = list(set(trend_data))
        trend_data.sort(key=lambda x: x[0], reverse=True)
        
        return trend_data

    def _analyze_trend(self, data: List[Tuple[str, float]], metric_name: str) -> Dict:
        """分析单个指标的趋势"""
        if len(data) < 2:
            return {"status": "数据不足", "message": f"{metric_name}数据点太少"}
        
        # 计算增长率
        growth_rates = []
        for i in range(len(data) - 1):
            current = data[i][1]
            previous = data[i + 1][1]
            if previous > 0:
                growth_rate = (current - previous) / previous
                growth_rates.append(growth_rate)
        
        if not growth_rates:
            return {"status": "无法计算", "message": f"{metric_name}无法计算增长率"}
        
        avg_growth = np.mean(growth_rates)
        
        # 判断趋势
        if avg_growth >= 0.1:
            trend_status = "强劲增长"
        elif avg_growth >= 0.05:
            trend_status = "稳定增长"
        elif avg_growth >= 0:
            trend_status = "缓慢增长"
        elif avg_growth >= -0.05:
            trend_status = "轻微下降"
        else:
            trend_status = "明显下降"
        
        return {
            "status": trend_status,
            "avg_growth_rate": avg_growth,
            "data_points": data,
            "growth_rates": growth_rates,
        }

    def _overall_trend_analysis(self, trend_results: Dict) -> str:
        """整体趋势分析"""
        if not trend_results:
            return "无足够数据进行趋势分析"
        
        positive_count = 0
        negative_count = 0
        
        for metric, analysis in trend_results.items():
            if isinstance(analysis, dict):
                status = analysis.get("status", "")
                if "增长" in status:
                    positive_count += 1
                elif "下降" in status:
                    negative_count += 1
        
        total_metrics = len(trend_results)
        
        if positive_count / total_metrics >= 0.7:
            return "整体呈现强劲增长趋势"
        elif positive_count / total_metrics >= 0.5:
            return "整体呈现稳定增长趋势"
        elif negative_count / total_metrics >= 0.7:
            return "整体呈现下降趋势"
        else:
            return "整体趋势波动较大，需要进一步分析"