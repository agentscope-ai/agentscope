"""
提示词管理系统

提供结构化的提示词管理和动态生成功能，支持多语言、多场景的提示词配置
"""

import os
import json
import yaml
from typing import Dict, List, Any, Optional, Union
from jinja2 import Environment, FileSystemLoader, Template
from datetime import datetime


class PromptTemplate:
    """
    提示词模板类
    
    支持动态参数填充和多语言版本
    """

    def __init__(
        self,
        name: str,
        template_content: str,
        description: str = "",
        parameters: List[str] = None,
        language: str = "zh",
        version: str = "1.0.0"
    ):
        """
        初始化提示词模板
        
        Args:
            name: 模板名称
            template_content: 模板内容
            description: 描述
            parameters: 参数列表
            language: 语言
            version: 版本
        """
        self.name = name
        self.template_content = template_content
        self.description = description
        self.parameters = parameters or []
        self.language = language
        self.version = version
        self.template = Template(template_content)
        self.created_at = datetime.now().isoformat()

    def render(self, **kwargs) -> str:
        """
        渲染提示词
        
        Args:
            **kwargs: 模板参数
            
        Returns:
            渲染后的提示词
        """
        try:
            return self.template.render(**kwargs)
        except Exception as e:
            raise ValueError(f"渲染模板失败: {str(e)}")

    def validate_parameters(self, parameters: Dict[str, Any]) -> List[str]:
        """
        验证参数
        
        Args:
            parameters: 参数字典
            
        Returns:
            缺失参数列表
        """
        missing = []
        for param in self.parameters:
            if param not in parameters:
                missing.append(param)
        return missing

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "name": self.name,
            "template_content": self.template_content,
            "description": self.description,
            "parameters": self.parameters,
            "language": self.language,
            "version": self.version,
            "created_at": self.created_at
        }


class PromptManager:
    """
    提示词管理器
    
    管理提示词模板的加载、存储和检索
    """

    def __init__(self, templates_dir: str = None):
        """
        初始化提示词管理器
        
        Args:
            templates_dir: 模板目录路径
        """
        if templates_dir is None:
            templates_dir = os.path.join(
                os.path.dirname(__file__), "..", "prompts"
            )
        
        self.templates_dir = templates_dir
        self.templates = {}
        self.categories = {}
        self.jinja_env = Environment(
            loader=FileSystemLoader(templates_dir),
            trim_blocks=True,
            lstrip_blocks=True
        )
        
        # 创建目录
        os.makedirs(templates_dir, exist_ok=True)
        
        # 加载模板
        self.load_templates()

    def load_templates(self):
        """加载所有提示词模板"""
        if not os.path.exists(self.templates_dir):
            return
        
        # 遍历所有文件
        for root, dirs, files in os.walk(self.templates_dir):
            for file in files:
                if file.endswith(('.json', '.yaml', '.yml')):
                    file_path = os.path.join(root, file)
                    try:
                        self._load_template_file(file_path)
                    except Exception as e:
                        print(f"加载模板文件失败 {file_path}: {str(e)}")

    def _load_template_file(self, file_path: str):
        """
        加载单个模板文件
        
        Args:
            file_path: 文件路径
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            if file_path.endswith('.json'):
                data = json.load(f)
            else:
                data = yaml.safe_load(f)
        
        # 支持单个模板或模板数组
        if isinstance(data, list):
            templates = data
        else:
            templates = [data]
        
        for template_data in templates:
            template = PromptTemplate(
                name=template_data["name"],
                template_content=template_data["template"],
                description=template_data.get("description", ""),
                parameters=template_data.get("parameters", []),
                language=template_data.get("language", "zh"),
                version=template_data.get("version", "1.0.0")
            )
            
            self.register_template(template)
            
            # 分类管理
            category = template_data.get("category", "general")
            if category not in self.categories:
                self.categories[category] = []
            self.categories[category].append(template.name)

    def register_template(self, template: PromptTemplate):
        """
        注册模板
        
        Args:
            template: 提示词模板
        """
        self.templates[template.name] = template

    def get_template(self, name: str) -> Optional[PromptTemplate]:
        """
        获取模板
        
        Args:
            name: 模板名称
            
        Returns:
            提示词模板
        """
        return self.templates.get(name)

    def get_templates_by_category(self, category: str) -> List[PromptTemplate]:
        """
        根据分类获取模板
        
        Args:
            category: 分类名称
            
        Returns:
            模板列表
        """
        template_names = self.categories.get(category, [])
        return [self.templates[name] for name in template_names if name in self.templates]

    def list_templates(self) -> Dict[str, Dict[str, Any]]:
        """
        列出所有模板
        
        Returns:
            模板信息字典
        """
        result = {}
        for name, template in self.templates.items():
            result[name] = {
                "description": template.description,
                "parameters": template.parameters,
                "language": template.language,
                "version": template.version,
                "created_at": template.created_at
            }
        return result

    def render_prompt(self, name: str, **kwargs) -> str:
        """
        渲染提示词
        
        Args:
            name: 模板名称
            **kwargs: 模板参数
            
        Returns:
            渲染后的提示词
        """
        template = self.get_template(name)
        if not template:
            raise ValueError(f"未找到模板: {name}")
        
        return template.render(**kwargs)

    def save_template(self, template: PromptTemplate, category: str = "general"):
        """
        保存模板到文件
        
        Args:
            template: 提示词模板
            category: 分类
        """
        template_data = template.to_dict()
        template_data["category"] = category
        
        filename = f"{template.name}.json"
        file_path = os.path.join(self.templates_dir, filename)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(template_data, f, ensure_ascii=False, indent=2)
        
        self.register_template(template)


class FinancialPromptManager(PromptManager):
    """
    财务分析专用提示词管理器
    
    预置财务分析相关的提示词模板
    """

    def __init__(self, templates_dir: str = None):
        """初始化财务提示词管理器"""
        super().__init__(templates_dir)
        self._create_default_templates()

    def _create_default_templates(self):
        """创建默认的财务分析提示词模板"""
        
        # 财务分析主提示词
        financial_analysis_prompt = PromptTemplate(
            name="financial_analysis_main",
            template_content="""你是一名专业的财务分析专家，具备以下专业能力：

## 专业背景
- 深厚的财务理论基础和实践经验
- 熟悉多种财务分析方法和工具
- 具备行业分析和风险评估能力

## 分析任务
请对 {{ company_name }}（{{ company_symbol }}）进行全面的财务分析：

### 分析维度
{% if analysis_types %}
{% for analysis_type in analysis_types %}
- {{ analysis_type }}
{% endfor %}
{% else %}
- 盈利能力分析
- 偿债能力分析  
- 运营效率分析
{% endif %}

### 分析期间
- 时间范围：{{ time_period or '最近3年' }}
- 数据来源：{{ data_source or '公开财报数据' }}

### 输出要求
1. **关键财务指标**：列出并解释重要的财务比率
2. **趋势分析**：分析历史变化趋势
3. **行业对比**：与行业平均水平比较
4. **风险评估**：识别主要财务风险
5. **投资建议**：提供专业的投资建议

请基于数据和事实进行分析，避免主观臆测。""",
            description="财务分析主要提示词模板",
            parameters=["company_name", "company_symbol", "analysis_types", "time_period", "data_source"],
            language="zh"
        )
        
        # 盈利能力分析提示词
        profitability_prompt = PromptTemplate(
            name="profitability_analysis",
            template_content="""请对 {{ company_name }}（{{ company_symbol }}）进行盈利能力分析：

## 分析指标
- 毛利率 (Gross Margin)
- 净利率 (Net Margin) 
- 资产收益率 (ROA)
- 净资产收益率 (ROE)
- 营业利润率 (Operating Margin)

## 数据期间
{% for period in periods %}
- {{ period }}
{% endfor %}

## 分析要求
1. 计算各期财务比率
2. 分析变化趋势
3. 识别盈利能力驱动因素
4. 与行业基准对比
5. 提供改善建议

## 输出格式
```markdown
## 盈利能力分析报告

### 财务比率
| 指标 | {{ periods[0] }} | {{ periods[1] }} | {{ periods[2] }} |
|------|-------------|-------------|-------------|
| 毛利率 | ... | ... | ... |
| 净利率 | ... | ... | ... |

### 趋势分析
[详细分析]

### 行业对比  
[对比分析]

### 结论与建议
[总结和建议]
```""",
            description="盈利能力分析提示词",
            parameters=["company_name", "company_symbol", "periods"],
            language="zh"
        )
        
        # 风险评估提示词
        risk_assessment_prompt = PromptTemplate(
            name="risk_assessment",
            template_content="""请对 {{ company_name }}（{{ company_symbol }}）进行财务风险评估：

## 风险类型
{% if risk_types %}
{% for risk_type in risk_types %}
- {{ risk_type }}
{% endfor %}
{% else %}
- 流动性风险
- 偿债风险
- 盈利风险
- 运营风险
{% endif %}

## 评估依据
- 最新财务报表数据
- 历史财务趋势
- 行业风险因素
- 宏观经济环境

## 评级标准
- **低风险**：财务指标健康，风险可控
- **中风险**：存在一定风险，需要关注
- **高风险**：财务状况恶化，需要警惕
- **极高风险**：存在严重财务危机

## 输出要求
1. 逐项风险评估
2. 综合风险等级
3. 关键风险因素
4. 风险缓解建议
5. 监控指标建议

请提供具体的数据支撑和专业判断。""",
            description="财务风险评估提示词",
            parameters=["company_name", "company_symbol", "risk_types"],
            language="zh"
        )
        
        # 投资建议提示词
        investment_recommendation_prompt = PromptTemplate(
            name="investment_recommendation",
            template_content="""基于财务分析结果，请为 {{ company_name }}（{{ company_symbol }}）提供投资建议：

## 分析基础
- 财务分析已完成
- 风险评估已完成
- 行业对比已完成

## 建议内容
1. **投资评级**：买入/持有/卖出
2. **目标价位**：基于估值模型
3. **投资期限**：短期/中期/长期
4. **仓位建议**：资金配置比例
5. **风险提示**：主要风险因素

## 考虑因素
- 投资者风险承受能力：{{ risk_tolerance or '中等' }}
- 投资目标：{{ investment_goal or '资本增值' }}
- 市场环境：{{ market_condition or '当前市场环境' }}

## 输出格式
```markdown
# 投资建议报告

## 投资评级
[评级结果及理由]

## 目标价位分析
[估值分析和目标价]

## 投资策略
[具体的投资建议]

## 风险提示
[重要风险提示]

## 免责声明
本建议仅供参考，投资有风险，决策需谨慎
```""",
            description="投资建议提示词",
            parameters=["company_name", "company_symbol", "risk_tolerance", "investment_goal", "market_condition"],
            language="zh"
        )
        
        # 注册模板
        self.register_template(financial_analysis_prompt, category="analysis")
        self.register_template(profitability_prompt, category="analysis") 
        self.register_template(risk_assessment_prompt, category="risk")
        self.register_template(investment_recommendation_prompt, category="investment")

    def get_financial_analysis_prompt(
        self,
        company_name: str,
        company_symbol: str,
        analysis_types: List[str] = None,
        **kwargs
    ) -> str:
        """获取财务分析提示词"""
        return self.render_prompt(
            "financial_analysis_main",
            company_name=company_name,
            company_symbol=company_symbol,
            analysis_types=analysis_types or ["盈利能力分析", "偿债能力分析", "运营效率分析"],
            **kwargs
        )

    def get_profitability_prompt(
        self,
        company_name: str,
        company_symbol: str,
        periods: List[str] = None
    ) -> str:
        """获取盈利能力分析提示词"""
        return self.render_prompt(
            "profitability_analysis",
            company_name=company_name,
            company_symbol=company_symbol,
            periods=periods or ["2023", "2022", "2021"]
        )

    def get_risk_assessment_prompt(
        self,
        company_name: str,
        company_symbol: str,
        risk_types: List[str] = None
    ) -> str:
        """获取风险评估提示词"""
        return self.render_prompt(
            "risk_assessment",
            company_name=company_name,
            company_symbol=company_symbol,
            risk_types=risk_types
        )

    def get_investment_recommendation_prompt(
        self,
        company_name: str,
        company_symbol: str,
        risk_tolerance: str = "中等",
        **kwargs
    ) -> str:
        """获取投资建议提示词"""
        return self.render_prompt(
            "investment_recommendation",
            company_name=company_name,
            company_symbol=company_symbol,
            risk_tolerance=risk_tolerance,
            **kwargs
        )


# 全局提示词管理器实例
_prompt_manager = None


def get_prompt_manager() -> PromptManager:
    """获取全局提示词管理器"""
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = FinancialPromptManager()
    return _prompt_manager


def get_financial_prompt_manager() -> FinancialPromptManager:
    """获取财务提示词管理器"""
    return get_prompt_manager()