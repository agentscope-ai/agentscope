"""
MCP (Model Context Protocol) 客户端集成

为财务分析智能体提供MCP协议支持，实现与外部数据源和服务的集成
"""

import os
import json
import asyncio
from typing import Dict, List, Any, Optional, Callable

try:
    from agentscope.mcp import HttpStatelessClient, HttpStatefulClient, StdioClient
    from agentscope.tools import ToolResponse
except ImportError:
    # Fallback for local development
    class HttpStatelessClient:
        def __init__(self, name: str, transport: str, url: str):
            self.name = name
            self.transport = transport
            self.url = url
    
    class HttpStatefulClient:
        def __init__(self, name: str, transport: str, url: str):
            self.name = name
            self.transport = transport
            self.url = url
    
    class StdioClient:
        def __init__(self, name: str, command: str, args: list):
            self.name = name
            self.command = command
            self.args = args
    
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

from ..tools.data_fetcher_fixed import FinancialDataFetcher
from ..tools.analyzer_fixed import FinancialAnalyzer
from ..tools.report_generator_fixed import ReportGenerator


class FinancialMCPClient:
    """
    财务数据MCP客户端
    
    支持多种MCP服务：
    - 财务数据提供商MCP
    - 市场数据MCP
    - 新闻数据MCP
    - 分析服务MCP
    """

    def __init__(self, config_path: str = None):
        """
        初始化MCP客户端
        
        Args:
            config_path: MCP配置文件路径
        """
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(__file__), "..", "config", "mcp_config.json"
            )
        
        self.config_path = config_path
        self.clients = {}
        self.tools_cache = {}
        
        # 加载配置
        self.config = self._load_config()
        
        # 初始化客户端
        self._init_clients()

    def _load_config(self) -> Dict[str, Any]:
        """加载MCP配置"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"加载MCP配置失败: {str(e)}")
        
        # 默认配置
        return {
            "financial_data": {
                "type": "http_stateless",
                "url": "https://financial-mcp.example.com/api",
                "api_key": os.environ.get("FINANCIAL_MCP_API_KEY"),
                "tools": ["get_stock_price", "get_financial_statements", "get_market_indices"]
            },
            "market_data": {
                "type": "http_stateless", 
                "url": "https://market-mcp.example.com/api",
                "api_key": os.environ.get("MARKET_MCP_API_KEY"),
                "tools": ["get_real_time_price", "get_technical_indicators", "get_market_sentiment"]
            },
            "news_data": {
                "type": "http_stateful",
                "url": "https://news-mcp.example.com/api", 
                "api_key": os.environ.get("NEWS_MCP_API_KEY"),
                "tools": ["search_news", "get_company_news", "analyze_sentiment"]
            }
        }

    def _init_clients(self):
        """初始化MCP客户端"""
        for service_name, service_config in self.config.items():
            try:
                # 模拟客户端初始化（实际应该根据配置创建真实客户端）
                if service_config["type"] == "http_stateless":
                    client = HttpStatelessClient(
                        name=service_name,
                        transport="streamable_http",
                        url=service_config["url"]
                    )
                elif service_config["type"] == "http_stateful":
                    client = HttpStatefulClient(
                        name=service_name,
                        transport="streamable_http", 
                        url=service_config["url"]
                    )
                elif service_config["type"] == "stdio":
                    client = StdioClient(
                        name=service_name,
                        command=service_config.get("command", ""),
                        args=service_config.get("args", [])
                    )
                else:
                    continue
                
                self.clients[service_name] = client
                
            except Exception as e:
                print(f"初始化MCP客户端 {service_name} 失败: {str(e)}")

    async def get_tools(self, service_name: str = None) -> Dict[str, Any]:
        """
        获取可用的MCP工具
        
        Args:
            service_name: 服务名称，如果为None则获取所有服务的工具
            
        Returns:
            工具列表
        """
        if service_name:
            if service_name in self.clients:
                if service_name not in self.tools_cache:
                    # 模拟工具列表获取
                    service_config = self.config.get(service_name, {})
                    self.tools_cache[service_name] = service_config.get("tools", [])
                
                return {service_name: self.tools_cache[service_name]}
            else:
                return {"error": f"未找到服务: {service_name}"}
        else:
            all_tools = {}
            for name, client in self.clients.items():
                if name not in self.tools_cache:
                    service_config = self.config.get(name, {})
                    self.tools_cache[name] = service_config.get("tools", [])
                
                all_tools[name] = self.tools_cache[name]
            
            return all_tools

    async def call_tool(
        self,
        service_name: str,
        tool_name: str,
        arguments: Dict[str, Any] = None
    ) -> ToolResponse:
        """
        调用MCP工具
        
        Args:
            service_name: 服务名称
            tool_name: 工具名称
            arguments: 工具参数
            
        Returns:
            工具执行结果
        """
        try:
            if service_name not in self.clients:
                return ToolResponse.error(f"未找到服务: {service_name}")
            
            # 验证工具是否存在
            available_tools = await self.get_tools(service_name)
            if service_name not in available_tools:
                return ToolResponse.error(f"服务 {service_name} 无可用工具")
            
            tool_names = [tool.get("name") for tool in available_tools[service_name]]
            if tool_name not in tool_names:
                return ToolResponse.error(f"工具 {tool_name} 不存在")
            
            # 模拟工具调用（实际应该调用MCP API）
            if service_name == "financial_data":
                if tool_name == "get_stock_price":
                    return await self._simulate_stock_price_call(arguments)
                elif tool_name == "get_financial_statements":
                    return await self._simulate_financial_statements_call(arguments)
                elif tool_name == "get_market_indices":
                    return await self._simulate_market_indices_call(arguments)
            elif service_name == "market_data":
                if tool_name == "get_real_time_price":
                    return await self._simulate_real_time_price_call(arguments)
                elif tool_name == "get_technical_indicators":
                    return await self._simulate_technical_indicators_call(arguments)
                elif tool_name == "get_market_sentiment":
                    return await self._simulate_market_sentiment_call(arguments)
            
            return ToolResponse.error(f"工具 {tool_name} 未实现")
            
        except Exception as e:
            return ToolResponse.error(f"调用工具失败: {str(e)}")

    async def get_financial_data_mcp(
        self,
        symbol: str,
        data_type: str = "stock_price",
        **kwargs
    ) -> ToolResponse:
        """
        通过MCP获取财务数据
        
        Args:
            symbol: 股票代码
            data_type: 数据类型
            **kwargs: 其他参数
            
        Returns:
            财务数据
        """
        try:
            # 根据数据类型选择合适的服务和工具
            if data_type in ["stock_price", "real_time_price"]:
                return await self.call_tool(
                    "market_data",
                    "get_real_time_price",
                    {"symbol": symbol, **kwargs}
                )
            elif data_type == "financial_statements":
                return await self.call_tool(
                    "financial_data",
                    "get_financial_statements",
                    {"symbol": symbol, **kwargs}
                )
            elif data_type == "market_indices":
                return await self.call_tool(
                    "financial_data",
                    "get_market_indices",
                    kwargs
                )
            elif data_type == "technical_indicators":
                return await self.call_tool(
                    "market_data",
                    "get_technical_indicators",
                    {"symbol": symbol, **kwargs}
                )
            else:
                return ToolResponse.error(f"不支持的数据类型: {data_type}")
                
        except Exception as e:
            return ToolResponse.error(f"获取MCP财务数据失败: {str(e)}")

    async def get_news_sentiment_mcp(
        self,
        symbol: str = None,
        keywords: List[str] = None,
        days: int = 7
    ) -> ToolResponse:
        """
        通过MCP获取新闻情绪数据
        
        Args:
            symbol: 股票代码
            keywords: 关键词列表
            days: 天数
            
        Returns:
            新闻情绪数据
        """
        try:
            if symbol:
                return await self.call_tool(
                    "news_data",
                    "get_company_news",
                    {"symbol": symbol, "days": days}
                )
            elif keywords:
                return await self.call_tool(
                    "news_data",
                    "search_news",
                    {"keywords": keywords, "days": days}
                )
            else:
                return ToolResponse.error("必须提供symbol或keywords参数")
                
        except Exception as e:
            return ToolResponse.error(f"获取MCP新闻数据失败: {str(e)}")

    # 模拟方法（实际实现中应该调用真实的MCP API）
    async def _simulate_stock_price_call(self, arguments: Dict[str, Any]) -> ToolResponse:
        """模拟股价调用"""
        symbol = arguments.get("symbol")
        data_fetcher = FinancialDataFetcher()
        return await data_fetcher.get_stock_price(
            symbol=symbol,
            period=arguments.get("period", "1y"),
            interval=arguments.get("interval", "1d")
        )
    
    async def _simulate_financial_statements_call(self, arguments: Dict[str, Any]) -> ToolResponse:
        """模拟财务报表调用"""
        symbol = arguments.get("symbol")
        data_fetcher = FinancialDataFetcher()
        return await data_fetcher.get_financial_statements(
            symbol=symbol,
            statement_type=arguments.get("statement_type", "income_statement"),
            period=arguments.get("period", "annual"),
            years=arguments.get("years", 3)
        )
    
    async def _simulate_market_indices_call(self, arguments: Dict[str, Any]) -> ToolResponse:
        """模拟市场指数调用"""
        data_fetcher = FinancialDataFetcher()
        return await data_fetcher.get_market_indices(
            indices=arguments.get("indices", [])
        )
    
    async def _simulate_real_time_price_call(self, arguments: Dict[str, Any]) -> ToolResponse:
        """模拟实时价格调用"""
        symbols = arguments.get("symbols", [])
        if not symbols:
            return ToolResponse.error("必须提供symbols参数")
        
        data_fetcher = FinancialDataFetcher()
        results = []
        
        for symbol in symbols:
            result = await data_fetcher.get_stock_price(symbol)
            if result.success:
                results.append(result.content)
            else:
                results.append({"error": result.error})
        
        return ToolResponse.success({"symbols": results})
    
    async def _simulate_technical_indicators_call(self, arguments: Dict[str, Any]) -> ToolResponse:
        """模拟技术指标调用"""
        symbol = arguments.get("symbol")
        indicators = arguments.get("indicators", [])
        
        # 返回模拟的技术指标数据
        mock_data = {
            "symbol": symbol,
            "indicators": {
                "RSI": {"value": 65.5, "signal": "neutral"},
                "MACD": {"value": 0.02, "signal": "buy"},
                "MA": {"value": 150.25, "period": "20"},
            },
            "timestamp": self._get_timestamp()
        }
        
        return ToolResponse.success(mock_data)
    
    async def _simulate_market_sentiment_call(self, arguments: Dict[str, Any]) -> ToolResponse:
        """模拟市场情绪调用"""
        market = arguments.get("market", "US")
        timeframe = arguments.get("timeframe", "1w")
        
        # 返回模拟的情绪数据
        mock_data = {
            "market": market,
            "sentiment": {
                "overall": "bullish",
                "fear_greed_index": 25.5,
                "put_call_ratio": 1.2,
                "volatility_index": 18.3,
            },
            "timestamp": self._get_timestamp()
        }
        
        return ToolResponse.success(mock_data)

    def _get_timestamp(self) -> str:
        """获取时间戳"""
        from datetime import datetime
        return datetime.now().isoformat()


class MCPToolManager:
    """
    MCP工具管理器
    
    管理MCP工具的注册、发现和调用
    """

    def __init__(self, mcp_client: FinancialMCPClient = None):
        """
        初始化MCP工具管理器
        
        Args:
            mcp_client: MCP客户端实例
        """
        if mcp_client is None:
            mcp_client = FinancialMCPClient()
        
        self.mcp_client = mcp_client
        self.registered_tools = {}
        self.tool_mappings = {}

    async def register_mcp_tools(self) -> Dict[str, Any]:
        """
        注册所有MCP工具为AgentScope工具
        
        Returns:
            注册结果
        """
        try:
            all_tools = await self.mcp_client.get_tools()
            registered = {}
            
            for service_name, tools in all_tools.items():
                if isinstance(tools, dict) and "error" in tools:
                    continue
                
                for tool in tools:
                    tool_name = tool.get("name")
                    if not tool_name:
                        continue
                    
                    # 创建AgentScope工具函数
                    async def mcp_tool_wrapper(*args, **kwargs):
                        # 提取技能参数
                        service_name = kwargs.pop("_service_name", service_name)
                        actual_tool_name = kwargs.pop("_tool_name", tool_name)
                        
                        return await self.mcp_client.call_tool(
                            service_name, actual_tool_name, kwargs
                        )
                    
                    # 设置工具函数属性
                    mcp_tool_wrapper.__name__ = f"mcp_{tool_name}"
                    mcp_tool_wrapper.__doc__ = tool.get("description", "")
                    mcp_tool_wrapper._mcp_service = service_name
                    mcp_tool_wrapper._mcp_tool = tool_name
                    
                    # 注册工具
                    full_tool_name = f"{service_name}_{tool_name}"
                    self.registered_tools[full_tool_name] = mcp_tool_wrapper
                    self.tool_mappings[full_tool_name] = {
                        "service": service_name,
                        "tool": tool_name,
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {})
                    }
                    
                    registered[full_tool_name] = {
                        "service": service_name,
                        "tool": tool_name,
                        "description": tool.get("description", ""),
                        "registered": True
                    }
            
            return {
                "success": True,
                "total_registered": len(registered),
                "tools": registered
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "total_registered": 0
            }

    def get_tool(self, tool_name: str):
        """获取已注册的工具函数"""
        return self.registered_tools.get(tool_name)

    def list_tools(self) -> Dict[str, Dict[str, Any]]:
        """列出所有已注册的MCP工具"""
        return self.tool_mappings

    async def create_toolkit(self):
        """
        创建包含所有MCP工具的AgentScope Toolkit
        
        Returns:
            Toolkit实例
        """
        from agentscope.tool import Toolkit
        
        toolkit = Toolkit()
        
        # 注册所有MCP工具
        registration_result = await self.register_mcp_tools()
        
        if registration_result["success"]:
            for tool_name, tool_func in self.registered_tools.items():
                try:
                    toolkit.register_tool_function(tool_func)
                except Exception as e:
                    print(f"注册工具 {tool_name} 失败: {str(e)}")
        
        return toolkit


# 全局MCP客户端和管理器实例
_mcp_client = None
_mcp_tool_manager = None


def get_mcp_client() -> FinancialMCPClient:
    """获取全局MCP客户端实例"""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = FinancialMCPClient()
    return _mcp_client


def get_mcp_tool_manager() -> MCPToolManager:
    """获取全局MCP工具管理器实例"""
    global _mcp_tool_manager
    if _mcp_tool_manager is None:
        _mcp_tool_manager = MCPToolManager(get_mcp_client())
    return _mcp_tool_manager


async def create_mcp_toolkit():
    """创建包含MCP工具的工具集"""
    manager = get_mcp_tool_manager()
    return await manager.create_toolkit()