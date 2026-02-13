# 工程化财务经营分析智能体

这是一个基于AgentScope框架开发的工程化财务经营分析智能体项目，集成了所有AgentScope高级特性，提供全面的财务数据分析、报告生成和投资建议功能。

## 🚀 高级特性

### 🧠 Agent Skill集成
- **Anthropic Agent Skill**: 专业的财务分析、市场研究、风险评估技能
- **技能注册表**: 统一管理和发现可用技能
- **动态技能加载**: 支持运行时技能注册和调用
- **技能参数验证**: 自动验证技能参数完整性

### 🔌 MCP (Model Context Protocol)集成
- **多数据源支持**: 集成外部MCP服务（财务数据、市场数据、新闻数据）
- **细粒度控制**: 支持stateless和stateful两种MCP客户端模式
- **工具自动注册**: MCP工具自动转换为AgentScope工具
- **服务发现**: 动态发现和管理MCP服务

### 🤝 A2A (Agent-to-Agent)通信
- **智能体协作**: 多个智能体协同完成复杂财务分析任务
- **消息路由**: 支持智能体间的消息传递和路由
- **工作流引擎**: 预定义的财务分析工作流程
- **注册表管理**: 智能体注册、发现和能力管理

### 📝 提示词管理系统
- **模板化提示词**: 使用Jinja2模板引擎管理提示词
- **多语言支持**: 支持中英文提示词模板
- **动态参数填充**: 支持上下文相关的提示词生成
- **分类管理**: 按功能和场景分类管理提示词

### 🔧 工具管理和配置
- **统一工具注册**: 集中管理所有工具的注册和配置
- **工具分组**: 支持按功能分组管理工具
- **配置热重载**: 支持配置文件动态加载
- **依赖管理**: 工具依赖关系管理

### 🏗️ 工程化实践
- **配置管理**: JSON格式的统一配置文件
- **日志系统**: 完整的日志记录和监控
- **错误处理**: 健壮的异常处理和错误恢复
- **性能监控**: 内置性能指标收集和监控

## 📊 核心功能

### 🔍 数据获取
- 支持Yahoo Finance、Alpha Vantage等多种数据源
- 获取财务报表（利润表、资产负债表、现金流量表）
- 获取股价数据和市场信息
- 支持历史数据和实时数据
- MCP协议数据源集成

### 📈 财务分析
- **盈利能力分析**: 毛利率、净利率、ROE、ROA等
- **偿债能力分析**: 负债比率、流动比率、速动比率等
- **运营效率分析**: 资产周转率、存货周转率、应收账款周转率等
- **趋势分析**: 收入增长、利润增长、资产变化等趋势
- **行业对比**: 与行业基准进行比较分析

### 📊 报告生成
- 支持HTML、Markdown、JSON多种格式
- 自动生成可视化图表（折线图、柱状图、饼图、热力图）
- 提供详细的投资建议和风险提示
- 支持批量报告生成
- 模板化报告生成

### 🤖 智能功能
- 基于ReAct架构的智能推理
- 支持工具自主调用
- 实时数据更新
- 交互式分析对话
- 多智能体协作分析

## 项目结构

```
financial_analysis_agent/
├── __init__.py                      # 项目初始化
├── financial_analysis_agent.py      # 基础财务分析智能体
├── engineered_agent.py              # 工程化财务分析智能体
├── demo.py                          # 基础使用示例
├── engineered_demo.py              # 工程化特性演示
├── tools/                           # 工具模块
│   ├── __init__.py                   # 工具模块初始化
│   ├── data_fetcher.py              # 数据获取工具
│   ├── analyzer.py                  # 财务分析工具
│   ├── report_generator.py          # 报告生成工具
│   └── manager.py                   # 工具管理和配置系统
├── skills/                          # Agent Skill模块
│   └── __init__.py                   # 财务分析技能定义
├── mcp/                            # MCP集成模块
│   └── __init__.py                   # MCP客户端和管理器
├── a2a/                            # A2A通信模块
│   └── __init__.py                   # 智能体通信和工作流
├── prompts/                         # 提示词管理模块
│   ├── __init__.py                   # 提示词管理系统
│   └── analysis_prompts.json        # 分析提示词模板
├── config/                         # 配置文件
│   ├── agent_config.json            # 智能体配置
│   ├── mcp_config.json              # MCP服务配置
│   └── tools_config.json            # 工具配置
├── data/                           # 数据目录
│   └── sample_financial_data.json  # 示例财务数据
├── reports/                        # 报告输出目录
├── logs/                           # 日志目录
└── requirements.txt                 # 依赖包列表
```

## 安装依赖

```bash
# 安装AgentScope
pip install agentscope

# 安装财务分析相关依赖
pip install yfinance pandas numpy matplotlib seaborn plotly jinja2

# 安装MCP相关依赖（可选）
pip install aiohttp mcp

# 安装所有依赖
pip install -r requirements.txt
```

## 环境配置

设置必要的环境变量：

```bash
# OpenAI API密钥（用于智能体推理）
export OPENAI_API_KEY="your-openai-api-key"

# Alpha Vantage API密钥（可选，用于获取财务数据）
export ALPHA_VANTAGE_API_KEY="your-alpha-vantage-api-key"
```

## 快速开始

### 基础使用

```python
import asyncio
from financial_analysis_agent import FinancialAnalysisAgent

async def main():
    # 创建财务分析智能体
    agent = FinancialAnalysisAgent()
    
    # 分析公司财务状况
    result = await agent.analyze_company(
        company_code="AAPL",  # 苹果公司
        analysis_type="comprehensive",
        period="annual",
        years=3
    )
    
    print(f"分析结果: {result}")

# 运行
asyncio.run(main())
```

### 交互式分析

```python
from agentscope.message import Msg

# 创建智能体
agent = FinancialAnalysisAgent()

# 发送分析请求
user_msg = Msg(
    name="user",
    content="请分析微软公司(MSFT)的财务状况，重点关注盈利能力和偿债风险",
    role="user"
)

# 获取分析结果
response = await agent(user_msg)
print(response.content)
```

### 运行演示程序

```bash
# 进入项目目录
cd examples/financial_analysis_agent

# 运行演示
python demo.py
```

## 核心组件

### 1. FinancialAnalysisAgent

主要的财务分析智能体类，继承自`ReActAgent`，具备以下功能：

- **智能推理**: 基于LLM的财务问题理解和分析规划
- **工具调用**: 自主调用数据获取、分析和报告生成工具
- **对话管理**: 支持多轮对话和上下文记忆

### 2. FinancialDataFetcher

财务数据获取工具，支持：

- 多数据源集成（Yahoo Finance、Alpha Vantage等）
- 财务报表数据获取
- 股价和市场数据获取
- 经济指标数据获取

### 3. FinancialAnalyzer

财务分析计算工具，提供：

- 各种财务比率计算
- 行业基准对比
- 趋势分析
- 风险评估

### 4. ReportGenerator

报告生成工具，支持：

- 多格式报告生成（HTML、Markdown、JSON）
- 可视化图表创建
- 投资建议生成
- 风险提示

## 使用场景

### 1. 投资分析
```python
# 分析投资标的
result = await agent.analyze_company("TSLA", analysis_type="comprehensive")
```

### 2. 风险评估
```python
# 评估财务风险
user_msg = Msg("user", "分析特斯拉的财务风险，重点关注债务水平", "user")
response = await agent(user_msg)
```

### 3. 批量分析
```python
# 批量分析多家公司
companies = ["AAPL", "MSFT", "GOOGL", "AMZN"]
for company in companies:
    result = await agent.analyze_company(company_code=company)
    # 处理分析结果
```

### 4. 报告生成
```python
# 生成详细报告
from tools.report_generator import ReportGenerator

generator = ReportGenerator()
report = await generator.generate_financial_report(
    analysis_result=result,
    report_type="comprehensive",
    format_type="html"
)
```

## 扩展功能

### 添加新的数据源

```python
# 在FinancialDataFetcher中添加新方法
async def get_custom_data(self, symbol: str) -> ToolResponse:
    # 实现自定义数据获取逻辑
    pass
```

### 自定义分析指标

```python
# 在FinancialAnalyzer中添加新的分析方法
async def calculate_custom_ratios(self, data: Dict) -> ToolResponse:
    # 实现自定义比率计算
    pass
```

### 扩展报告格式

```python
# 在ReportGenerator中添加新的报告格式
def _generate_custom_report(self, analysis_result: Dict) -> str:
    # 实现自定义报告格式
    pass
```

## 注意事项

1. **API限制**: 免费API可能有调用频率限制，建议合理安排调用频率
2. **数据准确性**: 财务数据可能存在延迟，建议以官方数据为准
3. **投资风险**: 本工具仅供参考，投资决策需谨慎
4. **环境配置**: 确保所有依赖库正确安装，特别是可视化库

## 许可证

本项目基于Apache License 2.0开源协议。

## 贡献

欢迎提交Issue和Pull Request来改进这个项目！

## 支持

如有问题或建议，请通过以下方式联系：

- 提交GitHub Issue
- 查看AgentScope文档: https://doc.agentscope.io/zh_CN/
- 加入社区讨论