# 工具模块与 MCP 协议深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [工具基类设计](#2-工具基类设计)
3. [Toolkit 工具包核心](#3-toolkit-工具包核心)
4. [内置工具分析](#4-内置工具分析)
5. [MCP 协议实现](#5-mcp-协议实现)
6. [工具调用流程](#6-工具调用流程)
7. [自定义工具开发指南](#7-自定义工具开发指南)
8. [代码示例](#8-代码示例)
9. [练习题](#9-练习题)

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举 AgentScope 内置工具的类型和注册方式 | 列举、识别 |
| 理解 | 解释 Toolkit 的工具注册流程、JSON Schema 自动生成机制和中间件洋葱模型 | 解释、描述 |
| 应用 | 使用 `Toolkit.register_tool_function()` 注册工具函数并集成到智能体的工具链中 | 实现、开发 |
| 分析 | 分析 MCP 三种客户端类型（StdIO/HttpStateless/HttpStateful）的适用场景与状态管理差异 | 分析、对比 |
| 评价 | 评价中间件机制与后处理函数 (`postprocess_func`) 在工具调用链路中的执行顺序与适用边界 | 评价、判断 |
| 创造 | 设计一个支持超时控制、错误降级和日志追踪的自定义工具中间件 | 设计、构建 |

## 先修检查

在开始学习本模块之前，请确认您已掌握以下知识：

- [ ] Python 函数装饰器的工作原理
- [ ] JSON Schema 的基本结构和用途
- [ ] 异步生成器 (`async def` + `yield`) 的基本概念
- [ ] 了解什么是进程间通信 (IPC) 和 HTTP API 调用

**预计学习时间**: 35 分钟

---

## 1. 模块概述

### 1.1 目录结构

```
src/agentscope/tool/                    # 工具模块根目录
├── __init__.py                         # 模块导出
├── _toolkit.py                         # Toolkit 核心类
├── _response.py                        # ToolResponse 响应类
├── _types.py                           # 类型定义 (RegisteredToolFunction, ToolGroup)
├── _async_wrapper.py                   # 异步包装器
├── _coding/                            # 编码相关工具
│   ├── __init__.py
│   ├── _python.py                      # Python 代码执行
│   └── _shell.py                       # Shell 命令执行
├── _text_file/                         # 文本文件操作
│   ├── __init__.py
│   ├── _view_text_file.py             # 查看文件
│   ├── _write_text_file.py            # 写入文件
│   └── _utils.py                      # 工具函数
└── _multi_modality/                   # 多模态工具
    ├── __init__.py
    ├── _openai_tools.py               # OpenAI 多模态
    └── _dashscope_tools.py            # DashScope 多模态

src/agentscope/mcp/                    # MCP 模块根目录
├── __init__.py                         # 模块导出
├── _client_base.py                    # MCP 客户端基类
├── _mcp_function.py                   # MCP 工具函数包装
├── _stateful_client_base.py           # 有状态客户端基类
├── _stdio_stateful_client.py          # StdIO 有状态客户端
├── _http_stateless_client.py         # HTTP 无状态客户端
└── _http_stateful_client.py          # HTTP 有状态客户端
```

### 1.2 核心组件

| 组件 | 文件位置 | 说明 |
|------|----------|------|
| `Toolkit` | `_toolkit.py:117` | 工具注册、管理和调用的核心类 |
| `ToolResponse` | `_response.py:12` | 工具响应数据结构 |
| `RegisteredToolFunction` | `_types.py:16` | 已注册工具的内部表示 |
| `ToolGroup` | `_types.py:136` | 工具组定义 |
| `MCPToolFunction` | `_mcp_function.py:18` | MCP 工具函数包装类 |
| `MCPClientBase` | `_client_base.py:17` | MCP 客户端基类 |
| `StatefulClientBase` | `_stateful_client_base.py:18` | 有状态 MCP 客户端基类 |

---

## 2. 工具基类设计

> 注：行号为 v1.0.19 版本参考值，不同版本可能有所变动，建议以类名/方法名定位。

### 2.1 ToolResponse 响应类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/tool/_response.py:12`

```python
@dataclass
class ToolResponse:
    """The result chunk of a tool call."""

    content: List[TextBlock | ImageBlock | AudioBlock | VideoBlock]
    """The execution output of the tool function."""

    metadata: Optional[dict] = None
    """The metadata to be accessed within the agent, so that we don't need to
    parse the tool result block."""

    stream: bool = False
    """Whether the tool output is streamed."""

    is_last: bool = True
    """Whether this is the last response in a stream tool execution."""

    is_interrupted: bool = False
    """Whether the tool execution is interrupted."""

    id: str = field(default_factory=lambda: _get_timestamp(True))
    """The identity of the tool response."""
```

**设计分析**:
- `ToolResponse` 是工具执行结果的统一包装类，采用数据类（dataclass）设计
- `content` 字段使用联合类型支持多种内容类型：文本、图像、音频、视频
- `stream` + `is_last` 组合支持流式响应机制
- `is_interrupted` 用于处理用户中断场景
- `metadata` 字段允许工具返回结构化元数据，避免 Agent 解析文本

### 2.2 RegisteredToolFunction 注册工具函数类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/tool/_types.py:16`

```python
@dataclass
class RegisteredToolFunction:
    """The registered tool function class."""

    name: str
    """The name of the tool function."""
    group: str | Literal["basic"]
    """The belonging group of the tool function"""
    source: Literal["function", "mcp_server", "function_group"]
    """The type of the tool function"""
    original_func: ToolFunction
    """The original function"""
    json_schema: dict
    """The JSON schema of the tool function"""
    preset_kwargs: dict[str, JSONSerializableObject] = field(default_factory=dict)
    """Preset keyword arguments, which won't be presented in the JSON schema."""
    original_name: str | None = None
    """The original name when renamed."""
    extended_model: Type[BaseModel] | None = None
    """The base model to extend the JSON schema dynamically."""
    mcp_name: str | None = None
    """The name of the MCP server if from MCP."""
    postprocess_func: (
        Callable[[ToolUseBlock, ToolResponse], ToolResponse | None]
        | Callable[[ToolUseBlock, ToolResponse], Awaitable[ToolResponse | None]]
    ) | None = None
    """Post-processing function after tool execution."""
    async_execution: bool = False
    """Whether to execute asynchronously."""

    @property
    def extended_json_schema(self) -> dict:
        """Get merged JSON schema with extended model."""
        # ... 合并逻辑见下文
```

**extended_json_schema 属性** (第 52-98 行):
- 当设置了 `extended_model` (Pydantic BaseModel) 时，动态合并 JSON Schema
- 合并 `$defs` 支持嵌套模型定义
- 检测字段冲突避免覆盖原有定义

**设计模式**: 注册器模式 (Registry Pattern)
- `RegisteredToolFunction` 作为注册表条目
- `Toolkit.tools` 字典作为注册表存储
- 支持同名冲突处理策略（override/skip/raise/rename）

### 2.3 工具函数类型

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/types.py`

工具函数四种形式:

1. **普通函数**: `def my_tool(**kwargs) -> ToolResponse`
2. **异步函数**: `async def my_tool(**kwargs) -> ToolResponse`
3. **生成器函数**: `def my_tool(**kwargs) -> Generator[ToolResponse, None, None]`
4. **异步生成器函数**: `async def my_tool(**kwargs) -> AsyncGenerator[ToolResponse, None]`

### 2.4 异步包装器

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/tool/_async_wrapper.py`

异步包装器将不同返回类型的工具函数统一转换为 `AsyncGenerator[ToolResponse, None]`:

```python
# _async_wrapper.py:46-54
async def _object_wrapper(
    obj: ToolResponse,
    postprocess_func: Callable[[ToolResponse], ToolResponse | None] | None,
) -> AsyncGenerator[ToolResponse, None]:
    """Wrap a ToolResponse object to an async generator."""
    yield await _postprocess_tool_response(obj, postprocess_func)

# _async_wrapper.py:57-66
async def _sync_generator_wrapper(
    sync_generator: Generator[ToolResponse, None, None],
    postprocess_func: Callable[[ToolResponse], ToolResponse | None] | None,
) -> AsyncGenerator[ToolResponse, None]:
    """Wrap a sync generator to an async generator."""
    for chunk in sync_generator:
        yield await _postprocess_tool_response(chunk, postprocess_func)

# _async_wrapper.py:69-104
async def _async_generator_wrapper(
    async_func: AsyncGenerator[ToolResponse, None],
    postprocess_func: Callable[[ToolResponse], ToolResponse | None] | None,
) -> AsyncGenerator[ToolResponse, None]:
    """Wrap async generator and handle CancelledError."""
    last_chunk = None
    try:
        async for chunk in async_func:
            processed_chunk = await _postprocess_tool_response(chunk, postprocess_func)
            yield processed_chunk
            last_chunk = processed_chunk
    except asyncio.CancelledError:
        # 添加中断信息到最后一个块
        interrupted_info = TextBlock(...)
        if last_chunk:
            last_chunk.content.append(interrupted_info)
            last_chunk.is_interrupted = True
            last_chunk.is_last = True
            yield await _postprocess_tool_response(last_chunk, postprocess_func)
        else:
            yield await _postprocess_tool_response(
                ToolResponse(content=[interrupted_info], is_interrupted=True, is_last=True),
                postprocess_func,
            )
```

**设计分析**:
- **适配器模式**: 三种包装器将不同返回类型统一为 `AsyncGenerator`
- **责任链模式**: `postprocess_func` 通过管道传递给每个包装器
- **中断处理**: `_async_generator_wrapper` 捕获 `asyncio.CancelledError`，确保用户中断时返回有效的响应

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
    - 工具函数执行采用统一流式接口。

    About MCP clients:
    - Register tool functions from MCP clients directly.
    - Client-level tool functions removal.

    About Agent skills:
    - Register agent skills from the given directory.
    - Provide prompt for the registered skills to the agent.
    """
```

**继承关系**: `Toolkit` 继承自 `StateModule`，获得状态管理能力

### 3.2 初始化

**文件**: `_toolkit.py:152-186`

```python
def __init__(
    self,
    agent_skill_instruction: str | None = None,
    agent_skill_template: str | None = None,
) -> None:
    """Initialize the toolkit."""
    super().__init__()

    self.tools: dict[str, RegisteredToolFunction] = {}      # 已注册工具
    self.groups: dict[str, ToolGroup] = {}                  # 工具组
    self.skills: dict[str, AgentSkill] = {}                  # Agent 技能
    self._middlewares: list = []                             # 中间件列表

    self._agent_skill_instruction = (
        agent_skill_instruction or self._DEFAULT_AGENT_SKILL_INSTRUCTION
    )
    self._agent_skill_template = (
        agent_skill_template or self._DEFAULT_AGENT_SKILL_TEMPLATE
    )

    # 异步任务管理 (用于 async_execution 模式)
    self._async_tasks: dict[str, asyncio.Task] = {}
    self._async_results: dict[str, ToolResponse] = {}
```

### 3.3 工具注册

**文件**: `_toolkit.py:273-534` (`register_tool_function` 方法)

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

**关键参数详解**:

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `tool_func` | 要注册的函数 | - |
| `group_name` | 所属工具组（basic 组始终激活） | "basic" |
| `preset_kwargs` | 预设参数（不暴露给 Agent，如 API 密钥） | None |
| `func_name` | 自定义函数名（用于重命名） | None |
| `json_schema` | 手动提供 JSON Schema（跳过自动解析） | None |
| `namesake_strategy` | 同名冲突处理策略 | "raise" |
| `async_execution` | 是否在后台异步执行 | False |
| `postprocess_func` | 工具执行后的后处理函数 | None |

**注册流程** (第 410-536 行):
1. 参数检查和类型验证
2. 解析工具函数类型（普通函数/partial/MCP工具）
3. 调用 `_parse_tool_function()`（位于 `_utils/_common.py:339`）从函数签名和 docstring 自动生成 JSON Schema，内部使用 `docstring_parser` 解析文档、`inspect.signature` 提取参数、`pydantic.create_model()` 动态构建 Schema
4. 从 Schema 中移除 preset_kwargs 对应的字段
5. 创建 `RegisteredToolFunction` 对象
6. 根据 `namesake_strategy` 处理同名冲突

### 3.4 工具组管理

**文件**: `_toolkit.py:187-220`

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

**工具组特性**:
- `basic` 组是特殊组，始终激活，不可删除
- 非 basic 组默认不激活（`active=False`）
- 组激活状态可通过 `update_tool_groups()` 动态修改

### 3.5 获取 JSON Schema

**文件**: `_toolkit.py:558-619`

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

**文件**: `_toolkit.py:57-114`

```python
def _apply_middlewares(
    func: Callable[
        ...,
        Coroutine[Any, Any, AsyncGenerator[ToolResponse, None]],
    ],
) -> Callable[..., AsyncGenerator[ToolResponse, None]]:
    """Decorator that applies registered middlewares at runtime."""

    @wraps(func)
    async def wrapper(
        self: "Toolkit",
        tool_call: ToolUseBlock,
    ) -> AsyncGenerator[ToolResponse, None]:
        middlewares = getattr(self, "_middlewares", [])

        if not middlewares:
            async for chunk in await func(self, tool_call):
                yield chunk
            return

        # 从内到外构建中间件链
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

**中间件设计**:
- **责任链模式**: 最后注册的中间件在最外层（反向构建）
- 中间件签名: `async def middleware(kwargs: dict, next_handler: callable) -> AsyncGenerator[ToolResponse, None]`
- 中间件必须是 async generator function

---

## 4. 内置工具分析

### 4.1 编码工具

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/tool/_coding/__init__.py`

#### execute_python_code

**文件**: `_coding/_python.py`

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

**文件**: `_coding/_shell.py`

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

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/tool/_text_file/__init__.py`

#### view_text_file

**文件**: `_text_file/_view_text_file.py`

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

**文件**: `_text_file/_write_text_file.py`

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

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/tool/_multi_modality/__init__.py`

#### DashScope 多模态工具

| 工具函数 | 说明 |
|----------|------|
| `dashscope_text_to_image` | 文本生成图像（阿里云 DashScope） |
| `dashscope_text_to_audio` | 文本生成语音 |
| `dashscope_image_to_text` | 图像描述/OCR |

#### OpenAI 多模态工具

| 工具函数 | 说明 |
|----------|------|
| `openai_text_to_image` | DALL-E 图像生成 |
| `openai_text_to_audio` | TTS 语音合成 |
| `openai_image_to_text` | Vision 图像描述 |
| `openai_audio_to_text` | Whisper 语音转文字 |
| `openai_edit_image` | 图像编辑 |
| `openai_create_image_variation` | 图像变体 |

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

**MCPClientBase** (`_client_base.py:17-76`):

```python
class MCPClientBase:
    """Base class for MCP clients."""

    def __init__(self, name: str) -> None:
        """Initialize the MCP client with a name."""
        self.name = name

    @abstractmethod
    async def get_callable_function(
        self,
        func_name: str,
        wrap_tool_result: bool = True,
    ) -> Callable:
        """Get a tool function by its name."""

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
                as_content.append(ImageBlock(type="image", source=Base64Source(...)))
            elif isinstance(content, mcp.types.AudioContent):
                as_content.append(AudioBlock(type="audio", source=Base64Source(...)))
            elif isinstance(content, mcp.types.EmbeddedResource):
                if isinstance(content.resource, mcp.types.TextResourceContents):
                    as_content.append(TextBlock(...))
        return as_content
```

**设计分析**:
- 抽象基类定义 MCP 客户端接口
- `_convert_mcp_content_to_as_blocks` 是适配器方法，将 MCP 协议内容块转换为 AgentScope 内部格式
- 支持 TextContent、ImageContent、AudioContent、EmbeddedResource 四种类型

### 5.3 MCPToolFunction MCP 工具函数包装类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/mcp/_mcp_function.py:18-113`

```python
class MCPToolFunction:
    """An MCP tool function class that can be called directly."""

    name: str
    description: str
    json_schema: dict[str, Any]

    def __init__(
        self,
        mcp_name: str,
        tool: mcp.types.Tool,
        wrap_tool_result: bool,
        client_gen: Callable[..., _AsyncGeneratorContextManager[Any]] | None = None,
        session: ClientSession | None = None,
        timeout: float | None = None,
    ) -> None:
        # ... 初始化逻辑
        self.client_gen = client_gen  # 无状态客户端使用
        self.session = session          # 有状态客户端使用

    async def __call__(self, **kwargs: Any) -> mcp.types.CallToolResult | ToolResponse:
        """Call the MCP tool function."""
        if self.client_gen:
            # 无状态模式：每次调用创建新会话
            async with self.client_gen() as cli:
                read_stream, write_stream = cli[0], cli[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    res = await session.call_tool(self.name, arguments=kwargs, ...)
        else:
            # 有状态模式：使用已有会话
            res = await self.session.call_tool(self.name, arguments=kwargs, ...)

        if self.wrap_tool_result:
            as_content = MCPClientBase._convert_mcp_content_to_as_blocks(res.content)
            return ToolResponse(content=as_content, metadata=res.meta)
        return res
```

**关键设计**:
- `client_gen` 和 `session` 互斥，必须提供其中一个
- `wrap_tool_result` 控制是否转换为 AgentScope 格式
- 支持超时控制 (`timeout`)

### 5.4 StatefulClientBase 有状态客户端基类

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/mcp/_stateful_client_base.py:18-155`

```python
class StatefulClientBase(MCPClientBase, ABC):
    """The base class for stateful MCP clients, which maintains
    the session state across multiple tool calls."""

    is_connected: bool

    def __init__(self, name: str) -> None:
        super().__init__(name=name)
        self.client = None
        self.stack = None
        self.session = None
        self.is_connected = False
        self._cached_tools = None

    async def connect(self) -> None:
        """Connect to MCP server."""
        self.stack = AsyncExitStack()
        context = await self.stack.enter_async_context(self.client)
        read_stream, write_stream = context[0], context[1]
        self.session = ClientSession(read_stream, write_stream)
        await self.stack.enter_async_context(self.session)
        await self.session.initialize()
        self.is_connected = True

    async def close(self, ignore_errors: bool = True) -> None:
        """Clean up the MCP client resources."""
        await self.stack.aclose()
        self.is_connected = False

    async def list_tools(self) -> List[mcp.types.Tool]:
        """Get all available tools from the server."""
        self._validate_connection()
        res = await self.session.list_tools()
        self._cached_tools = res.tools
        return res.tools
```

**生命周期管理**:
- `connect()`: 建立连接，初始化会话
- `close()`: 清理资源，关闭连接
- 使用 `AsyncExitStack` 管理多个上下文管理器

### 5.5 MCP 客户端类型

#### StdIOStatefulClient (`_stdio_stateful_client.py:11-77`)

通过标准输入输出与 MCP 服务器通信，适用于本地进程。

```python
class StdIOStatefulClient(StatefulClientBase):
    """A client class for StdIO MCP server connections."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        encoding: str = "utf-8",
        encoding_error_handler: Literal["strict", "ignore", "replace"] = "strict",
    ) -> None:
        super().__init__(name=name)
        self.client = stdio_client(
            StdioServerParameters(
                command=command,
                args=args or [],
                env=env,
                cwd=cwd,
                encoding=encoding,
                encoding_error_handler=encoding_error_handler,
            ),
        )
```

**适用场景**: 本地 MCP 服务器进程，如文件系统浏览器、代码执行环境

#### HttpStatefulClient (`_http_stateful_client.py:11-84`)

通过 HTTP 与 MCP 服务器通信，支持 SSE 或 streamable_http 传输。

```python
class HttpStatefulClient(StatefulClientBase):
    """The stateful sse/streamable HTTP MCP client implementation."""

    def __init__(
        self,
        name: str,
        transport: Literal["streamable_http", "sse"],
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30,
        sse_read_timeout: float = 60 * 5,
        **client_kwargs: Any,
    ) -> None:
        if self.transport == "streamable_http":
            self.client = streamablehttp_client(url=url, headers=headers, ...)
        else:
            self.client = sse_client(url=url, headers=headers, ...)
```

**适用场景**: 远程 MCP 服务器，需要保持会话状态

#### HttpStatelessClient (`_http_stateless_client.py:16-152`)

无状态 HTTP 客户端，每次调用创建新会话。

```python
class HttpStatelessClient(MCPClientBase):
    """The stateless sse/streamable HTTP MCP client implementation."""

    stateful: bool = False

    async def get_callable_function(self, func_name: str, ...) -> Callable:
        # 每次返回一个新的 MCPToolFunction
        return MCPToolFunction(
            mcp_name=self.name,
            tool=target_tool,
            wrap_tool_result=wrap_tool_result,
            client_gen=self.get_client,  # 每次调用创建新会话
            timeout=execution_timeout,
        )
```

**适用场景**: 远程 MCP 服务器，无状态请求响应式服务

### 5.6 MCP 工具函数注册

**文件**: `_toolkit.py:1035-1098` (`register_mcp_client` 方法)

```python
async def register_mcp_client(
    self,
    mcp_client: MCPClientBase,
    group_name: str = "basic",
    enable_funcs: list[str] | None = None,
    disable_funcs: list[str] | None = None,
    preset_kwargs_mapping: dict[str, dict[str, Any]] | None = None,
    postprocess_func: Callable[[ToolUseBlock, ToolResponse], ToolResponse | None] | None = None,
    namesake_strategy: Literal["override", "skip", "raise", "rename"] = "raise",
) -> None:
    """Register tool functions from an MCP client."""
    # 1. 建立连接（如需要）
    if isinstance(mcp_client, StatefulClientBase) and not mcp_client.is_connected:
        await mcp_client.connect()

    # 2. 获取 MCP 服务器上的所有工具
    if isinstance(mcp_client, StatefulClientBase):
        mcp_tools = await mcp_client.list_tools()
    else:
        mcp_tools = await mcp_client.list_tools()

    # 3. 过滤工具
    for tool in mcp_tools:
        if enable_funcs and tool.name not in enable_funcs:
            continue
        if disable_funcs and tool.name in disable_funcs:
            continue

        # 4. 获取可调用函数并注册
        callable_func = await mcp_client.get_callable_function(tool.name, ...)
        self.register_tool_function(
            callable_func,
            group_name=group_name,
            func_name=tool.name,
            preset_kwargs=preset_kwargs_mapping.get(tool.name),
            postprocess_func=postprocess_func,
            namesake_strategy=namesake_strategy,
        )
```

---

### 5.7 MCP 协议与 Java JDBC/ODBC 驱动对比

> ★ Insight ─────────────────────────────────────
> MCP (Model Context Protocol) 的设计理念与 Java JDBC/ODBC 类似：
> - 都是**抽象工具/数据源访问协议**
> - 都支持**多种传输方式**（JDBC-ODBC桥接、网络驱动）
> - 都通过**统一接口**屏蔽底层差异
> - 都有**驱动管理器**概念（MCPClientBase ≈ DriverManager）
> ──────────────────────────────────────────────────

**概念对应关系**:

| MCP 概念 | Java JDBC/ODBC 概念 | 说明 |
|----------|---------------------|------|
| `MCPClientBase` | `DriverManager` / `DataSource` | 客户端管理器，统一入口 |
| `StatefulClientBase` | `Connection` (有状态) | 保持会话的连接 |
| `HttpStatelessClient` | `Connection` (无状态池化) | 每次请求新建连接 |
| `MCPToolFunction` | `PreparedStatement` / `CallableStatement` | 可执行的工具/语句对象 |
| `工具` (MCP Server) | `Table` / `Stored Procedure` | 被调用的目标 |
| `list_tools()` | `DatabaseMetaData.getTables()` | 发现可用工具 |
| `call_tool()` | `statement.execute()` | 执行工具/语句 |

**MCP vs JDBC 设计差异**:

| 维度 | MCP | JDBC/ODBC |
|------|-----|-----------|
| **协议目标** | AI 模型调用外部工具 | 应用访问数据库 |
| **传输方式** | StdIO、HTTP（SSE/streamable） | TCP/IP、命名管道 |
| **返回类型** | 多模态（文本/图像/音频/视频） | 表格型 ResultSet |
| **会话状态** | 可选（stateful vs stateless） | 通常有状态 |
| **工具发现** | 运行时 `list_tools()` | 编译时 SQL 或元数据查询 |
| **Schema** | JSON Schema (function calling) | SQL DDL |
| **上下文** | AI Agent 的 prompt context | SQL 执行上下文 |

**代码对比**:

```python
# MCP 风格 - 注册并调用工具
mcp_client = HttpStatefulClient(name="db", url="https://mcp.example.com")
await mcp_client.connect()
func = await mcp_client.get_callable_function("query_database")
result = await func(sql="SELECT * FROM users WHERE id = ?", params=[123])

# JDBC 风格 - 获取连接并执行
ds = DataSource(url="jdbc:mysql://localhost:3306/mydb")
conn = ds.getConnection()
stmt = conn.prepareStatement("SELECT * FROM users WHERE id = ?")
stmt.setInt(1, 123)
rs = stmt.executeQuery()
```

**架构类比**:

```
┌─────────────────────────────────────────────────────────────┐
│                      AgentScope                             │
│  ┌─────────────┐    ┌──────────────┐    ┌─────────────┐  │
│  │   Toolkit   │───>│ MCPToolFunc  │───>│ MCP Client  │  │
│  │ (注册/调用)  │    │  (包装器)    │    │ (传输适配)  │  │
│  └─────────────┘    └──────────────┘    └─────────────┘  │
│                                                    │        │
└────────────────────────────────────────────────────│────────┘
                                                     │
                    ┌─────────────────────────────────┼───────┐
                    │              MCP               │       │
                    │  ┌───────────┐  ┌───────────┐  │       │
                    │  │  StdIO    │  │   HTTP    │  │       │
                    │  │ (本地进程) │  │ (远程服务) │  │       │
                    │  └───────────┘  └───────────┘  │       │
                    └─────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                     Java Application                        │
│  ┌─────────────┐    ┌──────────────┐    ┌─────────────┐  │
│  │    ORM/     │───>│ PreparedStm │───>│   Driver    │  │
│  │  JDBC API   │    │  (语句)     │    │ (ODBC/JDBC) │  │
│  └─────────────┘    └──────────────┘    └─────────────┘  │
│                                                    │        │
└────────────────────────────────────────────────────│────────┘
                                                     │
                    ┌─────────────────────────────────┼───────┐
                    │           Database               │       │
                    │  ┌───────────┐  ┌───────────┐  │       │
                    │  │    MySQL  │  │  Oracle   │  │       │
                    │  │  (本地)   │  │ (远程服务) │  │       │
                    │  └───────────┘  └───────────┘  │       │
                    └─────────────────────────────────────────┘
```

**总结**: MCP 协议可以被理解为"AI 领域的 JDBC"——它标准化了 AI Agent 如何调用外部工具/服务，使得同一个 AgentScope 应用可以连接不同的 MCP 服务器（就像 JDBC 可以连接不同的数据库），而无需修改上层代码。

---

## 6. 工具调用流程

### 6.1 完整调用流程

```
Agent.rely()
    │
    ├─> 调用 model 生成响应
    ├─> 检查响应中是否包含 tool_use 块
    └─> 遍历每个工具调用
            │
            ▼
    ReActAgent._acting()
            │
            ├─> 创建 ToolResultBlock
            └─> 调用 toolkit.call_tool_function(tool_call)
                    │
                    ▼
            Toolkit.call_tool_function()
                    │
                    ├─> 1. 查找 RegisteredToolFunction
                    ├─> 2. 检查工具组是否激活
                    ├─> 3. 合并 preset_kwargs 和 input
                    ├─> 4. 应用后处理函数 (partial)
                    ├─> 5. 应用中间件链
                    ├─> 6. 调用原始工具函数
                    └─> 7. 包装为 AsyncGenerator[ToolResponse]
                            │
                            ▼
                    工具函数执行
                    ├─> ToolResponse (同步)
                    ├─> AsyncGenerator[ToolResponse] (异步流式)
                    └─> Generator[ToolResponse] (同步流式)
                            │
                            ▼
                    _async_wrapper 包装
                    ├─> _object_wrapper: 单个响应
                    ├─> _sync_generator_wrapper: 同步生成器
                    └─> _async_generator_wrapper: 异步生成器
```

### 6.2 call_tool_function 实现详解

**文件**: `_toolkit.py:851-1033`

```python
@trace_toolkit          # 追踪装饰器
@_apply_middlewares     # 应用中间件链
async def call_tool_function(
    self,
    tool_call: ToolUseBlock,
) -> AsyncGenerator[ToolResponse, None]:
    """Execute the tool function by the ToolUseBlock."""

    # 步骤 1: 检查函数是否存在
    if tool_call["name"] not in self.tools:
        return _object_wrapper(
            ToolResponse(content=[TextBlock(type="text", text="FunctionNotFoundError: ...")]),
            None,
        )

    # 步骤 2: 获取工具函数
    tool_func = self.tools[tool_call["name"]]

    # 步骤 3: 检查工具组是否激活
    if tool_func.group != "basic" and not self.groups[tool_func.group].active:
        return _object_wrapper(
            ToolResponse(content=[TextBlock(type="text", text="FunctionInactiveError: ...")]),
            None,
        )

    # 步骤 4: 准备参数 (preset_kwargs + input)
    kwargs = {**tool_func.preset_kwargs, **(tool_call.get("input", {}) or {})}

    # 步骤 5: 准备后处理函数（使用 partial 绑定 tool_call）
    if tool_func.postprocess_func:
        partial_postprocess_func = partial(tool_func.postprocess_func, tool_call)
    else:
        partial_postprocess_func = None

    # 步骤 6: 检查是否启用异步执行
    if tool_func.async_execution:
        task_id = shortuuid.uuid()
        task = asyncio.create_task(
            self._execute_tool_in_background(task_id, tool_func, kwargs, partial_postprocess_func),
        )
        self._async_tasks[task_id] = task
        return _object_wrapper(
            ToolResponse(content=[TextBlock(type="text", text=f"<system-reminder>Tool executing async. Task ID: {task_id}</system-reminder>")]),
            None,
        )

    # 步骤 7: 执行工具函数
    try:
        if inspect.iscoroutinefunction(tool_func.original_func):
            res = await tool_func.original_func(**kwargs)
        else:
            res = tool_func.original_func(**kwargs)
    except mcp.shared.exceptions.McpError as e:
        res = ToolResponse(content=[TextBlock(type="text", text=f"MCP Error: {e}")])
    except Exception as e:
        res = ToolResponse(content=[TextBlock(type="text", text=f"Error: {e}")])

    # 步骤 8: 根据返回类型选择包装器
    if isinstance(res, AsyncGenerator):
        return _async_generator_wrapper(res, partial_postprocess_func)
    if isinstance(res, Generator):
        return _sync_generator_wrapper(res, partial_postprocess_func)
    if isinstance(res, ToolResponse):
        return _object_wrapper(res, partial_postprocess_func)

    raise TypeError(f"Invalid return type: {type(res)}")
```

### 6.3 postprocess_func 机制详解

**重要**: `postprocess_func` 在注册时签名为 `Callable[[ToolUseBlock, ToolResponse], ToolResponse | None]`，但在内部传递时会通过 `partial` 绑定 `tool_call`，因此实际执行的函数只有 `ToolResponse` 参数。

```python
# 注册时: 用户提供的函数签名
def my_postprocess(tool_call: ToolUseBlock, response: ToolResponse) -> ToolResponse:
    ...

# 内部传递时: partial 绑定 tool_call
partial_postprocess_func = partial(tool_func.postprocess_func, tool_call)

# 实际执行时: 只传递 response
yield await _postprocess_tool_response(chunk, partial_postprocess_func)
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
        content=[TextBlock(type="text", text=f"Hello, {name}!")],
    )

# 注册到工具包
toolkit.register_tool_function(greet)
```

### 7.2 带参数验证的工具（使用 Pydantic）

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

    ⚠️ 安全警告: 此示例使用 eval() 演示仅供教学。
    生产环境应使用 ast.literal_eval() 或专用数学库。
    """
    import math
    result = eval(input.expression, {"sqrt": math.sqrt, **math.__dict__})
    return ToolResponse(
        content=[TextBlock(type="text", text=f"Result: {round(result, input.precision)}")],
    )

# 注册时指定 extended_model 动态扩展 Schema
toolkit.register_tool_function(
    calculator,
    func_name="calculator",
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
import re

def sensitive_filter(tool_call: ToolUseBlock, response: ToolResponse) -> ToolResponse | None:
    """Filter sensitive information from tool responses.

    注意: postprocess_func 签名是 (ToolUseBlock, ToolResponse) -> ToolResponse | None
    tool_call 参数在内部通过 partial 绑定，实际执行时只需处理 response。
    """
    # 信用卡号正则
    sensitive_pattern = r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b'
    filtered_content = []

    for block in response.content:
        if block.get("type") == "text":
            text = re.sub(sensitive_pattern, "[REDACTED]", block["text"])
            filtered_content.append(TextBlock(type="text", text=text))
        else:
            filtered_content.append(block)

    return ToolResponse(content=filtered_content, metadata=response.metadata)

def get_credit_card_info() -> ToolResponse:
    """This tool might return sensitive data."""
    return ToolResponse(content=[TextBlock(type="text", text="Card: 1234-5678-9012-3456")])

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
        transport="streamable_http",
        url="https://mcp.example.com/mcp",
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

```python showLineNumbers
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
)

toolkit.create_tool_group(
    group_name="code_execution",
    description="Code execution tools",
    active=True,  # 默认激活
)

# 注册工具到不同组
toolkit.register_tool_function(view_text_file, group_name="file_operations")
toolkit.register_tool_function(write_text_file, group_name="file_operations")
toolkit.register_tool_function(execute_python_code, group_name="code_execution")

# 获取可用的 JSON Schema（只有激活的组）
schemas = toolkit.get_json_schemas()
print(f"Active tools: {[s['function']['name'] for s in schemas]}")

# 激活文件操作组
toolkit.update_tool_groups(["file_operations"], active=True)
schemas = toolkit.get_json_schemas()
print(f"After activation: {[s['function']['name'] for s in schemas]}")
```

**预期输出**：
```
Active tools: ['execute_python_code', 'execute_shell_command']
After activation: ['execute_python_code', 'execute_shell_command', 'view_text_file', 'write_text_file']
```

### 8.2 中间件使用

```python showLineNumbers
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

**预期输出**：
```
[LOG] Calling tool: execute_python_code
[LOG] Tool execute_python_code returned
```

---

## 本章关联

### 与其他模块的关系

| 关联模块 | 关联内容 | 参考位置 |
|----------|----------|----------|
| [Agent 模块深度剖析](module_agent_deep.md) | ReActAgent 如何调用 Toolkit 的工具，`toolkit` 参数与 Agent 的集成方式 | 第 4.4 节 `_acting()` 方法 |
| [Pipeline/基础设施模块深度剖析](module_pipeline_infra_deep.md) | MCP 协议与 A2A 协议在智能体间通信中的对比，工具调用与消息路由的关系 | 第 7 章 A2A 协议 |
| [Memory/RAG 模块深度剖析](module_memory_rag_deep.md) | 工具执行结果如何被存储到记忆中，RAG 检索工具的设计模式 | 第 9.4 节在 Agent 中使用记忆和知识库 |
| [Model 模块深度剖析](module_model_deep.md) | 模型的工具调用机制 (`tool_choice`、`structured_model`) 如何与 Toolkit 协作 | 第 7 章工具调用机制 |
| [最佳实践参考](reference_best_practices.md) | 工具设计原则、安全性实践（输入验证、权限控制） | 安全性章节 |

### 前置知识

- **JSON Schema**: 如不熟悉 JSON Schema 的结构和用途，建议先了解基础知识
- **异步生成器**: 需要理解 `async def` + `yield` 的工作原理
- **装饰器**: 需要理解 Python 装饰器的执行顺序和参数传递

### 后续学习建议

1. 完成本模块练习题后，建议继续学习 [Pipeline 模块](module_pipeline_infra_deep.md)，理解 A2A 协议与 MCP 协议的协作关系
2. 如需构建自定义工具链，建议参考 [Agent 模块](module_agent_deep.md) 的 Hook 机制，实现工具调用前后的自定义逻辑
3. 如需优化工具性能，建议参考 [最佳实践](reference_best_practices.md) 中的工具调用优化策略

---

### 边界情况与陷阱

#### Critical: ToolResponse 的必需字段

```python
# ToolResponse 是 dataclass，必需字段必须提供
@dataclass
class ToolResponse:
    content: Sequence[TextBlock | ImageBlock | AudioBlock | VideoBlock]
    success: bool = True

# 如果 content 为空列表，可能导致意外行为
response = ToolResponse(content=[], success=True)  # 空内容

# 如果 success=False 但有 content，调用方可能困惑
```

#### High: MCP 工具的 JSON Schema 验证

```python
# MCP 工具的参数通过 JSON Schema 验证
# 问题：复杂 schema 可能导致验证失败

schema = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {"type": "object"}  # 嵌套 object
        }
    }
}
# 如果实际参数结构与 schema 不完全匹配，会验证失败
```

#### High: 工具调用的超时处理

```python
# 工具执行没有内置超时机制
async def long_running_tool(param):
    await asyncio.sleep(3600)  # 1小时后返回
    return "done"

# 问题：Agent 会被阻塞，无法处理其他消息
# 解决方案：在调用方实现超时控制
try:
    result = await asyncio.wait_for(tool(), timeout=30)
except asyncio.TimeoutError:
    return ToolResponse(content=[TextBlock(...)], success=False)
```

#### Medium: 工具名称冲突

```python
# Toolkit 允许多个工具重名（后者覆盖）
toolkit = Toolkit()
toolkit.register(func_a)  # name="translate"
toolkit.register(func_b)  # name="translate" - 覆盖！

# 问题：难以调试的预期外行为
# 解决方案：使用命名空间前缀
toolkit.register(func_a, name="my_translate")
```

#### Medium: 工具函数的返回值类型

```python
# 工具函数应该返回特定类型
async def my_tool() -> dict:
    return {"result": "value"}

# 问题：返回字符串或其他类型会导致序列化失败
result = await tool()
# result.content[0].text 可能是字符串，需要检查类型
```

---

### 性能考量

#### 工具调用延迟分析

| 阶段 | 延迟占比 | 优化方向 |
|------|----------|----------|
| Schema 验证 | ~5% | 简化 schema |
| 参数解析 | ~5% | 减少嵌套 |
| 工具执行 | ~80% | 并行化/优化算法 |
| 结果序列化 | ~10% | 减少返回值大小 |

#### MCP 协议开销

```python
# MCP 工具调用涉及网络序列化
# 本地 MCP vs 远程 MCP 延迟差异巨大

# 本地（同一进程）：< 1ms
# HTTP（本地服务）：~10-50ms
# HTTP（远程服务）：~100-500ms

# 优化建议：
# - 批量工具调用减少网络往返
# - 使用流式响应减少首字节延迟
# - 本地缓存常用工具结果
```

#### 工具注册性能

```python
# Toolkit 注册大量工具时的性能
toolkit = Toolkit()
for i in range(1000):
    toolkit.register(create_tool(i))

# 注册时间：O(n)，每个工具需要验证 schema
# 优化：延迟注册，仅注册可能使用的工具
```

---

## 9. 练习题

### 9.1 基础题

1. **分析 RegisteredToolFunction 类的设计，参考 `_types.py:16`。**
   - 理解 `extended_json_schema` 属性的合并逻辑
   - 理解 `preset_kwargs` 如何隐藏敏感参数

2. **Toolkit 如何管理工具组？请说明 basic 组与其他组的区别。**
   - basic 组始终激活，其他组需要手动激活
   - 使用 `create_tool_group()` 创建新组
   - 使用 `update_tool_groups()` 激活/停用组

3. **解释 ToolResponse 的主要字段及其作用，参考 `_response.py:12`。**
   - `content`: 支持多种内容类型（文本、图像、音频、视频）
   - `stream` + `is_last`: 流式响应机制
   - `is_interrupted`: 处理用户中断

4. **分析 `_async_wrapper.py` 中的三种包装器函数的设计。**
   - `_object_wrapper`: 处理单个 ToolResponse
   - `_sync_generator_wrapper`: 同步生成器转异步
   - `_async_generator_wrapper`: 异步生成器包装并处理 CancelledError

### 9.2 进阶题

5. **分析中间件机制的实现，参考 `_toolkit.py:57-114`。**
   - 中间件链的构建顺序（反向）
   - 为什么中间件必须是 async generator function
   - 设计一个日志中间件

6. **分析 `call_tool_function` 的完整执行流程，参考 `_toolkit.py:851-1033`。**
   - 错误处理机制（FunctionNotFoundError、FunctionInactiveError、McpError）
   - 不同返回类型的处理
   - 异步执行模式 (async_execution)

7. **分析 MCP 客户端的类型及其适用场景。**
   - `StdIOStatefulClient`: 本地进程
   - `HttpStatefulClient`: 远程有状态服务
   - `HttpStatelessClient`: 远程无状态服务

8. **设计一个自定义中间件，实现工具调用的限流。**
   - 使用 `asyncio.Semaphore` 控制并发
   - 记录调用历史实现时间窗口限流

### 9.3 挑战题

9. **实现一个工具执行超时机制，当工具执行超过一定时间自动中断。**
   - 使用 `asyncio.wait_for()` 实现超时
   - 在 `_async_generator_wrapper` 中处理 TimeoutError

10. **设计一个工具版本管理机制，支持工具的热更新。**
    - 在 `RegisteredToolFunction` 中添加版本号
    - 实现 `update_tool_function()` 方法

11. **分析 AgentScope 的工具调用与 LangChain 的工具调用机制的异同。**
    - AgentScope 支持流式响应
    - AgentScope 支持中间件机制
    - AgentScope 内置 MCP 协议支持

12. **实现一个自定义 postprocess_func，实现响应缓存。**
    - postprocess_func 签名是 `(ToolUseBlock, ToolResponse) -> ToolResponse | None`
    - 内部通过 partial 绑定 tool_call 后只传递 response
    - 使用 `functools.lru_cache` 或自定义缓存

---

## 参考答案

### 9.1 基础题

**第1题：RegisteredToolFunction 设计**

`extended_json_schema` 将函数的原始 JSON Schema 与 `preset_kwargs` 合并：先从函数签名提取参数 Schema，然后移除 preset 已覆盖的参数（避免暴露给 LLM），最后合并 `required` 和 `properties`。`preset_kwargs` 将敏感参数（如 API Key）绑定到工具函数，LLM 调用时自动注入但 LLM 看不到。

**第2题：Toolkit 工具组**

`basic` 组是默认激活的核心工具集（如 `print`、`wait`），在 `__init__` 中自动注册。其他自定义组需通过 `update_tool_groups(group_name, activate=True)` 激活。这允许动态控制可用工具集——例如只在高权限模式下激活文件操作工具。

**第3题：ToolResponse 字段**

- `content`: 支持 `str | list[ContentBlock]`，可包含文本、图像、音频、视频
- `stream`: 当工具返回生成器时，`stream=True` 表示流式输出
- `is_last`: 流式场景中标记最后一块数据
- `is_interrupted`: 用户中断时设为 True，允许工具优雅退出

**第4题：三种包装器**

- `_object_wrapper`: 将单个 ToolResponse 包装为异步生成器（yield 一次）
- `_sync_generator_wrapper`: 将同步生成器转为异步（用 `asyncio.to_thread` 包装 next()）
- `_async_generator_wrapper`: 包装异步生成器，捕获 CancelledError 实现优雅取消

### 9.2 进阶题

**第5题：中间件机制**

中间件链采用洋葱模型：最后注册的中间件最先执行（反向构建）。必须是 async generator function 因为中间件需要在工具执行前后各执行一段逻辑（`yield` 前是前置处理，`yield` 后是后置处理）。

```python
async def logging_middleware(context, tool_call, response_stream):
    print(f"[调用] {tool_call.function.name}")
    async for resp in response_stream:
        yield resp
    print(f"[完成] {tool_call.function.name}")
```

**第7题：MCP 客户端类型**

| 客户端 | 传输方式 | 状态 | 适用场景 |
|--------|----------|------|----------|
| StdIOStatefulClient | stdin/stdout | 有状态 | 本地进程工具 |
| HttpStatefulClient | HTTP SSE | 有状态 | 远程服务、会话保持 |
| HttpStatelessClient | HTTP 请求 | 无状态 | REST API、无会话 |

**第8题：限流中间件**

```python
async def rate_limit_middleware(context, tool_call, response_stream):
    semaphore = context.get("semaphore", asyncio.Semaphore(5))
    async with semaphore:
        async for resp in response_stream:
            yield resp
```

### 9.3 挑战题

**第9题：工具执行超时**

```python
async def timeout_middleware(context, tool_call, response_stream):
    timeout = context.get("timeout", 30)
    try:
        async for resp in asyncio.timeout(timeout, response_stream).__aiter__():
            yield resp
    except TimeoutError:
        yield ToolResponse(content=f"工具 {tool_call.function.name} 执行超时", is_last=True)
```

**第12题：postprocess_func 缓存**

```python
from functools import lru_cache
import hashlib, json

def cached_postprocess(tool_call, response):
    cache_key = hashlib.md5(json.dumps({
        "name": tool_call.function.name,
        "args": tool_call.function.arguments,
    }).encode()).hexdigest()

    @lru_cache(maxsize=128)
    def get_cached(key):
        return response
    return get_cached(cache_key)
```

---

## 小结

| 组件 | 职责 | 关键特性 |
|------|------|----------|
| RegisteredToolFunction | 工具注册 | JSON Schema + preset_kwargs |
| Toolkit | 工具管理 | 分组、中间件、生命周期 |
| ToolResponse | 响应封装 | 流式、多模态、中断 |
| MCP Client | 协议适配 | StdIO/HTTP 有状态/无状态 |
| postprocess_func | 后处理 | 缓存、验证、转换 |

| 关联模块 | 关联点 | 参考位置 |
|----------|--------|----------|
| [智能体模块](module_agent_deep.md#4-reactagent-实现类分析) | ReActAgent 通过 `_acting()` 调用工具 | 第 4.4 节 |
| [管道模块](module_pipeline_infra_deep.md#7-a2a-协议) | MCP ↔ A2A 协议对比 | 第 7.1-7.4 节 |
| [模型模块](module_model_deep.md#7-工具调用机制详解) | `tool_choice` 控制工具调用策略 | 第 7.1-7.3 节 |
| [状态模块](module_state_deep.md#3-源码解读) | Toolkit 继承 StateModule | 第 3.1 节 |
| [追踪模块](module_tracing_deep.md#3-追踪装饰器) | trace_toolkit() 追踪工具执行 | 第 3.2 节 |


---

## 参考资料

### 工具模块源码文件

| 文件 | 说明 | 关键行号 |
|------|------|----------|
| `_response.py` | ToolResponse 响应类定义 | 12 |
| `_types.py` | RegisteredToolFunction 和 ToolGroup 数据类 | 16-98, 136-130 |
| `_async_wrapper.py` | 异步包装器 | 46-104 |
| `_toolkit.py` | Toolkit 核心类 | 117（类定义）, 152-186（初始化）, 273-534（注册）, 851-1033（调用） |
| `_coding/` | 编码相关工具 | Shell/Python 执行 |
| `_text_file/` | 文本文件操作工具 | view/write/insert |
| `_multi_modality/` | 多模态工具 | DashScope/OpenAI |

### MCP 模块源码文件

| 文件 | 说明 | 关键行号 |
|------|------|----------|
| `_client_base.py` | MCP 客户端基类 | 17-76 |
| `_mcp_function.py` | MCPToolFunction 包装类 | 18-113 |
| `_stateful_client_base.py` | 有状态客户端基类 | 18-155 |
| `_stdio_stateful_client.py` | StdIO 有状态客户端 | 11-77 |
| `_http_stateful_client.py` | HTTP 有状态客户端 | 11-84 |
| `_http_stateless_client.py` | HTTP 无状态客户端 | 16-152 |

### 源码路径

- 工具模块: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/tool/`
- MCP 模块: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/mcp/`
- 类型定义: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/types.py`

---

## 文档更新记录

| 版本 | 日期 | 更新内容 |
|------|------|----------|
| 1.0 | 2026-04-27 | 初始版本 |
| 1.1 | 2026-04-28 | 修正目录结构，更新关键行号，修正 postprocess_func 机制说明 |
| 1.2 | 2026-04-28 | 新增 MCP 协议与 Java JDBC/ODBC 对比说明（采纳初审建议） |
| 1.3 | 2026-04-28 | 统一术语："Tool 模块"→"工具模块"，"Tool 基类设计"→"工具基类设计" |
| 1.4 | 2026-04-28 | 进一步统一术语：docstring 和表格中的 "Tool" 改为"工具" |
| 1.5 | 2026-04-30 | 修正行号引用（ToolResponse: 12, RegisteredToolFunction: 16, Toolkit: 117, ToolGroup: 136）；修正学习目标：`@function` → `Toolkit.register_tool_function()`；添加行号版本免责声明 |

*文档版本: 1.5*
*最后更新: 2026-04-30*
