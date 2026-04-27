# Tool 模块与 MCP 协议深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [Tool 基类设计](#2-tool-基类设计)
3. [Toolkit 工具包核心](#3-toolkit-工具包核心)
4. [内置工具分析](#4-内置工具分析)
5. [MCP 协议实现](#5-mcp-协议实现)
6. [工具调用流程](#6-工具调用流程)
7. [自定义工具开发指南](#7-自定义工具开发指南)
8. [代码示例](#8-代码示例)
9. [练习题](#9-练习题)

---

## 1. 模块概述

### 1.1 目录结构

```
src/agentscope/tool/
├── __init__.py                   # 模块导出
├── _toolkit.py                  # Toolkit 核心类
├── _response.py                 # ToolResponse 响应类
├── _types.py                    # 类型定义
├── _async_wrapper.py            # 异步包装器
├── _coding/                    # 编码相关工具
│   ├── __init__.py
│   ├── _shell.py               # Shell 命令执行
│   └── _python.py              # Python 代码执行
├── _text_file/                 # 文本文件操作
│   ├── __init__.py
│   ├── _view_text_file.py      # 查看文件
│   ├── _write_text_file.py     # 写入文件
│   └── _utils.py               # 工具函数
└── _multi_modality/            # 多模态工具
    ├── __init__.py
    ├── _openai_tools.py         # OpenAI 多模态
    └── _dashscope_tools.py     # DashScope 多模态

src/agentscope/mcp/
├── __init__.py                 # 模块导出
├── _client_base.py             # MCP 客户端基类
├── _mcp_function.py            # MCP 工具函数
├── _stateful_client_base.py    # 有状态客户端基类
├── _stdio_stateful_client.py   # StdIO 有状态客户端
├── _http_stateless_client.py   # HTTP 无状态客户端
└── _http_stateful_client.py    # HTTP 有状态客户端
```

### 1.2 核心组件

| 组件 | 说明 |
|------|------|
| `Toolkit` | 工具注册、管理和调用的核心类 |
| `ToolResponse` | 工具响应数据结构 |
| `ToolUseBlock` | 工具调用请求块 |
| `MCPClientBase` | MCP 客户端基类 |
| `MCPToolFunction` | MCP 工具函数包装 |

---

## 2. Tool 基类设计

### 2.1 ToolResponse 响应类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/tool/_response.py`

```python
class ToolResponse:
    """Tool response class that represents the result of a tool call."""

    def __init__(
        self,
        content: list[TextBlock | ImageBlock | AudioBlock | VideoBlock],
        stream: bool = False,
        is_last: bool = False,
        is_interrupted: bool = False,
        metadata: dict | None = None,
    ) -> None:
        """Initialize a ToolResponse.

        Args:
            content: List of content blocks
            stream: Whether this is a streaming response
            is_last: Whether this is the last chunk
            is_interrupted: Whether the execution was interrupted
            metadata: Additional metadata
        """
        self.content = content
        self.stream = stream
        self.is_last = is_last
        self.is_interrupted = is_interrupted
        self.metadata = metadata
```

### 2.2 工具函数类型

**文件**: `src/agentscope/types.py` (工具函数定义)

工具函数可以是以下几种形式:

1. **普通函数**: `def my_tool(**kwargs) -> ToolResponse`
2. **异步函数**: `async def my_tool(**kwargs) -> ToolResponse`
3. **生成器函数**: `def my_tool(**kwargs) -> Generator[ToolResponse, None, None]`
4. **异步生成器函数**: `async def my_tool(**kwargs) -> AsyncGenerator[ToolResponse, None]`

---

## 3. Toolkit 工具包核心

### 3.1 类定义

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/tool/_toolkit.py:117`

```python
class Toolkit(StateModule):
    """Toolkit is the core module to register, manage and delete tool
    functions, MCP clients, Agent skills in AgentScope.

    About tool functions:
    - Register and parse JSON schemas from their docstrings automatically.
    - Group-wise tools management, and agentic tools activation/deactivation.
    - Extend the tool function JSON schema dynamically with Pydantic BaseModel.
    - Tool function execution with unified streaming interface.

    About MCP clients:
    - Register tool functions from MCP clients directly.
    - Client-level tool functions removal.

    About Agent skills:
    - Register agent skills from the given directory.
    - Provide prompt for the registered skills to the agent.
    """
```

### 3.2 初始化

**__init__() 方法** (第 152-186 行):

```python
def __init__(
    self,
    agent_skill_instruction: str | None = None,
    agent_skill_template: str | None = None,
) -> None:
    """Initialize the toolkit."""
    super().__init__()

    self.tools: dict[str, RegisteredToolFunction] = {}      # 注册的工具函数
    self.groups: dict[str, ToolGroup] = {}                  # 工具组
    self.skills: dict[str, AgentSkill] = {}                  # Agent 技能
    self._middlewares: list = []                             # 中间件列表

    self._agent_skill_instruction = (
        agent_skill_instruction or self._DEFAULT_AGENT_SKILL_INSTRUCTION
    )
    self._agent_skill_template = (
        agent_skill_template or self._DEFAULT_AGENT_SKILL_TEMPLATE
    )

    # 异步任务管理
    self._async_tasks: dict[str, asyncio.Task] = {}
    self._async_results: dict[str, ToolResponse] = {}
```

### 3.3 工具注册

**register_tool_function() 方法** (第 273-534 行):

```python
def register_tool_function(
    self,
    tool_func: ToolFunction,
    group_name: str | Literal["basic"] = "basic",
    preset_kwargs: dict[str, JSONSerializableObject] | None = None,
    func_name: str | None = None,
    func_description: str | None = None,
    json_schema: dict | None = None,
    include_long_description: bool = True,
    include_var_positional: bool = False,
    include_var_keyword: bool = False,
    postprocess_func: Callable[[ToolUseBlock, ToolResponse], ToolResponse | None] | None = None,
    namesake_strategy: Literal["override", "skip", "raise", "rename"] = "raise",
    async_execution: bool = False,
) -> None:
```

**关键参数**:

| 参数 | 说明 |
|------|------|
| `tool_func` | 要注册的函数 |
| `group_name` | 所属工具组（basic 组始终激活） |
| `preset_kwargs` | 预设参数（不暴露给 Agent） |
| `func_name` | 自定义函数名 |
| `json_schema` | 手动提供的 JSON Schema |
| `namesake_strategy` | 同名冲突处理策略 |
| `async_execution` | 是否异步执行 |

### 3.4 工具组管理

**create_tool_group() 方法** (第 187-220 行):

```python
def create_tool_group(
    self,
    group_name: str,
    description: str,
    active: bool = False,
    notes: str | None = None,
) -> None:
    """Create a tool group to organize tool functions"""
    if group_name in self.groups or group_name == "basic":
        raise ValueError(
            f"Tool group '{group_name}' is already registered in the toolkit.",
        )

    self.groups[group_name] = ToolGroup(
        name=group_name,
        description=description,
        notes=notes,
        active=active,
    )
```

### 3.5 获取 JSON Schema

**get_json_schemas() 方法** (第 558-619 行):

```python
def get_json_schemas(self) -> list[dict]:
    """Get the JSON schemas from the tool functions that belong to the
    active groups."""
    return [
        tool.extended_json_schema
        for tool in self.tools.values()
        if tool.group == "basic" or self.groups[tool.group].active
    ]
```

### 3.6 中间件机制

**_apply_middlewares 装饰器** (第 57-114 行):

```python
def _apply_middlewares(
    func: Callable[..., AsyncGenerator[ToolResponse, None]],
) -> Callable[..., AsyncGenerator[ToolResponse, None]]:
    """Decorator that applies registered middlewares at runtime."""

    @wraps(func)
    async def wrapper(self: "Toolkit", tool_call: ToolUseBlock):
        middlewares = getattr(self, "_middlewares", [])

        if not middlewares:
            async for chunk in await func(self, tool_call):
                yield chunk
            return

        # Build middleware chain
        async def base_handler(**kwargs):
            return await func(self, **kwargs)

        current_handler = base_handler
        for middleware in reversed(middlewares):
            def make_handler(mw, handler):
                async def wrapped(**kwargs):
                    return mw(kwargs, handler)
                return wrapped
            current_handler = make_handler(middleware, current_handler)

        async for chunk in await current_handler(tool_call=tool_call):
            yield chunk

    return wrapper
```

---

## 4. 内置工具分析

### 4.1 编码工具

**文件**: `src/agentscope/tool/_coding/__init__.py`

#### execute_python_code

```python
def execute_python_code(
    code: str,
    session_id: str | None = None,
    timeout: int = 60,
) -> ToolResponse:
    """Execute Python code in a sandboxed environment.

    Args:
        code: Python code to execute
        session_id: Optional session ID for stateful execution
        timeout: Execution timeout in seconds

    Returns:
        ToolResponse containing execution result
    """
```

#### execute_shell_command

```python
def execute_shell_command(
    command: str,
    timeout: int = 60,
    working_dir: str | None = None,
) -> ToolResponse:
    """Execute a shell command.

    Args:
        command: Shell command to execute
        timeout: Execution timeout in seconds
        working_dir: Working directory for the command

    Returns:
        ToolResponse containing command output
    """
```

### 4.2 文本文件工具

**文件**: `src/agentscope/tool/_text_file/__init__.py`

#### view_text_file

```python
def view_text_file(
    file_path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    max_lines: int = 100,
) -> ToolResponse:
    """View the content of a text file.

    Args:
        file_path: Path to the file
        start_line: Start line number (1-indexed)
        end_line: End line number (inclusive)
        max_lines: Maximum number of lines to display

    Returns:
        ToolResponse with file content
    """
```

#### write_text_file

```python
def write_text_file(
    file_path: str,
    content: str,
    append: bool = False,
) -> ToolResponse:
    """Write content to a text file.

    Args:
        file_path: Path to the file
        content: Content to write
        append: If True, append to existing file

    Returns:
        ToolResponse indicating success/failure
    """
```

### 4.3 多模态工具

**文件**: `src/agentscope/tool/_multi_modality/__init__.py`

#### DashScope 多模态工具

```python
dashscope_text_to_image    # 文本生成图像
dashscope_text_to_audio    # 文本生成语音
dashscope_image_to_text    # 图像描述
```

#### OpenAI 多模态工具

```python
openai_text_to_image       # DALL-E 图像生成
openai_text_to_audio       # TTS 语音合成
openai_image_to_text       # Vision 图像描述
openai_audio_to_text       # Whisper 语音转文字
openai_edit_image          # 图像编辑
openai_create_image_variation  # 图像变体
```

---

## 5. MCP 协议实现

### 5.1 MCP 模块结构

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/mcp/__init__.py`

```python
from ._client_base import MCPClientBase
from ._mcp_function import MCPToolFunction
from ._stateful_client_base import StatefulClientBase
from ._stdio_stateful_client import StdIOStatefulClient
from ._http_stateless_client import HttpStatelessClient
from ._http_stateful_client import HttpStatefulClient
```

### 5.2 MCP 客户端基类

**MCPClientBase** (`_client_base.py:18`):

```python
class MCPClientBase:
    """Base class for MCP clients."""

    def __init__(self, name: str) -> None:
        """Initialize the MCP client with a name.

        Args:
            name: Unique identifier for the MCP server
        """
        self.name = name

    @abstractmethod
    async def get_callable_function(
        self,
        func_name: str,
        wrap_tool_result: bool = True,
    ) -> Callable:
        """Get a tool function by its name."""
```

### 5.3 内容类型转换

**_convert_mcp_content_to_as_blocks() 方法** (`_client_base.py:39-101`):

```python
@staticmethod
def _convert_mcp_content_to_as_blocks(
    mcp_content_blocks: list,
) -> List[TextBlock | ImageBlock | AudioBlock | VideoBlock]:
    """Convert MCP content to AgentScope blocks."""

    as_content: list = []
    for content in mcp_content_blocks:
        if isinstance(content, mcp.types.TextContent):
            as_content.append(TextBlock(type="text", text=content.text))
        elif isinstance(content, mcp.types.ImageContent):
            as_content.append(ImageBlock(...))
        elif isinstance(content, mcp.types.AudioContent):
            as_content.append(AudioBlock(...))
        elif isinstance(content, mcp.types.EmbeddedResource):
            # 处理嵌入资源
    return as_content
```

### 5.4 MCP 客户端类型

#### StdIOStatefulClient

通过标准输入输出与 MCP 服务器通信，适用于本地进程。

```python
class StdIOStatefulClient(StatefulClientBase):
    """MCP client using stdio for local processes."""
```

#### HttpStatelessClient

通过 HTTP 与 MCP 服务器通信，适用于无状态的远程服务。

```python
class HttpStatelessClient:
    """MCP client using HTTP for remote stateless servers."""
```

#### HttpStatefulClient

通过 HTTP 与 MCP 服务器通信，支持有状态会话。

```python
class HttpStatefulClient(StatefulClientBase):
    """MCP client using HTTP for remote stateful servers."""
```

### 5.5 MCP 工具函数注册

**文件**: `src/agentscope/mcp/_mcp_function.py`

```python
class MCPToolFunction:
    """Wrapper for MCP tool functions."""

    def __init__(
        self,
        name: str,
        mcp_name: str,
        json_schema: dict,
        callable_func: Callable,
    ) -> None:
        self.name = name
        self.mcp_name = mcp_name
        self.json_schema = json_schema
        self._callable_func = callable_func

    async def __call__(self, **kwargs) -> ToolResponse:
        """Execute the MCP tool function."""
        result = await self._callable_func(**kwargs)
        return result
```

---

## 6. 工具调用流程

### 6.1 完整调用流程

```
┌─────────────────────────────────────────────────────────────┐
│                    Agent.rely()                             │
│                                                             │
│  1. 调用 model 生成响应                                       │
│  2. 检查响应中是否包含 tool_use 块                             │
│  3. 如果有，遍历每个工具调用                                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  ReActAgent._acting()                      │
│                                                             │
│  4. 创建 ToolResultBlock                                    │
│  5. 调用 toolkit.call_tool_function(tool_call)              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Toolkit.call_tool_function()                   │
│                                                             │
│  6. 查找对应的 RegisteredToolFunction                        │
│  7. 检查工具组是否激活                                        │
│  8. 准备参数 (preset_kwargs + input)                         │
│  9. 执行预处理函数（如有）                                     │
│  10. 应用中间件链                                             │
│  11. 调用原始工具函数                                         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  工具函数执行                                │
│                                                             │
│  支持的返回类型:                                              │
│  - ToolResponse (同步)                                      │
│  - AsyncGenerator[ToolResponse] (异步流式)                    │
│  - Generator[ToolResponse] (同步流式)                         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  响应包装与后处理                            │
│                                                             │
│  12. 应用后处理函数（如有）                                    │
│  13. 包装为统一格式                                          │
│  14. 返回 AsyncGenerator[ToolResponse]                       │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 call_tool_function 实现

**文件**: `_toolkit.py:851-1033`

```python
@trace_toolkit
@_apply_middlewares
async def call_tool_function(
    self,
    tool_call: ToolUseBlock,
) -> AsyncGenerator[ToolResponse, None]:
    """Execute the tool function by the ToolUseBlock."""

    # 1. 检查函数是否存在
    if tool_call["name"] not in self.tools:
        return _object_wrapper(
            ToolResponse(
                content=[TextBlock(type="text", text="FunctionNotFoundError...")],
            ),
            None,
        )

    # 2. 获取工具函数
    tool_func = self.tools[tool_call["name"]]

    # 3. 检查工具组是否激活
    if tool_func.group != "basic" and not self.groups[tool_func.group].active:
        return _object_wrapper(
            ToolResponse(
                content=[TextBlock(type="text", text="FunctionInactiveError...")],
            ),
            None,
        )

    # 4. 准备参数
    kwargs = {
        **tool_func.preset_kwargs,
        **(tool_call.get("input", {}) or {}),
    }

    # 5. 准备后处理函数
    if tool_func.postprocess_func:
        partial_postprocess_func = partial(
            tool_func.postprocess_func,
            tool_call,
        )
    else:
        partial_postprocess_func = None

    # 6. 执行工具函数
    try:
        if inspect.iscoroutinefunction(tool_func.original_func):
            res = await tool_func.original_func(**kwargs)
        else:
            res = tool_func.original_func(**kwargs)
    except Exception as e:
        res = ToolResponse(content=[TextBlock(type="text", text=f"Error: {e}")])

    # 7. 处理不同返回类型
    if isinstance(res, AsyncGenerator):
        return _async_generator_wrapper(res, partial_postprocess_func)
    elif isinstance(res, Generator):
        return _sync_generator_wrapper(res, partial_postprocess_func)
    elif isinstance(res, ToolResponse):
        return _object_wrapper(res, partial_postprocess_func)

    raise TypeError("Invalid return type from tool function")
```

---

## 7. 自定义工具开发指南

### 7.1 基础工具函数

```python
from agentscope.tool import ToolResponse
from agentscope.message import TextBlock

def greet(name: str) -> ToolResponse:
    """Greet the user with a personalized message.

    Args:
        name: The name of the person to greet

    Returns:
        A greeting message
    """
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=f"Hello, {name}! How can I help you today?",
            )
        ],
    )

# 注册到工具包
toolkit.register_tool_function(greet)
```

### 7.2 带参数验证的工具

```python
from pydantic import BaseModel, Field
from agentscope.tool import ToolResponse
from agentscope.message import TextBlock

class CalculatorInput(BaseModel):
    """Input for calculator tool."""
    expression: str = Field(description="Mathematical expression to evaluate")
    precision: int = Field(default=2, description="Decimal precision")

def calculator(input: CalculatorInput) -> ToolResponse:
    """Evaluate a mathematical expression.

    Args:
        expression: Mathematical expression (e.g., "2 + 2", "sqrt(16)")
        precision: Number of decimal places
    """
    try:
        import math
        result = eval(input.expression, {"sqrt": math.sqrt, **math.__dict__})
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Result: {round(result, input.precision)}",
                )
            ],
        )
    except Exception as e:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"Error: {str(e)}")],
        )
```

### 7.3 流式工具

```python
from agentscope.tool import ToolResponse
from agentscope.message import TextBlock
from typing import AsyncGenerator

async def stream_words(text: str, delay: float = 0.1) -> AsyncGenerator[ToolResponse, None]:
    """Stream words one by one with a delay.

    Args:
        text: Text to stream
        delay: Delay between words in seconds
    """
    import asyncio

    words = text.split()
    for i, word in enumerate(words):
        await asyncio.sleep(delay)
        yield ToolResponse(
            content=[TextBlock(type="text", text=word + " ")],
            stream=True,
            is_last=(i == len(words) - 1),
        )
```

### 7.4 带后处理的工具

```python
from agentscope.tool import ToolResponse
from agentscope.message import TextBlock

def sensitive_filter(
    tool_call: ToolUseBlock,
    response: ToolResponse,
) -> ToolResponse | None:
    """Filter sensitive information from tool responses."""
    import re

    # 检查是否包含敏感词
    sensitive_pattern = r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b'  # 信用卡号
    filtered_content = []

    for block in response.content:
        if block.get("type") == "text":
            text = re.sub(sensitive_pattern, "[REDACTED]", block["text"])
            filtered_content.append(TextBlock(type="text", text=text))
        else:
            filtered_content.append(block)

    return ToolResponse(
        content=filtered_content,
        metadata=response.metadata,
    )

def get_credit_card_info() -> ToolResponse:
    """This tool might return sensitive data."""
    return ToolResponse(
        content=[TextBlock(type="text", text="Card: 1234-5678-9012-3456")],
    )

# 注册时添加后处理
toolkit.register_tool_function(
    get_credit_card_info,
    postprocess_func=sensitive_filter,
)
```

### 7.5 MCP 工具集成

```python
from agentscope.mcp import HttpStatefulClient
from agentscope.tool import Toolkit

async def register_mcp_tools():
    """Register tools from an MCP server."""
    toolkit = Toolkit()

    # 创建 MCP 客户端
    mcp_client = HttpStatefulClient(
        name="my-mcp-server",
        server_url="https://mcp.example.com",
    )

    # 连接并注册工具
    await mcp_client.connect()
    await toolkit.register_mcp_client(
        mcp_client,
        group_name="mcp_tools",
        enable_funcs=["tool1", "tool2"],  # 只注册部分工具
        disable_funcs=["internal_tool"],   # 排除某些工具
    )

    return toolkit
```

---

## 8. 代码示例

### 8.1 完整工具包配置

```python
from agentscope.tool import Toolkit
from agentscope.tool import (
    execute_python_code,
    execute_shell_command,
    view_text_file,
    write_text_file,
)

# 创建工具包
toolkit = Toolkit()

# 创建工具组
toolkit.create_tool_group(
    group_name="file_operations",
    description="File read and write operations",
    active=False,  # 默认不激活
    notes="Use these tools when user asks to read or write files",
)

toolkit.create_tool_group(
    group_name="code_execution",
    description="Code execution tools",
    active=True,  # 默认激活
)

# 注册工具
toolkit.register_tool_function(
    view_text_file,
    group_name="file_operations",
)

toolkit.register_tool_function(
    write_text_file,
    group_name="file_operations",
)

toolkit.register_tool_function(
    execute_python_code,
    group_name="code_execution",
)

# 获取可用的 JSON Schema
schemas = toolkit.get_json_schemas()
print(f"Active tools: {[s['function']['name'] for s in schemas]}")

# 激活文件操作组
toolkit.update_tool_groups(["file_operations"], active=True)
schemas = toolkit.get_json_schemas()
print(f"After activation: {[s['function']['name'] for s in schemas]}")
```

### 8.2 动态工具管理

```python
from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock

toolkit = Toolkit()

# 动态添加工具
def dynamic_tool(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])

toolkit.register_tool_function(
    dynamic_tool,
    func_name="dynamic_tool",
)

# 运行时添加新工具
def new_tool(x: int, y: int) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=f"{x + y}")])

toolkit.register_tool_function(new_tool)

# 运行时移除工具
toolkit.remove_tool_function("old_tool")

# 查看状态
print(f"Registered tools: {list(toolkit.tools.keys())}")
print(f"Tool groups: {list(toolkit.groups.keys())}")
```

### 8.3 中间件使用

```python
from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock
from typing import AsyncGenerator

async def logging_middleware(
    kwargs: dict,
    next_handler: callable,
) -> AsyncGenerator[ToolResponse, None]:
    """Log all tool calls."""
    tool_call = kwargs["tool_call"]
    print(f"[LOG] Calling tool: {tool_call['name']}")

    async for response in await next_handler(**kwargs):
        print(f"[LOG] Tool {tool_call['name']} returned")
        yield response

async def caching_middleware(
    kwargs: dict,
    next_handler: callable,
) -> AsyncGenerator[ToolResponse, None]:
    """Cache tool results."""
    tool_call = kwargs["tool_call"]
    cache_key = f"{tool_call['name']}:{tool_call.get('input', {})}"

    if cache_key in cache:
        yield cache[cache_key]
        return

    async for response in await next_handler(**kwargs):
        cache[cache_key] = response
        yield response

toolkit = Toolkit()
toolkit.register_middleware(logging_middleware)
toolkit.register_middleware(caching_middleware)
```

---

## 9. 练习题

### 9.1 基础题

1. **分析 Toolkit 类的设计，参考 `_toolkit.py:117-186`。**

2. **Toolkit 如何管理工具组？请说明 basic 组与其他组的区别。**

3. **解释 ToolResponse 的主要字段及其作用。**

### 9.2 进阶题

4. **分析中间件机制的实现，参考 `_apply_middlewares` 装饰器。**

5. **设计一个自定义中间件，实现工具调用的限流。**

6. **分析 MCP 客户端的类型及其适用场景。**

### 9.3 挑战题

7. **实现一个工具执行超时机制，当工具执行超过一定时间自动中断。**

8. **设计一个工具版本管理机制，支持工具的热更新。**

9. **分析 AgentScope 的工具调用与 LangChain 的 Tool Calling 机制的异同。**

---

## 参考资料

- 源码路径: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/tool/`
- MCP 源码路径: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/mcp/`

---

*文档版本: 1.0*
*最后更新: 2026-04-27*
