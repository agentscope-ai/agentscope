"""
财务数据获取工具

提供从各种数据源获取财务数据的功能，包括：
- 财务报表数据
- 股价数据
- 市场数据
- 宏观经济数据
"""

import asyncio
import json
import os
import aiohttp
from typing import Dict, List, Optional, Any, Union
from datetime import datetime, timedelta
import pandas as pd

try:
    from agentscope.tools import ToolResponse
except ImportError:
    # Fallback for local development
    from typing import List, Dict, Any
    
    class ToolResponse:
        def __init__(self, content=None, error=None):
            self.content = content
            self.error = error
        
        @property
        def success(self):
            return self.error is None
        
        @staticmethod
        def success(content):
            return ToolResponse(content=content)
        
        @staticmethod
        def error(error):
            return ToolResponse(error=error)

try:
    import yfinance as yf
except ImportError:
    yf = None


async def get_financial_statements(
    symbol: str,
    statement_type: str = "income_statement",
    period: str = "annual",
    years: int = 3,
) -> ToolResponse:
    """
    获取财务报表数据
    
    Args:
        symbol: 股票代码
        statement_type: 报表类型 (income_statement, balance_sheet, cash_flow)
        period: 周期 (annual, quarterly)
        years: 获取年数
        
    Returns:
        财务报表数据
    """
    try:
        if yf is None:
            return ToolResponse.error("yfinance库未安装，无法获取数据")
        
        # 使用yfinance获取数据
        ticker = yf.Ticker(symbol)
        
        # 根据报表类型获取对应数据
        if statement_type == "income_statement":
            if period == "annual":
                data = ticker.financials
            else:
                data = ticker.quarterly_financials
        elif statement_type == "balance_sheet":
            if period == "annual":
                data = ticker.balance_sheet
            else:
                data = ticker.quarterly_balance_sheet
        elif statement_type == "cash_flow":
            if period == "annual":
                data = ticker.cashflow
            else:
                data = ticker.quarterly_cashflow
        else:
            return ToolResponse.error(f"不支持的报表类型: {statement_type}")
        
        # 限制年数
        if hasattr(data, 'columns') and len(data.columns) > years:
            data = data.iloc[:, :years]
        
        # 转换为字典格式
        result = {
            "symbol": symbol,
            "statement_type": statement_type,
            "period": period,
            "data": data.to_dict() if hasattr(data, 'to_dict') else {},
            "currency": ticker.info.get("currency", "USD") if hasattr(ticker, 'info') else "USD",
            "last_updated": datetime.now().isoformat(),
        }
        
        return ToolResponse.success(result)
        
    except Exception as e:
        return ToolResponse.error(f"获取财务报表失败: {str(e)}")


async def get_stock_price(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
) -> ToolResponse:
    """
    获取股价数据
    
    Args:
        symbol: 股票代码
        period: 时间周期 (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max)
        interval: 数据间隔 (1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo)
        
    Returns:
        股价数据
    """
    try:
        if yf is None:
            return ToolResponse.error("yfinance库未安装，无法获取数据")
        
        ticker = yf.Ticker(symbol)
        
        # 获取历史价格数据
        hist = ticker.history(period=period, interval=interval)
        
        # 获取基本信息
        info = ticker.info
        
        # 转换为字典格式
        result = {
            "symbol": symbol,
            "company_name": info.get("longName", "") if info else "",
            "current_price": info.get("currentPrice", 0) if info else 0,
            "market_cap": info.get("marketCap", 0) if info else 0,
            "volume": info.get("volume", 0) if info else 0,
            "history": hist.to_dict('index') if hasattr(hist, 'to_dict') else {},
            "period": period,
            "interval": interval,
            "currency": info.get("currency", "USD") if info else "USD",
            "last_updated": datetime.now().isoformat(),
        }
        
        return ToolResponse.success(result)
        
    except Exception as e:
        return ToolResponse.error(f"获取股价数据失败: {str(e)}")


async def get_company_info(symbol: str) -> ToolResponse:
    """
    获取公司基本信息
    
    Args:
        symbol: 股票代码
        
    Returns:
        公司基本信息
    """
    try:
        if yf is None:
            return ToolResponse.error("yfinance库未安装，无法获取数据")
        
        ticker = yf.Ticker(symbol)
        info = ticker.info
        
        # 筛选重要信息
        key_info = {
            "symbol": symbol,
            "company_name": info.get("longName", ""),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "market_cap": info.get("marketCap", 0),
            "enterprise_value": info.get("enterpriseValue", 0),
            "trailing_pe": info.get("trailingPE", 0),
            "forward_pe": info.get("forwardPE", 0),
            "peg_ratio": info.get("pegRatio", 0),
            "price_to_sales": info.get("priceToSalesTrailing12Months", 0),
            "price_to_book": info.get("priceToBook", 0),
            "dividend_yield": info.get("dividendYield", 0),
            "return_on_equity": info.get("returnOnEquity", 0),
            "return_on_assets": info.get("returnOnAssets", 0),
            "debt_to_equity": info.get("debtToEquity", 0),
            "current_ratio": info.get("currentRatio", 0),
            "quick_ratio": info.get("quickRatio", 0),
            "gross_margin": info.get("grossMargins", 0),
            "operating_margin": info.get("operatingMargins", 0),
            "profit_margin": info.get("profitMargins", 0),
            "revenue_growth": info.get("revenueGrowth", 0),
            "earnings_growth": info.get("earningsGrowth", 0),
            "currency": info.get("currency", "USD"),
            "country": info.get("country", ""),
            "website": info.get("website", ""),
            "business_summary": info.get("longBusinessSummary", ""),
            "last_updated": datetime.now().isoformat(),
        }
        
        return ToolResponse.success(key_info)
        
    except Exception as e:
        return ToolResponse.error(f"获取公司信息失败: {str(e)}")


async def get_market_indices(
    indices: List[str] = None
) -> ToolResponse:
    """
    获取市场指数数据
    
    Args:
        indices: 指数列表，默认为主要指数
        
    Returns:
        市场指数数据
    """
    if indices is None:
        indices = ["^GSPC", "^DJI", "^IXIC", "^RUT"]  # S&P 500, Dow Jones, NASDAQ, Russell 2000
    
    try:
        if yf is None:
            return ToolResponse.error("yfinance库未安装，无法获取数据")
        
        results = {}
        
        for index in indices:
            ticker = yf.Ticker(index)
            hist = ticker.history(period="1d", interval="1d")
            info = ticker.info
            
            if not hist.empty and info:
                latest = hist.iloc[-1]
                results[index] = {
                    "name": info.get("shortName", index),
                    "value": latest["Close"],
                    "change": latest["Close"] - latest["Open"],
                    "change_percent": ((latest["Close"] - latest["Open"]) / latest["Open"]) * 100,
                    "volume": latest["Volume"],
                    "date": latest.name.isoformat(),
                }
        
        return ToolResponse.success({
            "indices": results,
            "last_updated": datetime.now().isoformat(),
        })
        
    except Exception as e:
        return ToolResponse.error(f"获取市场指数失败: {str(e)}")


async def get_economic_indicators(
    indicators: List[str] = None
) -> ToolResponse:
    """
    获取经济指标数据
    
    Args:
        indicators: 经济指标列表
        
    Returns:
        经济指标数据
    """
    # 这里可以集成FRED或其他经济数据源
    # 简化实现，返回一些模拟数据
    try:
        if indicators is None:
            indicators = ["GDP", "CPI", "Unemployment", "Interest Rate"]
        
        # 模拟数据，实际应用中应该从真实API获取
        mock_data = {
            "GDP": {"value": 25.46, "unit": "trillion USD", "growth": 2.1},
            "CPI": {"value": 301.8, "unit": "index", "growth": 3.2},
            "Unemployment": {"value": 3.7, "unit": "percent", "growth": -0.1},
            "Interest Rate": {"value": 5.25, "unit": "percent", "growth": 0.0},
        }
        
        result = {
            "indicators": {k: mock_data.get(k, {}) for k in indicators},
            "last_updated": datetime.now().isoformat(),
        }
        
        return ToolResponse.success(result)
        
    except Exception as e:
        return ToolResponse.error(f"获取经济指标失败: {str(e)}")


def _load_sample_data(data_type: str) -> Dict:
    """
    加载示例数据（用于演示）
    
    Args:
        data_type: 数据类型
        
    Returns:
        示例数据
    """
    sample_data_dir = os.path.join(
        os.path.dirname(__file__), "..", "data"
    )
    
    try:
        file_path = os.path.join(sample_data_dir, f"{data_type}.json")
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    
    # 返回基础示例数据
    return {
        "message": "示例数据未找到，使用模拟数据",
        "data": _get_mock_data(data_type)
    }


def _get_mock_data(data_type: str) -> Dict:
    """获取模拟数据"""
    mock_data = {
        "income_statement": {
            "Revenue": {"2023": 1000000, "2022": 900000, "2021": 800000},
            "Cost of Revenue": {"2023": 600000, "2022": 540000, "2021": 480000},
            "Gross Profit": {"2023": 400000, "2022": 360000, "2021": 320000},
            "Operating Income": {"2023": 150000, "2022": 135000, "2021": 120000},
            "Net Income": {"2023": 100000, "2022": 90000, "2021": 80000},
        },
        "balance_sheet": {
            "Total Assets": {"2023": 500000, "2022": 450000, "2021": 400000},
            "Total Liabilities": {"2023": 300000, "2022": 270000, "2021": 240000},
            "Shareholders' Equity": {"2023": 200000, "2022": 180000, "2021": 160000},
        },
        "cash_flow": {
            "Operating Cash Flow": {"2023": 120000, "2022": 108000, "2021": 96000},
            "Investing Cash Flow": {"2023": -50000, "2022": -45000, "2021": -40000},
            "Financing Cash Flow": {"2023": -30000, "2022": -27000, "2021": -24000},
        }
    }
    
    return mock_data.get(data_type, {})