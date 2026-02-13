"""
工具管理和配置系统

提供统一的工具注册、管理和配置功能
"""

import os
import json
import importlib
from typing import Dict, List, Any, Optional, Callable, Type
from dataclasses import dataclass, field
from agentscope.tool import Toolkit
from agentscope.tools.tool_response import ToolResponse


@dataclass
class ToolConfig:
    """
    工具配置类
    """
    name: str
    description: str = ""
    function: Callable = None
    enabled: bool = True
    category: str = "general"
    parameters: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    """
    工具注册表
    
    管理所有可用工具的注册、发现和配置
    """

    def __init__(self):
        """初始化工具注册表"""
        self.tools: Dict[str, ToolConfig] = {}
        self.categories: Dict[str, List[str]] = {}
        self.tool_groups: Dict[str, List[str]] = {}

    def register_tool(
        self,
        name: str,
        function: Callable,
        description: str = "",
        category: str = "general",
        enabled: bool = True,
        **kwargs
    ) -> bool:
        """
        注册工具
        
        Args:
            name: 工具名称
            function: 工具函数
            description: 描述
            category: 分类
            enabled: 是否启用
            **kwargs: 其他配置
            
        Returns:
            是否注册成功
        """
        try:
            tool_config = ToolConfig(
                name=name,
                function=function,
                description=description,
                category=category,
                enabled=enabled,
                parameters=kwargs.get("parameters", {}),
                dependencies=kwargs.get("dependencies", []),
                metadata=kwargs.get("metadata", {})
            )
            
            self.tools[name] = tool_config
            
            # 更新分类
            if category not in self.categories:
                self.categories[category] = []
            if name not in self.categories[category]:
                self.categories[category].append(name)
            
            return True
            
        except Exception as e:
            print(f"注册工具 {name} 失败: {str(e)}")
            return False

    def unregister_tool(self, name: str) -> bool:
        """
        注销工具
        
        Args:
            name: 工具名称
            
        Returns:
            是否注销成功
        """
        if name in self.tools:
            tool_config = self.tools[name]
            category = tool_config.category
            
            # 从注册表移除
            del self.tools[name]
            
            # 从分类移除
            if category in self.categories and name in self.categories[category]:
                self.categories[category].remove(name)
            
            return True
        
        return False

    def get_tool(self, name: str) -> Optional[ToolConfig]:
        """
        获取工具配置
        
        Args:
            name: 工具名称
            
        Returns:
            工具配置
        """
        return self.tools.get(name)

    def list_tools(
        self,
        category: str = None,
        enabled_only: bool = False
    ) -> Dict[str, ToolConfig]:
        """
        列出工具
        
        Args:
            category: 分类过滤
            enabled_only: 仅列出启用的工具
            
        Returns:
            工具字典
        """
        result = {}
        
        for name, tool_config in self.tools.items():
            if category and tool_config.category != category:
                continue
            
            if enabled_only and not tool_config.enabled:
                continue
            
            result[name] = tool_config
        
        return result

    def enable_tool(self, name: str) -> bool:
        """启用工具"""
        if name in self.tools:
            self.tools[name].enabled = True
            return True
        return False

    def disable_tool(self, name: str) -> bool:
        """禁用工具"""
        if name in self.tools:
            self.tools[name].enabled = False
            return True
        return False

    def create_tool_group(self, group_name: str, tool_names: List[str]) -> bool:
        """
        创建工具组
        
        Args:
            group_name: 组名
            tool_names: 工具名称列表
            
        Returns:
            是否创建成功
        """
        # 验证工具是否存在
        for tool_name in tool_names:
            if tool_name not in self.tools:
                print(f"工具 {tool_name} 不存在")
                return False
        
        self.tool_groups[group_name] = tool_names.copy()
        return True

    def get_tool_group(self, group_name: str) -> List[str]:
        """获取工具组"""
        return self.tool_groups.get(group_name, [])

    def list_categories(self) -> List[str]:
        """列出所有分类"""
        return list(self.categories.keys())

    def list_groups(self) -> List[str]:
        """列出所有工具组"""
        return list(self.tool_groups.keys())


class ToolManager:
    """
    工具管理器
    
    提供高级的工具管理功能
    """

    def __init__(self, registry: ToolRegistry = None, config_file: str = None):
        """
        初始化工具管理器
        
        Args:
            registry: 工具注册表
            config_file: 配置文件路径
        """
        self.registry = registry or ToolRegistry()
        self.config_file = config_file or os.path.join(
            os.path.dirname(__file__), "..", "config", "tools_config.json"
        )
        
        # 加载配置
        self.load_config()
        
        # 自动注册默认工具
        self._register_default_tools()

    def load_config(self):
        """加载工具配置"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                
                # 应用配置
                for tool_name, tool_config in config.get("tools", {}).items():
                    if tool_config.get("enabled", True):
                        # 这里可以根据配置动态加载和注册工具
                        pass
                
                # 加载工具组配置
                for group_name, tool_names in config.get("groups", {}).items():
                    self.registry.create_tool_group(group_name, tool_names)
                
            except Exception as e:
                print(f"加载工具配置失败: {str(e)}")

    def save_config(self):
        """保存工具配置"""
        config = {
            "tools": {},
            "groups": self.registry.tool_groups,
            "categories": self.registry.categories
        }
        
        # 保存工具配置
        for name, tool_config in self.registry.tools.items():
            config["tools"][name] = {
                "description": tool_config.description,
                "enabled": tool_config.enabled,
                "category": tool_config.category,
                "parameters": tool_config.parameters,
                "dependencies": tool_config.dependencies,
                "metadata": tool_config.metadata
            }
        
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存工具配置失败: {str(e)}")

    def create_toolkit(self, tool_names: List[str] = None) -> Toolkit:
        """
        创建工具集
        
        Args:
            tool_names: 工具名称列表，如果为None则使用所有启用的工具
            
        Returns:
            Toolkit实例
        """
        toolkit = Toolkit()
        
        if tool_names is None:
            tools_to_add = self.registry.list_tools(enabled_only=True)
        else:
            tools_to_add = {}
            for name in tool_names:
                tool_config = self.registry.get_tool(name)
                if tool_config and tool_config.enabled:
                    tools_to_add[name] = tool_config
        
        for name, tool_config in tools_to_add.items():
            try:
                toolkit.register_tool_function(tool_config.function)
            except Exception as e:
                print(f"注册工具 {name} 到Toolkit失败: {str(e)}")
        
        return toolkit

    def register_tools_from_module(self, module_path: str, module_name: str = None):
        """
        从模块注册工具
        
        Args:
            module_path: 模块路径
            module_name: 模块名称
        """
        try:
            if module_name:
                module = importlib.import_module(f"{module_path}.{module_name}")
            else:
                module = importlib.import_module(module_path)
            
            # 查找模块中的工具函数
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                
                # 检查是否是工具函数（可以通过装饰器或命名约定）
                if callable(attr) and hasattr(attr, '_is_tool_function'):
                    tool_name = getattr(attr, '_tool_name', attr_name)
                    description = getattr(attr, '_tool_description', '')
                    category = getattr(attr, '_tool_category', 'general')
                    
                    self.registry.register_tool(
                        name=tool_name,
                        function=attr,
                        description=description,
                        category=category
                    )
        
        except Exception as e:
            print(f"从模块 {module_path} 注册工具失败: {str(e)}")

    def _register_default_tools(self):
        """注册默认工具"""
        try:
            # 注册财务分析工具
            from ..tools.data_fetcher import FinancialDataFetcher
            from ..tools.analyzer import FinancialAnalyzer
            from ..tools.report_generator import ReportGenerator
            
            data_fetcher = FinancialDataFetcher()
            analyzer = FinancialAnalyzer()
            report_generator = ReportGenerator()
            
            # 注册数据获取工具
            self.registry.register_tool(
                name="get_financial_statements",
                function=data_fetcher.get_financial_statements,
                description="获取财务报表数据",
                category="data"
            )
            
            self.registry.register_tool(
                name="get_stock_price",
                function=data_fetcher.get_stock_price,
                description="获取股价数据",
                category="data"
            )
            
            self.registry.register_tool(
                name="get_company_info",
                function=data_fetcher.get_company_info,
                description="获取公司基本信息",
                category="data"
            )
            
            # 注册分析工具
            self.registry.register_tool(
                name="calculate_profitability_ratios",
                function=analyzer.calculate_profitability_ratios,
                description="计算盈利能力比率",
                category="analysis"
            )
            
            self.registry.register_tool(
                name="calculate_solvency_ratios",
                function=analyzer.calculate_solvency_ratios,
                description="计算偿债能力比率",
                category="analysis"
            )
            
            self.registry.register_tool(
                name="calculate_efficiency_ratios",
                function=analyzer.calculate_efficiency_ratios,
                description="计算运营效率比率",
                category="analysis"
            )
            
            self.registry.register_tool(
                name="trend_analysis",
                function=analyzer.trend_analysis,
                description="进行趋势分析",
                category="analysis"
            )
            
            # 注册报告工具
            self.registry.register_tool(
                name="generate_financial_report",
                function=report_generator.generate_financial_report,
                description="生成财务分析报告",
                category="report"
            )
            
            self.registry.register_tool(
                name="create_chart",
                function=report_generator.create_chart,
                description="创建图表",
                category="report"
            )
            
            # 创建默认工具组
            self.registry.create_tool_group(
                "data_collection",
                ["get_financial_statements", "get_stock_price", "get_company_info"]
            )
            
            self.registry.create_tool_group(
                "financial_analysis",
                ["calculate_profitability_ratios", "calculate_solvency_ratios", "calculate_efficiency_ratios"]
            )
            
            self.registry.create_tool_group(
                "reporting",
                ["generate_financial_report", "create_chart"]
            )
            
            self.registry.create_tool_group(
                "all_tools",
                list(self.registry.tools.keys())
            )
            
        except Exception as e:
            print(f"注册默认工具失败: {str(e)}")


# 全局工具管理器实例
_tool_manager = None


def get_tool_manager() -> ToolManager:
    """获取全局工具管理器"""
    global _tool_manager
    if _tool_manager is None:
        _tool_manager = ToolManager()
    return _tool_manager


def get_tool_registry() -> ToolRegistry:
    """获取工具注册表"""
    return get_tool_manager().registry


# 工具装饰器
def tool_function(
    name: str = None,
    description: str = "",
    category: str = "general",
    enabled: bool = True
):
    """
    工具函数装饰器
    
    Args:
        name: 工具名称
        description: 描述
        category: 分类
        enabled: 是否启用
    """
    def decorator(func):
        func._is_tool_function = True
        func._tool_name = name or func.__name__
        func._tool_description = description
        func._tool_category = category
        func._tool_enabled = enabled
        return func
    
    return decorator