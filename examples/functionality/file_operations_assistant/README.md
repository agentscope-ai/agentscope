# 文件操作助手示例

## 示例介绍

这个示例展示了如何使用 AgentScope 创建一个**文件操作助手**，它可以帮助用户执行各种文件相关的操作，如读取文件、搜索文件、创建文件等。

## 功能特性

- ✅ **文件读取**: 查看和读取文件内容
- ✅ **文件搜索**: 在当前目录或指定目录中搜索文件
- ✅ **文件创建**: 创建新文件并写入内容
- ✅ **文件操作**: 使用 Python 代码或 Shell 命令进行复杂的文件操作
- ✅ **交互式对话**: 通过自然语言与助手交互

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

启动后，你可以通过自然语言与助手交互：

### 示例 1: 查看文件内容

```
用户: 查看 README.md 的内容
助手: [助手会读取并显示文件内容]
```

### 示例 2: 搜索文件

```
用户: 在当前目录搜索所有 Python 文件
助手: [助手会使用 find 或 dir 命令搜索文件]
```

### 示例 3: 创建文件

```
用户: 创建一个名为 notes.txt 的文件，内容是 "今日任务：学习 AgentScope"
助手: [助手会创建文件并写入指定内容]
```

### 示例 4: 运行 Shell 命令

```
用户: 列出当前目录的所有文件
助手: [助手会执行 ls 或 dir 命令]
```

### 示例 5: 使用 Python 进行复杂操作

```
用户: 统计当前目录下所有 .py 文件的行数
助手: [助手会编写 Python 代码来完成这个任务]
```

## 技术实现

### 工具集成

本示例使用了以下 AgentScope 内置工具：

1. **`execute_shell_command`**: 执行 Shell 命令
   - Windows: `dir`, `cd`, `type` 等
   - Linux/Mac: `ls`, `grep`, `find` 等

2. **`execute_python_code`**: 执行 Python 代码
   - 可以进行复杂的文件处理
   - 支持文件 I/O、JSON 处理等

3. **`view_text_file`**: 查看文本文件内容
   - 安全地读取文件内容
   - 支持大文件的分页查看

### 代码结构

```python
# 创建工具包
toolkit = Toolkit()
toolkit.register_tool_function(execute_shell_command)
toolkit.register_tool_function(execute_python_code)
toolkit.register_tool_function(view_text_file)

# 创建智能体
assistant = ReActAgent(
    name="FileAssistant",
    sys_prompt="...",  # 定义助手的职责和能力
    toolkit=toolkit,   # 注册工具
    # ... 其他配置
)
```

## 扩展功能

你可以根据需要扩展这个示例：

### 1. 添加自定义文件工具

```python
from agentscope.tool import tool

@tool
async def count_file_lines(filepath: str) -> dict:
    """统计文件行数"""
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = len(f.readlines())
    return {"file": filepath, "lines": lines}

# 注册自定义工具
toolkit.register_tool_function(count_file_lines)
```

### 2. 添加文件类型识别

```python
@tool
async def detect_file_type(filepath: str) -> dict:
    """检测文件类型"""
    import mimetypes
    mime_type, _ = mimetypes.guess_type(filepath)
    return {"file": filepath, "type": mime_type}
```

### 3. 集成更多文件操作

- 文件压缩/解压
- 批量重命名
- 文件权限管理
- 文件同步

## 注意事项

⚠️ **安全提示**:
- 助手可以执行文件操作，请谨慎使用
- 建议在测试环境中运行
- 避免授予过高权限

⚠️ **平台差异**:
- Shell 命令在 Windows 和 Linux/Mac 上有所不同
- 助手会根据操作系统自动选择合适的命令

## 相关文档

- [AgentScope 工具文档](https://doc.agentscope.io/tutorial/task_tool.html)
- [ReAct Agent 文档](https://doc.agentscope.io/tutorial/task_agent.html)
- [内置工具列表](https://doc.agentscope.io/api/agentscope.tool.html)

## 贡献

欢迎提交 Issue 或 Pull Request 来改进这个示例！

