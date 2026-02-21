"""
修复后的工具管理和配置系统

提供统一的工具注册、管理和配置功能，符合AgentScope框架规范
"""

import os
import json
import asyncio
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime

try:
    from agentscope.tools import Toolkit
except ImportError:
    # Fallback for local development
    class Toolkit:
        def __init__(self):
            self.tools = {}
        
        def register_tool_function(self, tool_func):
            if hasattr(tool_func, '__name__'):
                self.tools[tool_func.__name__] = tool_func
        
        def get_tool_names(self) -> List[str]:
            return list(self.tools.keys())
        
        async def create_toolkit(self, tool_names: List[str] = None):
            toolkit = Toolkit()
            for tool_name in (tool_names or self.get_tool_names()):
                tool_func = self.tools.get(tool_name)
                if tool_func:
                    toolkit.register_tool_function(tool_func)
            
            return toolkit

from .tools.data_fetcher_fixed import (
    get_financial_statements,
    get_stock_price,
    get_company_info,
    get_market_indices,
    get_economic_indicators,
)

try:
    from agentscope.tools import ToolResponse
except ImportError:
    # Fallback for local development
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
    from agentscope.agent import AgentBase
except ImportError:
    # Fallback for local development
    class AgentBase:
        def __init__(self, **kwargs):
            pass


@dataclass
class ToolConfig:
    """工具配置类"""
    name: str
    description: str = ""
    function: Callable = None
    enabled: bool = True
    category: str = "general"
    parameters: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "category": self.category,
            "parameters": self.parameters,
            "dependencies": self.dependencies,
            "metadata": self.metadata
        }


class ToolRegistry:
    """工具注册表"""
    
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
        """注册工具
        
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
            if category in self.categories:
                if name in self.categories[category]:
                    self.categories[category].remove(name)
            
            return True
        
        return False

    def get_tool(self, name: str) -> Optional[ToolConfig]:
        """获取工具配置"""
        return self.tools.get(name)

    def list_tools(
        self,
        category: str = None,
        enabled_only: bool = False
    ) -> Dict[str, ToolConfig]:
        """列出工具"""
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

    def create_tool_group(
        self,
        group_name: str,
        tool_names: List[str]
    ) -> bool:
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

    def load_config(self, config_file: str = None):
        """加载工具配置"""
        if config_file and os.path.exists(config_file):
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                
                # 应用配置
                for tool_name, tool_config in config.get("tools", {}).items():
                    if tool_config.get("enabled", True):
                        self.register_tool(
                            name=tool_name,
                            function=None,  # 需要从现有工具中获取
                            description=tool_config.get("description", ""),
                            category=tool_config.get("category", "general"),
                            enabled=True
                        )
                
                # 加载工具组配置
                for group_name, tool_names in config.get("groups", {}).items():
                    self.create_tool_group(group_name, tool_names)
                
                return config
                
            except Exception as e:
                print(f"加载工具配置失败: {str(e)}")
        
        # 使用默认配置
        return {}

    def save_config(self, config_file: str = None):
        """保存工具配置"""
        config = {
            "tools": {},
            "groups": self.tool_groups,
            "categories": self.categories
        }
        
        # 保存工具配置
        for name, tool_config in self.tools.items():
            config["tools"][name] = {
                "description": tool_config.description,
                "enabled": tool_config.enabled,
                "category": tool_config.category,
                "parameters": tool_config.parameters,
                "dependencies": tool_config.dependencies,
                "metadata": tool_config.metadata
            }
        
        # 保存工具组配置
        for group_name, tool_names in self.tool_groups.items():
            config["groups"][group_name] = tool_names
        
        try:
            os.makedirs(os.path.dirname(config_file), exist_ok=True)
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            print(f"保存工具配置失败: {str(e)}")

    def get_tool_func(self, name: str) -> Optional[Callable]:
        """获取工具函数"""
        tool_config = self.get_tool(name)
        return tool_config.function if tool_config else None


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
        if registry is None:
            registry = ToolRegistry()
        
        self.registry = registry or ToolRegistry()
        self.config_file = config_file or os.path.join(
            os.path.dirname(__file__), "..", "config", "tools_config.json"
        )
        
        # 加载配置
        self.load_config()
        
        # 自动注册默认工具
        self._register_default_tools()

    def load_config(self):
        """加载配置文件"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                
                # 应用配置
                for tool_name, tool_config in config.get("tools", {}).items():
                    if tool_config.get("enabled", True):
                        self.registry.register_tool(
                            name=tool_name,
                            function=tool_config.get("function"),
                            description=tool_config.get("description", ""),
                            category=tool_config.get("category", "general"),
                            parameters=tool_config.get("parameters", {}),
                            dependencies=tool_config.get("dependencies", []),
                            metadata=tool_config.get("metadata", {})
                        )
                
                # 加载工具组配置
                for group_name, tool_names in config.get("groups", {}).items():
                    self.registry.create_tool_group(group_name, tool_names)
                
            except Exception as e:
                print(f"加载配置失败: {str(e)}")

    def save_config(self):
        """保存配置文件"""
        config = {
            "tools": {},
            "groups": self.registry.tool_groups,
            "categories": self.registry.categories,
            "global_settings": {
                "default_timeout": 30,
                "max_retry_count": 3,
                "enable_caching": True,
                "cache_duration": 1800,
                "parallel_execution": True,
                "max_concurrent_tools": 5
            }
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
        
        # 保存工具组配置
        for group_name, tool_names in self.registry.tool_groups.items():
            config["groups"][group_name] = tool_names
        
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            print(f"保存配置失败: {str(e)}")

    def create_toolkit(self, tool_names: List[str] = None) -> Toolkit:
        """创建工具集"""
        toolkit = Toolkit()
        
        if tool_names is None:
            tool_names = self.registry.list_tools(enabled_only=True)
        
        for name, tool_func in self.registry.tools.items():
            if tool_config := self.registry.get_tool(name):
                if tool_config and tool_config.enabled:
                    toolkit.register_tool_function(tool_func)
        
        return toolkit

    def _register_default_tools(self):
        """注册默认工具"""
        try:
            # 注册财务数据工具
            from .tools.data_fetcher_fixed import (
                get_financial_statements,
                get_stock_price,
                get_company_info,
                get_market_indices,
                get_economic_indicators,
            )
            
            self.registry.register_tool(
                name="get_financial_statements",
                function=get_financial_statements,
                description="获取财务报表数据",
                category="data"
            )
            
            self.registry.register_tool(
                name="get_stock_price",
                function=get_stock_price,
                description="获取股价数据",
                category="data"
            )
            
            self.registry.register_tool(
                name="get_company_info",
                function=get_company_info,
                description="获取公司基本信息",
                category="data"
            )
            
            self.registry.register_tool(
                name="get_market_indices",
                function=get_market_indices,
                description="获取市场指数",
                category="data"
            )
            
            self.registry.register_tool(
                name="get_economic_indicators",
                function=get_economic_indicators,
                description="获取经济指标",
                category="data"
            )
            
            # 创建默认工具组
            self.registry.create_tool_group(
                "data_collection",
                ["get_financial_statements", "get_stock_price", "get_company_info", "get_market_indices", "get_economic_indicators"]
            )
            
            self.registry.create_tool_group(
                "data_analysis",
                ["calculate_profitability_ratios", "calculate_solvency_ratios", "calculate_efficiency_ratios", "trend_analysis"]
            )
            
            self.registry.create_tool_group(
                "reporting",
                ["generate_financial_report", "create_chart"]
            )
            
            # 创建所有工具组
            self.registry.create_tool_group(
                "all_tools",
                self.registry.get_tool_names()
            )
            
        except Exception as e:
            print(f"注册默认工具失败: {str(e)}")

    async def register_tools_from_module(self, module_path: str, module_name: str = None):
        """从模块注册工具"""
        try:
            import importlib
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
                    enabled = getattr(attr, '_tool_enabled', True)
                    
                    self.registry.register_tool(
                        name=tool_name,
                        function=attr,
                        description=description,
                        category=category=category,
                        enabled=enabled
                    )
                    
        except Exception as e:
            print(f"从模块 {module_path} 注册工具失败: {str(e)}")

    def get_tool_manager(self) -> ToolManager:
        """获取工具管理器"""
        if not self._tool_manager:
            self._tool_manager = ToolManager(self.registry, self.config_file)
        return self._tool_manager

    def get_tool_registry(self) -> ToolRegistry:
        """获取工具注册表"""
        return self.registry

    def list_categories(self) -> List[str]:
        """列出所有分类"""
        return self.registry.list_categories()

    def list_groups(self) -> List[str]:
        """列出所有工具组"""
        return self.registry.list_groups()

    def create_toolkit(self, tool_names: List[str] = None) -> Toolkit:
        """创建工具集"""
        return self.tool_manager.create_toolkit(tool_names)

    def list_tools(self) -> Dict[str, Dict[str, Any]]:
        """列出所有工具"""
        return self.registry.list_tools()

    def get_tool_func(self, name: str) -> Optional[Callable]:
        """获取工具函数"""
        return self.registry.get_tool_func(name)

    def enable_tool(self, name: str) -> bool:
        """启用工具"""
        return self.registry.enable_tool(name)

    def disable_tool(self, name: str) -> bool:
        """禁用工具"""
        return self.registry.disable_tool(name)

    async def register_skill_tools(self, skills: Dict[str, Any] = None):
        """注册技能工具"""
        # 将技能转换为工具函数
        if skills:
            for skill_name, skill in skills.items():
                skill_class = skills[skill_name]
                if hasattr(skill_class, 'execute'):
                    skill_instance = skill_class()
                    
                    # 创建技能工具函数
                    async def skill_tool_wrapper(*args, **kwargs):
                        # 提取技能参数
                        skill_params = {}
                        for param in skill_instance.parameters:
                            if param in kwargs:
                                skill_params[param] = kwargs[param]
                        
                        # 执行技能
                        result = await skill_instance.execute(skill_params)
                        
                        return result.get("result", {"error": result.get("error", "Skill execution failed")}
                    
                    # 注册为工具
                    self.registry.register_tool(
                        name=f"skill_{skill_name}",
                        function=skill_tool_wrapper,
                        description=skill_instance.description,
                        category="skills"
                    )
            
            return self.registry

    def get_skill_tool_names(self) -> List[str]:
        """获取技能工具名称列表"""
        return [name for name in self.registry.list_tools() if name.startswith("skill_")]


# 全局工具管理器实例
_tool_manager = None

def get_tool_manager() -> ToolManager:
    """获取全局工具管理器"""
    global _tool_manager
    if _tool_manager is None:
        _tool_manager = ToolManager()
    return _tool_manager


# 工具装饰器
def tool_function(
    name: str = None,
    description: str = "",
    category: str = "general",
    enabled: bool = True,
    **kwargs
):
    """工具函数装饰器"""
    def decorator(func):
        func._is_tool_function = True
        func._tool_name = name or func.__name__
        func._tool_description = description
        func._tool_category = category
        func._tool_enabled = enabled
        func._tool_parameters = kwargs.get("parameters", {})
        func._tool_dependencies = kwargs.get("dependencies", [])
        
        return func