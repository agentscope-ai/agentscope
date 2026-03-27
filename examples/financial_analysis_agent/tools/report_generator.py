"""
财务报告生成器

提供财务分析报告的生成功能，包括：
- 文本报告生成
- 图表生成
- HTML报告生成
- PDF报告导出
"""

import os
import json
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
from typing import Dict, List, Optional, Any
from datetime import datetime
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

from agentscope.tools.tool_response import ToolResponse

# 设置中文字体支持
plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


class ReportGenerator:
    """
    财务报告生成器
    
    生成各种格式的财务分析报告
    """

    def __init__(self, output_dir: str = None):
        """
        初始化报告生成器
        
        Args:
            output_dir: 报告输出目录
        """
        if output_dir is None:
            output_dir = os.path.join(os.path.dirname(__file__), "..", "reports")
        
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        # 设置图表样式
        plt.style.use('seaborn-v0_8')
        sns.set_palette("husl")

    async def generate_financial_report(
        self,
        analysis_result: Dict[str, Any],
        report_type: str = "comprehensive",
        format_type: str = "html",
    ) -> ToolResponse:
        """
        生成财务分析报告
        
        Args:
            analysis_result: 分析结果数据
            report_type: 报告类型 (comprehensive, summary, detailed)
            format_type: 报告格式 (html, markdown, json)
            
        Returns:
            报告生成结果
        """
        try:
            # 生成报告内容
            if report_type == "comprehensive":
                content = self._generate_comprehensive_report(analysis_result)
            elif report_type == "summary":
                content = self._generate_summary_report(analysis_result)
            elif report_type == "detailed":
                content = self._generate_detailed_report(analysis_result)
            else:
                return ToolResponse.error(f"不支持的报告类型: {report_type}")
            
            # 保存报告
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            company_code = analysis_result.get("company_code", "unknown")
            
            if format_type == "html":
                filename = f"financial_report_{company_code}_{timestamp}.html"
                filepath = os.path.join(self.output_dir, filename)
                await self._save_html_report(content, filepath)
            elif format_type == "markdown":
                filename = f"financial_report_{company_code}_{timestamp}.md"
                filepath = os.path.join(self.output_dir, filename)
                await self._save_markdown_report(content, filepath)
            elif format_type == "json":
                filename = f"financial_report_{company_code}_{timestamp}.json"
                filepath = os.path.join(self.output_dir, filename)
                await self._save_json_report(content, filepath)
            else:
                return ToolResponse.error(f"不支持的格式类型: {format_type}")
            
            result = {
                "report_type": report_type,
                "format_type": format_type,
                "filename": filename,
                "filepath": filepath,
                "generated_at": datetime.now().isoformat(),
                "content": content if format_type == "json" else None,
            }
            
            return ToolResponse.success(result)
            
        except Exception as e:
            return ToolResponse.error(f"生成财务报告失败: {str(e)}")

    async def create_chart(
        self,
        data: Dict[str, Any],
        chart_type: str = "line",
        title: str = "财务分析图表",
        save_path: str = None,
    ) -> ToolResponse:
        """
        创建财务分析图表
        
        Args:
            data: 图表数据
            chart_type: 图表类型 (line, bar, pie, scatter, heatmap)
            title: 图表标题
            save_path: 保存路径
            
        Returns:
            图表生成结果
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            if save_path is None:
                save_path = os.path.join(
                    self.output_dir, f"chart_{timestamp}.png"
                )
            
            # 根据图表类型创建图表
            if chart_type == "line":
                fig = self._create_line_chart(data, title)
            elif chart_type == "bar":
                fig = self._create_bar_chart(data, title)
            elif chart_type == "pie":
                fig = self._create_pie_chart(data, title)
            elif chart_type == "scatter":
                fig = self._create_scatter_chart(data, title)
            elif chart_type == "heatmap":
                fig = self._create_heatmap(data, title)
            else:
                return ToolResponse.error(f"不支持的图表类型: {chart_type}")
            
            # 保存图表
            fig.write_image(save_path, width=1000, height=600)
            
            result = {
                "chart_type": chart_type,
                "title": title,
                "filepath": save_path,
                "created_at": datetime.now().isoformat(),
            }
            
            return ToolResponse.success(result)
            
        except Exception as e:
            return ToolResponse.error(f"创建图表失败: {str(e)}")

    def _generate_comprehensive_report(self, analysis_result: Dict[str, Any]) -> str:
        """生成综合报告"""
        company_code = analysis_result.get("company_code", "N/A")
        analysis_data = analysis_result.get("analysis", {})
        data = analysis_result.get("data", {})
        
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>财务分析报告 - {company_code}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; line-height: 1.6; }}
        .header {{ background-color: #f0f0f0; padding: 20px; border-radius: 5px; }}
        .section {{ margin: 20px 0; padding: 15px; border: 1px solid #ddd; border-radius: 5px; }}
        .metric {{ display: inline-block; margin: 10px; padding: 10px; background-color: #f9f9f9; border-radius: 3px; }}
        .positive {{ color: green; }}
        .negative {{ color: red; }}
        .neutral {{ color: blue; }}
        table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>财务分析报告</h1>
        <h2>公司代码: {company_code}</h2>
        <p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>
"""
        
        # 盈利能力分析
        if "profitability" in analysis_data:
            profitability = analysis_data["profitability"]
            html_content += f"""
    <div class="section">
        <h3>盈利能力分析</h3>
        {self._format_profitability_section(profitability)}
    </div>
"""
        
        # 偿债能力分析
        if "solvency" in analysis_data:
            solvency = analysis_data["solvency"]
            html_content += f"""
    <div class="section">
        <h3>偿债能力分析</h3>
        {self._format_solvency_section(solvency)}
    </div>
"""
        
        # 运营效率分析
        if "efficiency" in analysis_data:
            efficiency = analysis_data["efficiency"]
            html_content += f"""
    <div class="section">
        <h3>运营效率分析</h3>
        {self._format_efficiency_section(efficiency)}
    </div>
"""
        
        # 趋势分析
        if "trend_analysis" in analysis_data:
            trend = analysis_data["trend_analysis"]
            html_content += f"""
    <div class="section">
        <h3>趋势分析</h3>
        {self._format_trend_section(trend)}
    </div>
"""
        
        # 总结和建议
        html_content += f"""
    <div class="section">
        <h3>总结与建议</h3>
        {self._generate_summary_and_recommendations(analysis_result)}
    </div>
</body>
</html>
"""
        
        return html_content

    def _generate_summary_report(self, analysis_result: Dict[str, Any]) -> str:
        """生成摘要报告"""
        company_code = analysis_result.get("company_code", "N/A")
        
        summary = f"""
# 财务分析摘要报告

**公司代码**: {company_code}
**报告时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 核心财务指标
{self._extract_key_metrics(analysis_result)}

## 总体评价
{self._generate_overall_assessment(analysis_result)}

## 主要风险点
{self._identify_key_risks(analysis_result)}

## 投资建议
{self._generate_investment_recommendations(analysis_result)}
"""
        
        return summary

    def _generate_detailed_report(self, analysis_result: Dict[str, Any]) -> Dict[str, Any]:
        """生成详细报告（JSON格式）"""
        return {
            "metadata": {
                "company_code": analysis_result.get("company_code"),
                "report_type": "detailed",
                "generated_at": datetime.now().isoformat(),
            },
            "raw_data": analysis_result.get("data", {}),
            "analysis_results": analysis_result.get("analysis", {}),
            "trend_analysis": analysis_result.get("trend_analysis", {}),
            "risk_assessment": self._assess_risks(analysis_result),
            "recommendations": self._generate_detailed_recommendations(analysis_result),
        }

    def _format_profitability_section(self, profitability: Dict[str, Any]) -> str:
        """格式化盈利能力部分"""
        ratios = profitability.get("ratios", {})
        analysis = profitability.get("analysis", {})
        
        html = "<div>"
        
        # 显示关键比率
        for ratio_name, value in ratios.items():
            analysis_text = analysis.get(ratio_name, "")
            status = self._get_ratio_status(ratio_name, value)
            html += f"""
            <div class="metric {status}">
                <strong>{self._translate_ratio_name(ratio_name)}</strong>: {value:.2%}
                <br><small>{analysis_text}</small>
            </div>
            """
        
        html += "</div>"
        return html

    def _format_solvency_section(self, solvency: Dict[str, Any]) -> str:
        """格式化偿债能力部分"""
        ratios = solvency.get("ratios", {})
        analysis = solvency.get("analysis", {})
        
        html = "<div>"
        
        for ratio_name, value in ratios.items():
            analysis_text = analysis.get(ratio_name, "")
            status = self._get_ratio_status(ratio_name, value)
            html += f"""
            <div class="metric {status}">
                <strong>{self._translate_ratio_name(ratio_name)}</strong>: {value:.2f}
                <br><small>{analysis_text}</small>
            </div>
            """
        
        html += "</div>"
        return html

    def _format_efficiency_section(self, efficiency: Dict[str, Any]) -> str:
        """格式化运营效率部分"""
        ratios = efficiency.get("ratios", {})
        analysis = efficiency.get("analysis", {})
        
        html = "<div>"
        
        for ratio_name, value in ratios.items():
            analysis_text = analysis.get(ratio_name, "")
            status = self._get_ratio_status(ratio_name, value)
            html += f"""
            <div class="metric {status}">
                <strong>{self._translate_ratio_name(ratio_name)}</strong>: {value:.2f}
                <br><small>{analysis_text}</small>
            </div>
            """
        
        html += "</div>"
        return html

    def _format_trend_section(self, trend_data: Dict[str, Any]) -> str:
        """格式化趋势分析部分"""
        html = "<div>"
        
        for metric, analysis in trend_data.items():
            if isinstance(analysis, dict):
                status = analysis.get("status", "无数据")
                growth = analysis.get("avg_growth_rate", 0)
                html += f"""
                <div class="metric">
                    <strong>{metric}</strong>: {status}
                    <br><small>平均增长率: {growth:.2%}</small>
                </div>
                """
        
        html += "</div>"
        return html

    def _translate_ratio_name(self, ratio_name: str) -> str:
        """翻译财务比率名称"""
        translations = {
            "gross_margin": "毛利率",
            "net_margin": "净利率",
            "roe": "净资产收益率(ROE)",
            "roa": "总资产收益率(ROA)",
            "debt_to_equity": "负债权益比",
            "current_ratio": "流动比率",
            "quick_ratio": "速动比率",
            "asset_turnover": "资产周转率",
            "inventory_turnover": "存货周转率",
            "operating_margin": "营业利润率",
        }
        return translations.get(ratio_name, ratio_name)

    def _get_ratio_status(self, ratio_name: str, value: float) -> str:
        """获取比率状态"""
        # 根据比率的含义和数值确定状态
        positive_ratios = ["gross_margin", "net_margin", "roe", "roa", "asset_turnover"]
        
        if ratio_name in positive_ratios:
            if value > 0.15:
                return "positive"
            elif value > 0.08:
                return "neutral"
            else:
                return "negative"
        else:
            # 对于负债类比率，数值越低越好
            if ratio_name == "debt_to_equity":
                if value < 0.5:
                    return "positive"
                elif value < 1.0:
                    return "neutral"
                else:
                    return "negative"
            elif ratio_name in ["current_ratio", "quick_ratio"]:
                if value >= 2.0:
                    return "positive"
                elif value >= 1.0:
                    return "neutral"
                else:
                    return "negative"
        
        return "neutral"

    def _create_line_chart(self, data: Dict[str, Any], title: str) -> go.Figure:
        """创建折线图"""
        fig = go.Figure()
        
        # 假设数据格式为 {"years": [...], "values": [...], "series": "series_name"}
        years = data.get("years", [])
        values = data.get("values", [])
        series_name = data.get("series", "数值")
        
        fig.add_trace(go.Scatter(
            x=years,
            y=values,
            mode='lines+markers',
            name=series_name,
            line=dict(width=2),
            marker=dict(size=8)
        ))
        
        fig.update_layout(
            title=title,
            xaxis_title="年份",
            yaxis_title="数值",
            hovermode='x unified'
        )
        
        return fig

    def _create_bar_chart(self, data: Dict[str, Any], title: str) -> go.Figure:
        """创建柱状图"""
        categories = list(data.keys())
        values = list(data.values())
        
        fig = go.Figure(data=[
            go.Bar(x=categories, y=values, marker_color='skyblue')
        ])
        
        fig.update_layout(
            title=title,
            xaxis_title="类别",
            yaxis_title="数值"
        )
        
        return fig

    def _create_pie_chart(self, data: Dict[str, Any], title: str) -> go.Figure:
        """创建饼图"""
        labels = list(data.keys())
        values = list(data.values())
        
        fig = go.Figure(data=[go.Pie(labels=labels, values=values, hole=0.3)])
        
        fig.update_layout(title=title)
        
        return fig

    def _create_scatter_chart(self, data: Dict[str, Any], title: str) -> go.Figure:
        """创建散点图"""
        x_values = data.get("x", [])
        y_values = data.get("y", [])
        
        fig = go.Figure(data=[
            go.Scatter(x=x_values, y=y_values, mode='markers')
        ])
        
        fig.update_layout(
            title=title,
            xaxis_title="X轴",
            yaxis_title="Y轴"
        )
        
        return fig

    def _create_heatmap(self, data: Dict[str, Any], title: str) -> go.Figure:
        """创建热力图"""
        # 假设数据是相关矩阵
        z_values = data.get("z", [])
        x_labels = data.get("x_labels", [])
        y_labels = data.get("y_labels", [])
        
        fig = go.Figure(data=go.Heatmap(
            z=z_values,
            x=x_labels,
            y=y_labels,
            colorscale='RdYlBu'
        ))
        
        fig.update_layout(title=title)
        
        return fig

    async def _save_html_report(self, content: str, filepath: str) -> None:
        """保存HTML报告"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

    async def _save_markdown_report(self, content: str, filepath: str) -> None:
        """保存Markdown报告"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

    async def _save_json_report(self, content: Dict[str, Any], filepath: str) -> None:
        """保存JSON报告"""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(content, f, ensure_ascii=False, indent=2)

    def _extract_key_metrics(self, analysis_result: Dict[str, Any]) -> str:
        """提取关键指标"""
        # 实现关键指标提取逻辑
        return "- 关键指标1\n- 关键指标2\n- 关键指标3"

    def _generate_overall_assessment(self, analysis_result: Dict[str, Any]) -> str:
        """生成总体评价"""
        return "基于财务分析结果，该公司整体表现..."

    def _identify_key_risks(self, analysis_result: Dict[str, Any]) -> str:
        """识别关键风险"""
        return "- 风险点1\n- 风险点2\n- 风险点3"

    def _generate_investment_recommendations(self, analysis_result: Dict[str, Any]) -> str:
        """生成投资建议"""
        return "- 建议内容1\n- 建议内容2\n- 建议内容3"

    def _generate_summary_and_recommendations(self, analysis_result: Dict[str, Any]) -> str:
        """生成总结与建议"""
        return """
        <h4>总体评价</h4>
        <p>基于综合财务分析，该公司在各方面表现...</p>
        
        <h4>主要优势</h4>
        <ul>
            <li>优势1</li>
            <li>优势2</li>
        </ul>
        
        <h4>风险提示</h4>
        <ul>
            <li>风险1</li>
            <li>风险2</li>
        </ul>
        
        <h4>投资建议</h4>
        <p>根据分析结果，建议投资者...</p>
        """

    def _assess_risks(self, analysis_result: Dict[str, Any]) -> Dict[str, Any]:
        """评估风险"""
        return {
            "financial_risk": "低",
            "operational_risk": "中",
            "market_risk": "高",
            "overall_risk": "中"
        }

    def _generate_detailed_recommendations(self, analysis_result: Dict[str, Any]) -> List[str]:
        """生成详细建议"""
        return [
            "建议1：提高盈利能力",
            "建议2：优化资本结构",
            "建议3：加强风险管理"
        ]