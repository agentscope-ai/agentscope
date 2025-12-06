# 代码审查助手示例

## 示例介绍

这个示例展示了如何使用 AgentScope 创建一个**代码审查助手**，它可以帮助开发者审查代码、发现潜在问题、提供改进建议，并推荐最佳实践。

## 功能特性

- ✅ **代码分析**: 分析代码结构、逻辑和潜在问题
- ✅ **Bug 检测**: 识别代码中的错误和潜在的 bug
- ✅ **安全审查**: 发现安全漏洞和风险
- ✅ **性能优化**: 提供性能改进建议
- ✅ **代码风格**: 检查代码风格和可读性
- ✅ **最佳实践**: 推荐编程最佳实践和设计模式

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

### 示例 1: 审查文件

```
用户: 请审查 main.py 这个文件

助手:
[助手会读取文件内容并进行全面审查]

审查结果：
1. **代码结构**: 整体结构清晰...
2. **潜在问题**:
   - 第 15 行：变量名不够描述性
   - 第 23 行：缺少异常处理
3. **改进建议**: ...
```

### 示例 2: 审查代码片段

```
用户: 请审查这段代码：
def calculate_total(items):
    total = 0
    for item in items:
        total += item.price
    return total

助手:
代码审查结果：

✅ **优点**:
- 函数逻辑清晰
- 代码简洁易懂

⚠️ **问题**:
1. 缺少输入验证：如果 items 为 None 会报错
2. 没有处理价格可能为负数的情况
3. 函数缺少文档字符串

💡 **改进建议**:
def calculate_total(items):
    """计算商品总价"""
    if not items:
        return 0.0

    total = 0.0
    for item in items:
        if item.price < 0:
            raise ValueError(f"商品 {item.name} 价格不能为负数")
        total += item.price
    return total
```

### 示例 3: 安全审查

```
用户: 这段代码有什么安全问题吗？
user_input = input("请输入用户名: ")
query = f"SELECT * FROM users WHERE username = '{user_input}'"

助手:
🔴 **严重安全问题**: SQL 注入漏洞

问题分析：
- 直接将用户输入拼接到 SQL 查询中
- 恶意用户可能输入 ' OR '1'='1 来绕过验证

修复建议：
使用参数化查询：
query = "SELECT * FROM users WHERE username = ?"
cursor.execute(query, (user_input,))
```

### 示例 4: 性能优化

```
用户: 如何优化这段代码的性能？
data = []
for i in range(1000000):
    data.append(i * 2)

助手:
性能优化建议：

当前代码的问题：
- 使用 append 在循环中逐步扩展列表，效率较低

优化方案：
1. 使用列表推导式（推荐）：
   data = [i * 2 for i in range(1000000)]

2. 或使用生成器（如果不需要一次性生成所有数据）：
   data = (i * 2 for i in range(1000000))

性能提升：约 2-3 倍
```

### 示例 5: 最佳实践咨询

```
用户: Python 函数命名的最佳实践是什么？

助手:
Python 函数命名最佳实践：

1. **使用小写字母和下划线**：
   ✅ 正确：calculate_total_price
   ❌ 错误：CalculateTotalPrice (这是类名风格)

2. **使用描述性名称**：
   ✅ 正确：get_user_by_id
   ❌ 错误：get_u

3. **动词开头**：
   ✅ 正确：process_data, validate_input
   ❌ 错误：data_processor, input_validator

4. **避免缩写**：
   ✅ 正确：calculate_average
   ❌ 错误：calc_avg

更多内容请参考 PEP 8 风格指南...
```

## 技术实现

### 工具使用

代码审查助手使用了以下工具：

1. **`view_text_file`**: 读取和分析代码文件
   ```python
   # 助手会自动读取文件内容
   await view_text_file("path/to/file.py")
   ```

2. **`execute_python_code`**: 测试代码逻辑
   ```python
   # 可以执行代码来验证逻辑
   await execute_python_code("code_snippet")
   ```

### 系统提示设计

助手使用了详细的系统提示来指导其行为：

```python
sys_prompt = """
You are an expert code reviewer assistant.
You help developers improve their code quality by:
- Analyzing code for bugs and potential issues
- Suggesting improvements for code readability
- Identifying security vulnerabilities
- Recommending best practices
...
"""
```

## 扩展功能

你可以根据需要扩展这个示例：

### 1. 添加代码格式化检查

```python
@tool
async def check_code_format(filepath: str) -> dict:
    """检查代码格式是否符合 PEP 8"""
    import subprocess
    result = subprocess.run(
        ["flake8", filepath],
        capture_output=True,
        text=True,
    )
    return {"issues": result.stdout.splitlines()}
```

### 2. 添加复杂度分析

```python
@tool
async def analyze_complexity(filepath: str) -> dict:
    """分析代码复杂度"""
    import radon.complexity as cc_mod

    with open(filepath) as f:
        code = f.read()

    complexity = cc_mod.cc_visit(code)
    return {"complexity": [str(c) for c in complexity]}
```

### 3. 添加依赖分析

```python
@tool
async def analyze_dependencies(filepath: str) -> dict:
    """分析代码依赖"""
    import ast

    with open(filepath) as f:
        tree = ast.parse(f.read())

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend([alias.name for alias in node.names])
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module)

    return {"imports": imports}
```

### 4. 集成静态分析工具

```python
# 可以集成 pylint, mypy, bandit 等工具
import pylint.lint

@tool
async def run_pylint(filepath: str) -> dict:
    """运行 pylint 检查"""
    results = pylint.lint.Run([filepath], exit=False)
    return {"score": results.linter.stats.global_note}
```

## 应用场景

- 🔍 **代码质量保证**: 在代码提交前进行审查
- 👥 **团队协作**: 帮助团队成员提高代码质量
- 📚 **学习辅助**: 帮助初学者理解最佳实践
- 🔒 **安全审计**: 识别安全漏洞
- ⚡ **性能优化**: 发现性能瓶颈

## 注意事项

⚠️ **审查准确性**:
- AI 助手的建议仅供参考，需要人工验证
- 对于复杂逻辑，建议结合专业工具进行深度分析

⚠️ **上下文理解**:
- 助手可能无法完全理解整个项目的上下文
- 建议提供相关的背景信息和代码注释

⚠️ **工具安全性**:
- 使用 `execute_python_code` 时要注意代码安全性
- 避免执行恶意或未经验证的代码

## 相关文档

- [AgentScope 工具文档](https://doc.agentscope.io/tutorial/task_tool.html)
- [代码审查最佳实践](https://google.github.io/eng-practices/review/)
- [PEP 8 Python 风格指南](https://pep8.org/)

## 贡献

欢迎提交 Issue 或 Pull Request 来改进这个示例！

