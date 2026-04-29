# Utils 工具模块源码深度剖析

## 目录

1. 模块概述
2. 目录结构
3. 核心功能源码解读
4. 设计模式总结
5. 代码示例
6. 练习题

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举工具模块的核心功能：日志、JSON修复、时间戳、媒体处理 | 列举、识别 |
| 理解 | 解释流式 JSON 解析（`_parse_streaming_json_dict`）的修复策略 | 解释、描述 |
| 应用 | 使用 `DictMixin` 实现字典式属性访问的混入类 | 实现、操作 |
| 分析 | 分析 JSON 修复在 LLM 流式输出场景中的必要性与边界情况 | 分析、诊断 |
| 评价 | 评价日志系统设计在多模块协作中的统一性 | 评价、推荐 |
| 创造 | 设计一个自定义工具函数并提取其 Schema 注册到工具系统 | 设计、构建 |

## 先修检查

在开始学习本模块之前，请确认您已掌握以下知识：

- [ ] Python `logging` 模块基础
- [ ] JSON 解析与 `json.loads` / `json.dumps`
- [ ] Python Mixin 模式与多继承
- [ ] inspect 模块基础（函数签名提取）

**预计学习时间**: 30 分钟

---

## 1. 模块概述

AgentScope 的工具模块包含两个核心部分：

1. **日志系统**（`_logging.py`）：提供统一的日志记录功能
2. **通用工具**（`_utils/_common.py`）：提供 JSON 处理、HTTP 请求、媒体处理等通用能力

**核心职责：**
- 统一日志格式与输出控制
- JSON 解析与修复（处理不完整的 JSON）
- 工具函数Schema提取
- 媒体数据处理（base64、音频重采样）

**源码位置：**
- 日志系统：`/Users/nadav/IdeaProjects/agentscope/src/agentscope/_logging.py`
- 通用工具：`/Users/nadav/IdeaProjects/agentscope/src/agentscope/_utils/_common.py`
- 字典混入：`/Users/nadav/IdeaProjects/agentscope/src/agentscope/_utils/_mixin.py`

---

## 2. 目录结构

```
agentscope/
├── _logging.py                 # 日志系统
└── _utils/
    ├── __init__.py             # 空文件
    ├── _common.py              # 通用工具函数
    └── _mixin.py               # DictMixin 混入类
```

---

## 3. 核心功能源码解读

### 3.1 日志系统 _logging.py

#### 3.1.1 日志格式定义

```python
_DEFAULT_FORMAT = (
    "%(asctime)s | %(levelname)-7s | "
    "%(module)s:%(funcName)s:%(lineno)s - %(message)s"
)
```

日志格式包含：时间戳、日志级别、模块名:函数名:行号、消息内容。

#### 3.1.2 Logger 单例

```python
logger = logging.getLogger("as")  # "as" = agentscope 缩写
```

使用标准库的 `logging.getLogger()` 获取命名空间为 "as" 的 logger 实例。

#### 3.1.3 setup_logger 函数

```python
def setup_logger(
    level: str,
    filepath: str | None = None,
) -> None:
    if level not in ["INFO", "DEBUG", "WARNING", "ERROR", "CRITICAL"]:
        raise ValueError(
            f"Invalid logging level: {level}. Must be one of "
            f"'INFO', 'DEBUG', 'WARNING', 'ERROR', 'CRITICAL'.",
        )
    logger.handlers.clear()  # 清除已有处理器
    logger.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
    logger.addHandler(handler)

    if filepath:
        handler = logging.FileHandler(filepath)
        handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
        logger.addHandler(handler)

    logger.propagate = False  # 阻止日志向上传播
```

**设计亮点：**

- **多层处理器**：同时支持控制台（StreamHandler）和文件（FileHandler）输出
- **防重复添加**：每次 setup 前清除已有 handlers
- **propagate=False**：防止日志冒泡到 root logger，避免重复输出

#### 3.1.4 默认初始化

```python
setup_logger("INFO")  # 模块加载时自动初始化为 INFO 级别
```

---

### 3.2 DictMixin 混入类

```python
# _utils/_mixin.py
class DictMixin(dict):
    """The dictionary mixin that allows attribute-style access."""

    __setattr__ = dict.__setitem__
    __getattr__ = dict.__getitem__
```

**作用**：让子类同时支持字典操作和属性访问。

```python
class Config(DictMixin):
    pass

config = Config()
config["name"] = "test"       # 字典方式
config.age = 25               # 属性方式
print(config["name"])         # test
print(config.age)             # 25
```

---

### 3.3 通用工具函数 _common.py

#### 3.3.1 JSON 修复与解析

```python
def _json_loads_with_repair(json_str: str) -> dict:
    """解析可能不完整的 JSON 字符串"""
    try:
        repaired = repair_json(json_str, stream_stable=True)
        result = json.loads(repaired)
        if isinstance(result, dict):
            return result
    except Exception:
        # 失败时记录警告，返回空字典
        logger.warning("Failed to load JSON dict from string...")
    return {}
```

**应用场景**：解析 LLM 流式输出中的不完整 JSON。

```python
def _parse_streaming_json_dict(
    json_str: str,
    last_input: dict | None = None,
) -> dict:
    """流式 JSON 解析，无回退"""
    json_str = json_str or "{}"
    try:
        result = json.loads(json_str)
        if isinstance(result, dict):
            return result
    except Exception:
        pass

    # 尝试修复
    repaired_input = _json_loads_with_repair(json_str)
    last_input = last_input or {}

    # 如果上次的结果更大（包含更多字段），保留上次结果
    if len(json.dumps(last_input)) > len(json.dumps(repaired_input)):
        return last_input
    return repaired_input
```

#### 3.3.2 时间戳生成

```python
def _get_timestamp(add_random_suffix: bool = False) -> str:
    """获取当前时间戳，格式：YYYY-MM-DD HH:MM:SS.sss"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    if add_random_suffix:
        timestamp += f"_{os.urandom(3).hex()}"

    return timestamp
```

#### 3.3.3 异步函数检测与执行

```python
async def _is_async_func(func: Callable) -> bool:
    """检测函数是否为异步函数"""
    return (
        inspect.iscoroutinefunction(func)
        or inspect.isasyncgenfunction(func)
        or isinstance(func, types.CoroutineType)
        or isinstance(func, types.GeneratorType)
        and asyncio.iscoroutine(func)
        or isinstance(func, functools.partial)
        and await _is_async_func(func.func)
    )

async def _execute_async_or_sync_func(func: Callable, *args, **kwargs) -> Any:
    """执行异步或同步函数"""
    if await _is_async_func(func):
        return await func(*args, **kwargs)
    return func(*args, **kwargs)
```

**设计亮点**：统一异步/同步函数调用接口，简化调用方代码。

#### 3.3.4 HTTP 资源获取

```python
def _get_bytes_from_web_url(url: str, max_retries: int = 3) -> str:
    """从 URL 获取内容，支持重试"""
    for _ in range(max_retries):
        try:
            response = requests.get(url)
            response.raise_for_status()
            return response.content.decode("utf-8")
        except UnicodeDecodeError:
            # 尝试 base64 编码
            return base64.b64encode(response.content).decode("ascii")
        except Exception as e:
            logger.info(f"Failed to fetch... retrying...")
    raise RuntimeError(f"Failed after {max_retries} retries")
```

#### 3.3.5 Base64 媒体数据保存

```python
def _save_base64_data(media_type: str, base64_data: str) -> str:
    """将 base64 数据保存到临时文件"""
    extension = "." + media_type.split("/")[-1]  # 从 MIME 类型推断扩展名

    with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as f:
        decoded_data = base64.b64decode(base64_data)
        f.write(decoded_data)
        return f.name
```

#### 3.3.6 工具函数 Schema 提取

```python
def _extract_json_schema_from_mcp_tool(tool: Tool) -> dict:
    """从 MCP 工具提取 JSON Schema"""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": tool.inputSchema.get("properties", {}),
                "required": tool.inputSchema.get("required", []),
            },
        },
    }
```

#### 3.3.7 Pydantic 模型转工具定义

```python
def _create_tool_from_base_model(
    structured_model: Type[BaseModel],
    tool_name: str = "generate_structured_output",
) -> Dict[str, Any]:
    """将 Pydantic 模型转换为函数工具定义"""
    schema = structured_model.model_json_schema()
    _remove_title_field(schema)  # 移除 title 避免 LLM 误解

    return {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": "Generate the required structured output...",
            "parameters": schema,
        },
    }
```

#### 3.3.8 工具函数 Docstring 解析

```python
def _parse_tool_function(
    tool_func: ToolFunction,
    include_long_description: bool,
    include_var_positional: bool,
    include_var_keyword: bool,
) -> dict:
    """从函数的 docstring 和签名提取 JSON Schema"""
    docstring = parse(tool_func.__doc__)
    params_docstring = {_.arg_name: _.description for _ in docstring.params}

    # 解析函数签名
    fields = {}
    for name, param in inspect.signature(tool_func).parameters.items():
        if name in ["self", "cls"]:
            continue
        # 处理不同类型的参数...
        fields[name] = (annotation, Field(description=...))

    # 创建动态 Pydantic 模型
    base_model = create_model("_StructuredOutputDynamicClass", **fields)
    params_json_schema = base_model.model_json_schema()

    return {
        "type": "function",
        "function": {
            "name": tool_func.__name__,
            "parameters": params_json_schema,
        },
    }
```

**关键技术**：
- 使用 `docstring_parser.parse()` 解析 Google 风格的 docstring
- 使用 `inspect.signature()` 提取函数签名
- 使用 `pydantic.create_model()` 动态创建验证模型

#### 3.3.9 音频重采样

```python
def _resample_pcm_delta(
    pcm_base64: str,
    sample_rate: int,
    target_rate: int,
) -> str:
    """PCM 音频重采样"""
    pcm_data = base64.b64decode(pcm_base64)
    audio_array = np.frombuffer(pcm_data, dtype=np.int16)

    if sample_rate == target_rate:
        return pcm_base64  # 直接返回，无需重采样

    # 使用 scipy 进行重采样
    num_samples = int(len(audio_array) * target_rate / sample_rate)
    resampled_audio = signal.resample(audio_array, num_samples)

    # 转换回 int16 并编码
    resampled_audio = np.clip(resampled_audio, -32768, 32767).astype(np.int16)
    return base64.b64encode(resampled_audio.tobytes()).decode("utf-8")
```

---

## 4. 设计模式总结

### 4.1 单例模式（日志）

```python
logger = logging.getLogger("as")  # 全局唯一 logger 实例
```

### 4.2 混入模式（Mixin）

```python
class DictMixin(dict):
    __setattr__ = dict.__setitem__
    __getattr__ = dict.__getitem__
```

多重继承中用于添加通用功能。

### 4.3 适配器模式（HTTP 获取）

```python
def _get_bytes_from_web_url(url: str, max_retries: int = 3) -> str:
    # UnicodeDecodeError -> base64 fallback
```

自动检测编码并适配。

### 4.4 动态模型创建

```python
base_model = create_model("_StructuredOutputDynamicClass", **fields)
```

运行时根据函数签名动态创建 Pydantic 模型。

### 4.5 建造者模式（工具定义构建）

```python
tool_definition = {
    "type": "function",
    "function": {
        "name": tool_name,
        "description": "...",
        "parameters": schema,
    },
}
```

分步骤构建复杂对象。

---

## 4.5 边界情况与陷阱

### DictMixin 的 AttributeError 陷阱

```python
from agentscope._utils._mixin import DictMixin

class Config(DictMixin):
    pass

cfg = Config()
# hasattr(cfg, "missing_key") 会抛出 KeyError 而非返回 False
# 因为 __getattr__ 内部直接 self[key]，触发 dict 的 KeyError
# 这打破了 Python 的 hasattr() 约定（应返回 False 而非抛异常）
```

**解决方法**：在使用 DictMixin 的类上调用 `hasattr()` 前，先用 `key in obj` 检查键是否存在。

### _parse_streaming_json_dict 的长度比较

```python
# 该函数用 len(json.dumps(candidate)) 比较候选 JSON 的大小
# 但字符串长度不等同于字段完整性：
# {"a": "x" * 1000}  # 长度 1011，但只有 1 个字段
# {"a": 1, "b": 2, "c": 3}  # 长度 27，但有 3 个字段
# 在极端情况下，长值的不完整 JSON 可能"胜过"短但完整的 JSON
```

### _is_async_func 对生成器的误判

```python
import asyncio
import types

def gen_func():
    yield 1

# _is_async_func 检查 types.GeneratorType 和 asyncio.iscoroutine
# 一个普通生成器函数不是 async 的，但 functools.partial 包装后的
# 生成器可能被误判——需注意递归解包时的类型判断
```

### 音频重采样的精度损失

`_resample_pcm_delta` 使用 `signal.resample`（基于 FFT），会引入 Gibbs 现象（频谱泄漏）。重采样后的音频在高低频交界处可能出现振铃伪影。`np.clip` 会静默截断超出范围的采样值，可能导致失真。

---

## 5. 代码示例

### 5.1 使用日志系统

```python
from agentscope._logging import logger, setup_logger

# 自定义日志配置
setup_logger(level="DEBUG", filepath="./app.log")

logger.info("应用启动")
logger.debug("调试信息")
logger.warning("警告信息")
logger.error("错误信息")
```

### 5.2 使用 DictMixin

```python
from agentscope._utils._mixin import DictMixin

class MyConfig(DictMixin):
    pass

config = MyConfig()
config.db_host = "localhost"     # 属性方式设置
config["db_port"] = 5432        # 字典方式设置
config["db_name"] = "mydb"

print(config.db_host)           # localhost
print(config["db_port"])        # 5432
```

### 5.3 解析流式 JSON

```python
from agentscope._utils._common import _parse_streaming_json_dict

# 模拟 LLM 流式输出
chunks = ['{"name": "Ali', '{"name": "Alice", "age": 25}']
result = {}
for chunk in chunks:
    result = _parse_streaming_json_dict(chunk, result)
print(result)  # {"name": "Alice", "age": 25}
```

### 5.4 从 Pydantic 模型创建工具

```python
from pydantic import BaseModel
from agentscope._utils._common import _create_tool_from_base_model

class Person(BaseModel):
    name: str
    age: int
    email: str

tool = _create_tool_from_base_model(Person, "extract_person")
# 可用于 function calling API
```

### 5.5 异步函数统一执行

```python
import asyncio
from agentscope._utils._common import _execute_async_or_sync_func

async def async_greet(name: str) -> str:
    await asyncio.sleep(0.1)
    return f"Hello, {name}!"

def sync_greet(name: str) -> str:
    return f"Hello, {name}!"

async def main():
    result1 = await _execute_async_or_sync_func(async_greet, "Alice")
    result2 = await _execute_async_or_sync_func(sync_greet, "Bob")
    print(result1, result2)

asyncio.run(main())
```

---

## 6. 练习题

### 练习 1：实现带超时的 HTTP 获取

**任务**：为 `_get_bytes_from_web_url` 添加超时参数，并处理超时异常。

**参考答案**：

```python
import requests
from requests.exceptions import ConnectTimeout, ReadTimeout

def _get_bytes_from_web_url(
    url: str,
    max_retries: int = 3,
    timeout: float = 10.0,
) -> str:
    """Fetch content from URL with timeout and retry support.

    Args:
        url: Target URL to fetch
        max_retries: Maximum number of retry attempts
        timeout: Request timeout in seconds

    Returns:
        Decoded string content from the URL

    Raises:
        requests.exceptions.Timeout: If all retries exceed timeout
        requests.exceptions.RequestException: For other request errors
    """
    last_exception = None

    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response.content.decode("utf-8")

        except (ConnectTimeout, ReadTimeout) as e:
            last_exception = e
            logger.warning(
                f"Timeout fetching {url} (attempt {attempt + 1}/{max_retries})"
            )

        except requests.exceptions.RequestException as e:
            last_exception = e
            logger.warning(
                f"Error fetching {url}: {e} (attempt {attempt + 1}/{max_retries})"
            )

    # 所有重试都失败
    raise last_exception
```

### 练习 2：扩展 DictMixin

**任务**：为 `DictMixin` 添加 `__delattr__` 支持，实现 `del obj.key` 语法。

**参考答案**：

```python
from agentscope._utils._mixin import DictMixin

class ExtendedDictMixin(DictMixin):
    """扩展 DictMixin，支持完整的属性操作语法。

    示例:
        >>> d = ExtendedDictMixin()
        >>> d.name = "test"      # 写属性
        >>> d.name               # 读属性
        'test'
        >>> del d.name           # 删除属性
        >>> d.name               # 访问已删除属性
        Traceback (most recent call last):
            AttributeError: 'ExtendedDictMixin' object has no attribute 'name'
    """

    __setattr__ = dict.__setitem__
    __getattr__ = dict.__getitem__
    __delattr__ = dict.__delitem__

    def __repr__(self) -> str:
        """支持友好打印。"""
        items = ", ".join(f"{k}={v!r}" for k, v in self.items())
        return f"{self.__class__.__name__}({items})"
```

### 练习 3：实现 JSON Schema 验证器

**任务**：使用 `_parse_tool_function` 提取工具 schema，并验证函数调用的参数是否符合 schema。

**参考答案**：

```python
import jsonschema
from agentscope._utils._common import _parse_tool_function

def validate_tool_call(tool_func: ToolFunction, **kwargs) -> tuple[bool, str]:
    """验证工具函数调用参数是否符合 schema。

    Args:
        tool_func: 工具函数定义对象
        **kwargs: 实际传入的参数

    Returns:
        (是否通过验证, 错误信息)

    示例:
        >>> schema = _parse_tool_function(tool_func, True, False, False)
        >>> valid, msg = validate_tool_call(tool_func, name="Alice", age=25)
        >>> print(valid)  # True 或 False
    """
    # 提取 schema
    schema = _parse_tool_function(tool_func, True, False, False)

    # 构建 jsonschema 格式
    json_schema = {
        "type": "object",
        "properties": schema.get("parameters", {}).get("properties", {}),
        "required": schema.get("parameters", {}).get("required", []),
    }

    try:
        jsonschema.validate(instance=kwargs, schema=json_schema)
        return True, ""

    except jsonschema.ValidationError as e:
        return False, f"参数验证失败: {e.message}"

    except jsonschema.SchemaError as e:
        return False, f"Schema 定义错误: {e.message}"
```

### 练习 4：日志格式化扩展

**任务**：修改 `_DEFAULT_FORMAT`，添加线程 ID（`%(thread)d`）信息，便于多线程调试。

**参考答案**：

```python
import logging
from agentscope._logging import _DEFAULT_FORMAT, setup_logger

# 原始格式
_original_format = _DEFAULT_FORMAT

# 扩展格式：添加线程 ID
_extended_format = (
    "%(asctime)s | %(levelname)-7s | "
    "[T:%(thread)08d] %(name)s:%(funcName)s:%(lineno)s - %(message)s"
)

def setup_logger_with_thread(
    level: str = "INFO",
    log_file: str | None = None,
) -> None:
    """设置日志系统，格式包含线程信息。

    Args:
        level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: 可选的日志文件路径
    """
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=_extended_format,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file) if log_file else logging.NullHandler(),
        ],
    )

# 使用示例
if __name__ == "__main__":
    import threading
    import time

    setup_logger_with_thread("DEBUG")

    def worker(name: str) -> None:
        logger.info(f"Worker {name} started")
        time.sleep(0.1)
        logger.info(f"Worker {name} finished")

    threads = [
        threading.Thread(target=worker, args=(f"thread-{i}",))
        for i in range(3)
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 输出示例:
    # 2024-01-15 10:30:45 | INFO    | [T:00001234] __main__:worker:42 - Worker thread-0 started
    # 2024-01-15 10:30:45 | INFO    | [T:00005678] __main__:worker:42 - Worker thread-1 started
    # 2024-01-15 10:30:45 | INFO    | [T:00009abc] __main__:worker:42 - Worker thread-2 started
```

---

## 设计模式总结

| 设计模式 | 应用位置 | 说明 |
|----------|----------|------|
| **Mixin** | DictMixin | 为类添加字典接口能力 |
| **Strategy** | JSON 修复策略 | 委托给 `json_repair` 库的 `repair_json()` 函数处理容错修复，解耦修复逻辑 |
| **Template Method** | `_create_tool_from_base_model` | 固定字段到 Schema 转换框架 |
| **Builder** | 日志格式化 | 组合多个日志处理器构建完整日志系统 |

## 小结

| 组件 | 文件 | 核心功能 |
|------|------|----------|
| 日志系统 | `_logging.py` | 统一日志格式、多输出目标 |
| 字典混入 | `_utils/_mixin.py` | 属性/字典双重访问 |
| JSON 工具 | `_utils/_common.py` | 流式 JSON 解析与修复 |
| 工具提取 | `_utils/_common.py` | 函数签名到 Schema |
| 媒体处理 | `_utils/_common.py` | Base64 编解码、音频重采样 |

工具模块体现了 AgentScope 对 LLM 应用常见需求的抽象：处理不完整 JSON、提取函数 schema、媒体数据处理等，都是构建 AI 应用时频繁遇到的问题。

## 章节关联

| 关联模块 | 关联点 |
|----------|--------|
| [工具模块](module_tool_mcp_deep.md) 第 3.2 节 | `_parse_tool_function` 生成工具 Schema（位于 `_common.py:339`） |
| [模型模块](module_model_deep.md) 第 4 节 | `_parse_streaming_json_dict` 解析 LLM 流式 JSON 输出 |
| [文件模块](module_file_deep.md) 第 3 节 | 共享 `_common.py` 中的 Base64 编解码和文件工具函数 |

### Java 开发者对照

| Python 概念 | Java 等价物 | 说明 |
|-------------|------------|------|
| `_json_loads_with_repair` | Jackson 自定义 Deserializer | 容错 JSON 解析 |
| `DictMixin` | `Map` + `AbstractMap` | 属性与 Map 双接口 |
| `inspect.signature()` | Java Reflection API | 运行时函数签名提取 |
| `logging` 模块 | SLF4J + Logback | 日志门面 + 实现 |

## 练习题

### 基础题

**Q1**: `DictMixin` 提供了哪两种访问方式？为什么这种双接口设计在 LLM 应用中有用？

**Q2**: `_json_loads_with_repair()` 为什么比标准的 `json.loads()` 更适合处理 LLM 输出？

### 中级题

**Q3**: `_parse_streaming_json_dict()` 在解析流式 JSON 时，为什么需要保留上一次成功解析的结果（`last_input`）？如果不保留会怎样？

**Q4**: `_create_tool_from_base_model()` 如何从 Pydantic BaseModel 的字段信息生成 JSON Schema？简述转换逻辑。

### 挑战题

**Q5**: 设计一个更健壮的流式 JSON 解析器，能够处理嵌套数组、转义字符和 Unicode 编码错误。需要考虑哪些边界情况？

---

### 参考答案

**A1**: `DictMixin` 同时支持 `obj.key` 属性访问和 `obj["key"]` 字典访问。在 LLM 应用中，API 响应通常是 JSON 字典，用字典语法访问很自然；而在业务逻辑中，属性语法更简洁可读。双接口让同一对象在不同上下文中都能方便使用。

**A2**: LLM 生成的 JSON 经常有格式问题：截断（超出 max_tokens）、多余逗号、缺少闭合括号、包含注释等。标准 `json.loads()` 遇到这些问题会直接报错，而 `_json_loads_with_repair()` 会尝试修复常见问题后再解析。

**A3**: `_parse_streaming_json_dict()` 使用 `last_input` 保留上一次成功解析的结果，因为流式传输中中间 chunk 的 JSON 可能不完整（如截断的 tool_use 参数），`_json_loads_with_repair` 修复后可能丢失字段。保留上一次结果可以确保不回退——只有当修复后的结果比上一次更完整时才更新。如果不保留，中间的不完整 chunk 会导致已解析的字段丢失。

**A4**: 转换逻辑：(1) 获取 BaseModel 的 `model_fields` 字典；(2) 遍历每个字段，提取类型注解、描述（来自 `Field(description=...)`）、默认值；(3) 将 Python 类型映射到 JSON Schema 类型（str→string, int→integer, list→array 等）；(4) 组装为符合 OpenAI function calling 格式的 JSON Schema。

**A5**: 关键边界情况：(1) 字符串内的 JSON 结构化字符（花括号、方括号、引号）；(2) Unicode 转义序列（`\uXXXX`）；(3) 多行字符串值；(4) 数字精度问题；(5) 截断发生在键名中间的情况。建议使用状态机解析器，维护字符串/数组/对象三层嵌套状态。

---

*文档版本: 1.0*
*最后更新: 2026-04-28*
