# 计算器助手示例

## 示例介绍

这个示例展示了如何使用 AgentScope 创建一个**计算器助手**，它可以帮助用户进行各种数学计算，包括基本运算、复杂表达式求值和科学计算。

## 功能特性

- ✅ **基本运算**: 加法、减法、乘法、除法
- ✅ **高级运算**: 幂运算、开方、对数等
- ✅ **表达式求值**: 支持复杂的数学表达式
- ✅ **结构化输出**: 使用 Pydantic 模型确保输出格式
- ✅ **代码执行**: 使用 Python 代码确保计算准确性

## 快速开始

### 1. 环境准备

确保已安装 AgentScope 并设置了环境变量：

```bash
# 设置 DashScope API Key
export DASHSCOPE_API_KEY="your_dashscope_api_key_here"

# Windows PowerShell:
$env:DASHSCOPE_API_KEY="your_dashscope_api_key_here"
```

### 2. 运行示例

```bash
python main.py
```

## 使用示例

### 示例 1: 基本运算

```
用户: 计算 123 + 456
助手: 123 + 456 = 579
```

### 示例 2: 复杂表达式

```
用户: 计算 (25 * 4) / 2 + 10
助手:
首先计算 25 * 4 = 100
然后计算 100 / 2 = 50
最后计算 50 + 10 = 60
答案是 60
```

### 示例 3: 科学计算

```
用户: 计算 2 的 10 次方
助手: 2^10 = 1024
```

### 示例 4: 几何计算

```
用户: 如果一个圆的半径是 5，求它的面积
助手:
圆的面积公式是 π * r²
π ≈ 3.14159
r = 5
面积 = 3.14159 * 5² = 3.14159 * 25 ≈ 78.54
```

### 示例 5: 结构化输出

程序启动时会自动演示结构化输出功能：

```json
{
  "expression": "123 * 456",
  "result": 56088.0,
  "operation": "multiplication"
}
```

## 技术实现

### 结构化输出模型

使用 Pydantic 模型定义计算结果的结构：

```python
class CalculationResult(BaseModel):
    """计算结果的结构化输出模型"""

    expression: str = Field(description="数学表达式")
    result: float = Field(description="计算结果")
    operation: Literal["addition", "subtraction", "multiplication",
                       "division", "power", "other"] = Field(
        description="运算类型"
    )
```

### 工具使用

助手使用 `execute_python_code` 工具来执行精确的计算：

```python
# 当用户询问复杂计算时，助手会使用 Python 代码
# 例如：eval("(25 * 4) / 2 + 10")
```

### 代码结构

```python
# 创建工具包
toolkit = Toolkit()
toolkit.register_tool_function(execute_python_code)

# 创建智能体
calculator = ReActAgent(
    name="Calculator",
    sys_prompt="...",  # 定义助手的计算能力
    toolkit=toolkit,
    # ... 其他配置
)

# 使用结构化输出
result = await calculator(query, structured_model=CalculationResult)
```

## 扩展功能

你可以根据需要扩展这个示例：

### 1. 添加单位转换

```python
@tool
async def convert_units(value: float, from_unit: str, to_unit: str) -> dict:
    """单位转换"""
    # 实现单位转换逻辑
    pass
```

### 2. 添加数学函数

```python
@tool
async def calculate_statistics(numbers: list[float]) -> dict:
    """计算统计信息"""
    import statistics
    return {
        "mean": statistics.mean(numbers),
        "median": statistics.median(numbers),
        "std_dev": statistics.stdev(numbers),
    }
```

### 3. 添加图形绘制

```python
@tool
async def plot_function(expression: str, x_range: tuple) -> str:
    """绘制函数图像"""
    import matplotlib.pyplot as plt
    import numpy as np
    # 实现绘图逻辑
    pass
```

### 4. 添加公式求解

```python
from sympy import symbols, solve

@tool
async def solve_equation(equation: str) -> dict:
    """求解方程"""
    x = symbols('x')
    solutions = solve(equation, x)
    return {"solutions": [str(s) for s in solutions]}
```

## 应用场景

- 📊 **数据分析和处理**: 快速计算统计数据
- 📐 **工程计算**: 工程项目的数学计算
- 🎓 **学习辅助**: 帮助学生理解数学概念
- 💼 **金融计算**: 利息、投资回报等计算
- 🔬 **科学研究**: 科学实验的数据处理

## 注意事项

⚠️ **精度考虑**:
- 对于高精度计算，建议使用 `decimal` 模块
- 浮点数运算可能存在精度问题

⚠️ **安全性**:
- 使用 `execute_python_code` 时要确保代码安全性
- 避免执行用户提供的未经验证的代码

## 相关文档

- [结构化输出文档](https://doc.agentscope.io/tutorial/task_agent.html#structured-output)
- [工具使用文档](https://doc.agentscope.io/tutorial/task_tool.html)
- [Pydantic 文档](https://docs.pydantic.dev/)

## 贡献

欢迎提交 Issue 或 Pull Request 来改进这个示例！

