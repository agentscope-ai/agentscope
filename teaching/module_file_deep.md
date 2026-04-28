# File 文件操作模块源码深度剖析

## 目录
1. 模块概述
2. 目录结构
3. 核心类与函数源码解读
4. 设计模式总结
5. 代码示例
6. 练习题

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举文件操作模块提供的核心工具函数 | 列举、识别 |
| 理解 | 解释文件检测、Base64 编解码、临时文件管理的实现原理 | 解释、描述 |
| 应用 | 使用 `_text_file` 工具实现文本文件的查看与编辑操作 | 实现、操作 |
| 分析 | 分析文件工具在 Agent 工具调用链中的角色与数据流 | 分析、追踪 |
| 评价 | 评价函数式工具设计 vs 面向对象文件处理的优劣 | 评价、对比 |
| 创造 | 设计一个支持多格式文件处理的工具扩展 | 设计、构建 |

## 先修检查

在开始学习本模块之前，请确认您已掌握以下知识：

- [ ] Python 文件 I/O 基础（`open`、`pathlib`）
- [ ] Base64 编解码原理
- [ ] 临时文件管理（`tempfile` 模块）
- [ ] MCP 工具协议基础（参见工具模块）

**预计学习时间**: 25 分钟

### Java 开发者对照

| Python 概念 | Java 等价物 | 说明 |
|-------------|------------|------|
| `pathlib.Path` | `java.nio.file.Path` | 路径操作 |
| `base64.b64encode/decode` | `java.util.Base64` | 编解码 |
| `tempfile.mkstemp` | `Files.createTempFile` | 临时文件 |
| `with open()` | try-with-resources | 资源管理 |

---

## 1. 模块概述

AgentScope 的文件操作功能主要分布在两个位置：
1. **`_utils/_common.py`** - 文件处理通用工具函数
2. **`tool/_text_file/`** - 文本文件读写工具

本模块不提供面向对象的 `File` 类，而是采用函数式工具设计，提供本地文件检测、Base64 编解码、临时文件管理、文本文件查看与编辑等功能。

**源码位置**:
- 工具函数: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/_utils/_common.py`
- 文本文件工具: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/tool/_text_file/`

---

## 2. 目录结构

### 2.1 工具函数模块

```
_utils/
├── __init__.py      # 空模块
└── _common.py       # 通用工具函数
```

### 2.2 文本文件工具模块

```
tool/_text_file/
├── __init__.py              # 模块导出
├── _utils.py                # 内部工具函数
├── _view_text_file.py       # 查看文件工具
└── _write_text_file.py      # 写入文件工具
```

---

## 3. 核心类与函数源码解读

### 3.1 文件检测 (`_common.py`)

#### 3.1.1 本地文件检测

```python
def _is_accessible_local_file(url: str) -> bool:
    """Check if the given URL is a local URL."""
    # First identify if it's an uri with 'file://' schema,
    if url.startswith("file://"):
        local_path = url.removeprefix("file://")
        return os.path.isfile(local_path)
    return os.path.isfile(url)
```

**功能**: 判断给定 URL 是否指向本地可访问的文件。

**设计要点**:
- 支持 `file://` 协议前缀
- 使用 `os.path.isfile()` 验证文件存在性
- 返回布尔值表示是否可访问

---

### 3.2 Base64 数据处理 (`_common.py`)

#### 3.2.1 保存 Base64 数据到临时文件

```python
def _save_base64_data(
    media_type: str,
    base64_data: str,
) -> str:
    """Save the base64 data to a temp file and return the file path.
    The extension is guessed from the MIME type."""

    extension = "." + media_type.split("/")[-1]

    with tempfile.NamedTemporaryFile(
        suffix=extension,
        delete=False,
    ) as temp_file:
        decoded_data = base64.b64decode(base64_data)
        temp_file.write(decoded_data)
        return temp_file.name
```

**功能**: 将 Base64 编码的数据保存到临时文件。

**关键设计**:
- 从 MIME 类型推断文件扩展名（如 `image/png` -> `.png`）
- 使用 `NamedTemporaryFile` 创建临时文件
- `delete=False` 防止文件被自动删除，便于后续使用

#### 3.2.2 从 Web URL 获取字节数据

```python
def _get_bytes_from_web_url(
    url: str,
    max_retries: int = 3,
) -> str:
    """Get the bytes from a given URL."""
    for _ in range(max_retries):
        try:
            response = requests.get(url)
            response.raise_for_status()
            return response.content.decode("utf-8")

        except UnicodeDecodeError:
            return base64.b64encode(response.content).decode("ascii")

        except Exception as e:
            logger.info(
                "Failed to fetch bytes from URL %s. Error %s. Retrying...",
                url,
                str(e),
            )

    raise RuntimeError(
        f"Failed to fetch bytes from URL `{url}` after {max_retries} retries.",
    )
```

**功能**: 从 URL 获取内容，支持自动重试和编码处理。

---

### 3.3 文本文件查看工具 (`_text_file/`)

#### 3.3.1 查看工具主函数

```python
async def view_text_file(
    file_path: str,
    ranges: list[int] | None = None,
) -> ToolResponse:
    """View the file content in the specified range with line numbers."""
    file_path = os.path.expanduser(file_path)

    if not os.path.exists(file_path):
        return ToolResponse(
            content=[TextBlock(type="text", text=f"Error: The file {file_path} does not exist.")],
        )

    if not os.path.isfile(file_path):
        return ToolResponse(
            content=[TextBlock(type="text", text=f"Error: The path {file_path} is not a file.")],
        )

    try:
        content = _view_text_file(file_path, ranges)
    except ToolInvalidArgumentsError as e:
        return ToolResponse(content=[TextBlock(type="text", text=e.message)])

    if ranges is None:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"""The content of {file_path}:\n```\n{content}```""")],
        )
    else:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"""The content of {file_path} in {ranges} lines:\n```\n{content}```""")],
        )
```

**功能**: 异步查看文件内容，支持行范围选择。

**关键设计**:
- 使用 `os.path.expanduser()` 支持 `~` 路径展开
- 多重校验：文件存在性、路径是文件而非目录
- 错误处理：返回友好的错误信息而非抛出异常

#### 3.3.2 低级查看实现

```python
def _view_text_file(
    file_path: str,
    ranges: list[int] | None = None,
) -> str:
    """Return the file content in the specified range with line numbers."""
    with open(file_path, "r", encoding="utf-8") as file:
        lines = file.readlines()

    if ranges:
        _assert_ranges(ranges)
        start, end = ranges

        if start > len(lines):
            raise ToolInvalidArgumentsError(
                f"InvalidArgumentError: The range '{ranges}' is out of bounds "
                f"for the file '{file_path}', which has only {len(lines)} lines.",
            )

        view_content = [
            f"{index + start}: {line}"
            for index, line in enumerate(lines[start - 1 : end])
        ]
        return "".join(view_content)

    return "".join(f"{index + 1}: {line}" for index, line in enumerate(lines))
```

**输出格式**: 每行以 `行号: 内容` 格式显示。

---

### 3.4 文本文件写入工具 (`_text_file/`)

#### 3.4.1 写入文件

```python
async def write_text_file(
    file_path: str,
    content: str,
    ranges: None | list[int] = None,
) -> ToolResponse:
    """Create/Replace/Overwrite content in a text file."""
    file_path = os.path.expanduser(file_path)

    # 文件不存在则创建
    if not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(content)
        return ToolResponse(
            content=[TextBlock(type="text", text=f"Create and write {file_path} successfully.")],
        )

    # 读取原内容
    with open(file_path, "r", encoding="utf-8") as file:
        original_lines = file.readlines()

    # 范围替换模式
    if ranges is not None:
        start, end = ranges
        new_content = (
            original_lines[: start - 1]
            + [content]
            + original_lines[end:]
        )
        with open(file_path, "w", encoding="utf-8") as file:
            file.write("".join(new_content))

        # 显示替换后的内容片段
        with open(file_path, "r", encoding="utf-8") as file:
            new_lines = file.readlines()

        view_start, view_end = _calculate_view_ranges(...)
        content = "".join([f"{index + view_start}: {line}" ...])
        return ToolResponse(
            content=[TextBlock(type="text", text=f"""Write {file_path} successfully...""")],
        )

    # 全量覆盖模式
    with open(file_path, "w", encoding="utf-8") as file:
        file.write(content)

    return ToolResponse(
        content=[TextBlock(type="text", text=f"Overwrite {file_path} successfully.")],
    )
```

**三种模式**:
1. **创建模式**: 文件不存在时创建新文件
2. **范围替换**: `ranges=[start, end]` 替换指定行范围
3. **全量覆盖**: `ranges=None` 覆盖整个文件

#### 3.4.2 插入文本

```python
async def insert_text_file(
    file_path: str,
    content: str,
    line_number: int,
) -> ToolResponse:
    """Insert the content at the specified line number in a text file."""
    # 行号从 1 开始计数
    # 允许在最后一行之后插入（追加）
    if line_number <= 0:
        return ToolResponse(content=[TextBlock(type="text", text=f"InvalidArgumentsError...")])

    with open(file_path, "r", encoding="utf-8") as file:
        original_lines = file.readlines()

    # 追加到末尾
    if line_number == len(original_lines) + 1:
        new_lines = original_lines + ["\n" + content]
    # 插入到中间
    elif line_number < len(original_lines) + 1:
        new_lines = (
            original_lines[: line_number - 1]
            + [content + "\n"]
            + original_lines[line_number - 1 :]
        )
    # 行号超出范围
    else:
        return ToolResponse(...)

    with open(file_path, "w", encoding="utf-8") as file:
        file.writelines(new_lines)
```

---

### 3.5 内部工具函数 (`_utils.py`)

#### 3.5.1 范围计算

```python
def _calculate_view_ranges(
    old_n_lines: int,
    new_n_lines: int,
    start: int,
    end: int,
    extra_view_n_lines: int = 5,
) -> tuple[int, int]:
    """Calculate after writing the new content, the view ranges of the file."""
    view_start = max(1, start - extra_view_n_lines)
    delta_lines = new_n_lines - old_n_lines
    view_end = min(end + delta_lines + extra_view_n_lines, new_n_lines)
    return view_start, view_end
```

**功能**: 计算写入后需要显示的行范围，支持显示前后额外的上下文行。

#### 3.5.2 范围验证

```python
def _assert_ranges(ranges: list[int]) -> None:
    """Check if the ranges are valid."""
    if (
        isinstance(ranges, list)
        and len(ranges) == 2
        and all(isinstance(i, int) for i in ranges)
    ):
        start, end = ranges
        if start > end:
            raise ToolInvalidArgumentsError(...)
    else:
        raise ToolInvalidArgumentsError(...)
```

---

## 4. 设计模式总结

### 4.1 函数式工具模式

不采用面向对象的 `File` 类，而是提供独立的工具函数，便于组合使用。

### 4.2 防御式编程

```python
# 多重检查
if not os.path.exists(file_path):
    return error_response
if not os.path.isfile(file_path):
    return error_response
```

### 4.3 错误即返回值

```python
if error_condition:
    return ToolResponse(content=[TextBlock(type="text", text=f"Error: ...")])
```

不抛出异常，而是返回包含错误信息的 `ToolResponse`。

### 4.4 异步设计

```python
async def view_text_file(...) -> ToolResponse:
```

使用 `async/await` 支持异步操作，适合在 Agent 运行时环境中使用。

### 4.5 临时文件管理

```python
with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as temp_file:
    ...
    return temp_file.name
```

`delete=False` 确保临时文件持久化，便于后续访问。

---

## 5. 代码示例

### 5.1 本地文件检测

```python
from agentscope._utils._common import _is_accessible_local_file

# 检测普通路径
print(_is_accessible_local_file("/tmp/test.txt"))  # True/False

# 检测 file:// 协议
print(_is_accessible_local_file("file:///home/user/doc.txt"))
```

### 5.2 Base64 数据处理

```python
from agentscope._utils._common import _save_base64_data

# 保存 Base64 图片数据到临时文件
media_type = "image/png"
base64_data = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

temp_path = _save_base64_data(media_type, base64_data)
print(f"临时文件路径: {temp_path}")
```

### 5.3 查看文本文件

```python
import asyncio
from agentscope.tool._text_file import view_text_file

async def main():
    # 查看整个文件
    response = await view_text_file("/path/to/file.txt")
    print(response)

    # 查看指定行范围
    response = await view_text_file("/path/to/file.txt", ranges=[1, 50])

asyncio.run(main())
```

### 5.4 写入文本文件

```python
import asyncio
from agentscope.tool._text_file import write_text_file

async def main():
    # 创建新文件
    await write_text_file("/tmp/new.txt", "Hello, World!")

    # 全量覆盖
    await write_text_file("/tmp/existing.txt", "New content")

    # 范围替换（第10-20行）
    await write_text_file("/tmp/existing.txt", "Replacement content", ranges=[10, 20])

asyncio.run(main())
```

### 5.5 插入文本

```python
import asyncio
from agentscope.tool._text_file import insert_text_file

async def main():
    # 在第5行插入内容
    await insert_text_file("/tmp/test.txt", "Inserted content", line_number=5)

    # 追加到末尾
    await insert_text_file("/tmp/test.txt", "Appended content", line_number=1000)

asyncio.run(main())
```

---

## 6. 练习题

### 6.1 基础题

1. 使用 `_is_accessible_local_file()` 检测 `/etc/hosts` 文件是否存在。

2. 使用 `view_text_file()` 查看一个文件的前 20 行。

3. 使用 `write_text_file()` 创建一个新文件并写入内容。

### 6.2 提高题

4. 实现一个函数 `read_file_safely(file_path)`，安全读取文件并在失败时返回错误信息。

5. 实现一个函数 `copy_file_content(src, dst)`，复制源文件内容到目标文件。

6. 实现一个函数 `count_lines(file_path)`，统计文件的行数。

### 6.3 挑战题

7. 实现一个函数 `replace_in_file(file_path, old_text, new_text)`，在文件中替换指定文本（类似 `sed` 命令）。

8. 实现一个函数 `create_file_with_line_numbers(file_path, content)`，创建文件时为每行添加行号。

9. 设计一个函数 `batch_view_files(file_paths, ranges)`，批量查看多个文件指定范围的内容。

---

**答案提示**:
- 参考 `_view_text_file()` 的实现
- 使用 `with open()` 确保文件正确关闭
- 使用列表推导式处理多行内容

---

## 参考答案

### 6.1 基础题

**第1题：文件检测**

```python
from agentscope._utils._common import _is_accessible_local_file

exists = _is_accessible_local_file("/etc/hosts")
print(f"文件存在: {exists}")
```

**第2题：查看文件前 20 行**

```python
from agentscope.tool._text_file import view_text_file

content = view_text_file(file_path="/path/to/file.txt", start_line=1, end_line=20)
print(content)
```

**第3题：创建文件并写入**

```python
from agentscope.tool._text_file import write_text_file

result = write_text_file(
    file_path="/tmp/test.txt",
    content="Hello, AgentScope!",
    mode="overwrite",
)
```

### 6.2 提高题

**第4题：read_file_safely**

```python
def read_file_safely(file_path: str) -> str:
    try:
        with open(file_path, "r") as f:
            return f.read()
    except FileNotFoundError:
        return f"错误：文件 {file_path} 不存在"
    except PermissionError:
        return f"错误：无权限读取 {file_path}"
```

**第5题：copy_file_content**

```python
def copy_file_content(src: str, dst: str) -> None:
    with open(src, "r") as f:
        content = f.read()
    with open(dst, "w") as f:
        f.write(content)
```

**第6题：count_lines**

```python
def count_lines(file_path: str) -> int:
    with open(file_path, "r") as f:
        return sum(1 for _ in f)
```

### 6.3 挑战题

**第7题：replace_in_file**

```python
def replace_in_file(file_path: str, old_text: str, new_text: str) -> int:
    with open(file_path, "r") as f:
        content = f.read()
    new_content = content.replace(old_text, new_text)
    count = content.count(old_text)
    with open(file_path, "w") as f:
        f.write(new_content)
    return count
```

**第8题：create_file_with_line_numbers**

```python
def create_file_with_line_numbers(file_path: str, content: str) -> None:
    lines = content.split("\n")
    numbered = [f"{i+1}: {line}" for i, line in enumerate(lines)]
    with open(file_path, "w") as f:
        f.write("\n".join(numbered))
```

**第9题：batch_view_files**

```python
async def batch_view_files(file_paths: list[str], ranges: list[tuple[int,int]]) -> dict:
    results = {}
    for path, (start, end) in zip(file_paths, ranges):
        results[path] = view_text_file(path, start_line=start, end_line=end)
    return results
```

---

## 小结

| 特性 | 实现方式 |
|------|----------|
| 文件检测 | `_is_accessible_local_file()` 安全校验 |
| Base64 编解码 | `_encode_file_to_base64()` / `_decode_base64_to_file()` |
| 临时文件 | `_get_temp_file_name()` 自动命名 |
| 文本查看 | `view_text_file()` 支持行范围 |
| 文本编辑 | `write_text_file()` 支持插入/替换/删除 |

文件操作模块采用函数式工具设计，为 Agent 的工具调用提供底层文件操作支持。

## 章节关联

| 关联模块 | 关联点 |
|----------|--------|
| [工具模块](module_tool_mcp_deep.md) | 文件工具通过 MCP 协议暴露给 Agent |
| [Utils 模块](module_utils_deep.md) | Base64 编解码等共享工具函数 |
| [智能体模块](module_agent_deep.md) | Agent 通过 Toolkit 调用文件操作 |

## 参考资料

- 工具函数: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/_utils/_common.py`
- 文本文件工具: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/tool/_text_file/`

---

*文档版本: 1.0*
*最后更新: 2026-04-28*
