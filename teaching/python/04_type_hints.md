# 04 - 类型提示

## 为什么需要类型提示？

Python 是动态类型语言，但类型提示让代码更清晰：

```python
# 无类型提示
def process(data, config):
    return data

# 有类型提示 - 类似 Java 的类型声明
def process(data: dict, config: dict) -> dict:
    return data
```

`★ Insight ─────────────────────────────────────`
- 类型提示是**可选的**，不影响运行时行为
- 主要用于：IDE 智能提示、静态检查、文档
- 类似 TypeScript 的类型注解，但更轻量
`─────────────────────────────────────────────────`

## 基础类型

```python
# 基础类型
name: str = "Alice"      # String
age: int = 25            # int/Integer
height: float = 1.75    # double
is_active: bool = True   # boolean

# 容器类型（Java 泛型语法不同）
names: list[str] = ["Alice", "Bob"]        # List<String>
scores: dict[str, int] = {"A": 90}        # Map<String, Integer>
items: tuple[int, str] = (1, "one")        # Pair<Integer, String>
unique: set[int] = {1, 2, 3}              # Set<Integer>

# 可选类型
maybe: str | None = None                   # Optional<String>
maybe_int: int | None = None
```

| Python | Java |
|--------|------|
| `str` | `String` |
| `int` | `int` / `Integer` |
| `float` | `double` / `Double` |
| `bool` | `boolean` |
| `list[T]` | `List<T>` |
| `dict[K, V]` | `Map<K, V>` |
| `set[T]` | `Set<T>` |
| `T \| None` | `Optional<T>` 或 `@Nullable T` |
| `Any` | `Object` |

## AgentScope 源码示例

**文件**: `src/agentscope/agent/_agent_base.py`

```python
class AgentBase:
    # 类型别名
    supported_hook_types: list[str] = [
        "pre_reply",
        "post_reply",
    ]

    # 类级别属性（OrderedDict）
    _class_pre_reply_hooks: dict[
        str,
        Callable[  # Callable[[int, str], bool] 类似 Java 的 Function<T, R>
            [
                "AgentBase",      # self
                dict[str, Any],   # kwargs
            ],
            dict[str, Any] | None,
        ],
    ] = OrderedDict()

    def __init__(self) -> None:
        # 实例属性带类型
        self.id: str = shortuuid.uuid()
        self._reply_task: Task | None = None  # Union[Task, None]
        self._subscribers: dict[str, list[AgentBase]] = {}
```

## Union 和 Optional

```python
# Union - 联合类型，类似 Java 的多重类型
def process(value: int | str | float) -> str:
    """接受多种类型"""
    return str(value)

# Optional - 可选类型，类似 Java 8 Optional
def greet(name: str | None) -> str:
    if name is None:
        return "Hello, stranger"
    return f"Hello, {name}"

# 等价于
from typing import Optional
def greet(name: Optional[str]) -> str:
    ...
```

## Callable（函数类型）

```python
from typing import Callable

# Callable[[参数类型...], 返回类型]
def apply(func: Callable[[int, int], int], x: int, y: int) -> int:
    """接收一个函数作为参数"""
    return func(x, y)

# Java 对照：
# IntBinaryOperator apply(Function<Integer, Integer> func, int x, int y)

# 使用
result = apply(lambda a, b: a + b, 1, 2)  # 3
result = apply(lambda a, b: a * b, 3, 4)   # 12

# 无参数的 Callable
def execute(func: Callable[[], None]) -> None:
    func()
```

## Literal（字面量类型）

```python
from typing import Literal

# 限制为特定值，类似 Java 枚举
def move(direction: Literal["up", "down", "left", "right"]) -> None:
    pass

move("up")    # OK
move("north") # Error!

# AgentScope 中的用法
role: Literal["user", "assistant", "system"]
```

## TypeAlias（类型别名）

```python
from typing import TypeAlias

# 定义类型别名，类似 Java 的 type alias
AgentId: TypeAlias = str
Timestamp: TypeAlias = str
JSONObject: TypeAlias = dict[str, Any]

# 使用
def get_agent(agent_id: AgentId) -> Agent:
    ...

timestamp: Timestamp = "2024-01-01"
```

## Protocol（结构子类型）

```python
from typing import Protocol

# Protocol 定义结构化类型，类似 Java 的接口（但更灵活）
class Readable(Protocol):
    def read(self, size: int = -1) -> bytes: ...

# 任何有 read() 方法的类都满足 Protocol
class File:
    def read(self, size: int = -1) -> bytes:
        return b"file content"

class Socket:
    def read(self, size: int = -1) -> bytes:
        return b"socket data"

# Java 对照：Readable 像是一个只定义了 read 方法的接口
def process(r: Readable) -> None:
    data = r.read()

process(File())   # OK - 有 read 方法
process(Socket()) # OK - 有 read 方法
```

## 泛型

```python
from typing import TypeVar, Generic

# TypeVar - 泛型变量，类似 Java 的 <T>
T = TypeVar('T')
U = TypeVar('U')

# 泛型类
class Box(Generic[T]):
    def __init__(self, content: T) -> None:
        self.content = content

    def get(self) -> T:
        return self.content

# 泛型函数
def first(lst: list[T]) -> T | None:
    return lst[0] if lst else None

# 使用
box: Box[str] = Box("hello")
result: str = box.get()

# Java 对照：
# class Box<T> { T content; T get() { return content; } }
# Box<String> box = new Box<>("hello");
```

## 复杂类型示例

```python
from typing import Callable, TypeVar, Any

# AgentScope 中的复杂类型提示
MsgContent: TypeAlias = str | Sequence[ContentBlock]

HookFunc: TypeAlias = Callable[
    ["AgentBase", dict[str, Any]],
    dict[str, Any] | None
]

def register_hook(
    hook: HookFunc,
    name: str,
) -> None:
    ...
```

## typing 模块速查表

| Python | Java | 说明 |
|--------|------|------|
| `str` | `String` | 字符串 |
| `int` | `int` | 整数 |
| `float` | `double` | 浮点数 |
| `bool` | `boolean` | 布尔 |
| `list[T]` | `List<T>` | 列表 |
| `dict[K, V]` | `Map<K, V>` | 字典 |
| `set[T]` | `Set<T>` | 集合 |
| `tuple[T, U]` | `Pair<T, U>` | 元组 |
| `T \| None` | `@Nullable T` | 可空 |
| `Any` | `Object` | 任意类型 |
| `Callable[[...], R]` | `Function<T, R>` | 函数类型 |
| `TypeVar('T')` | `<T>` | 泛型变量 |
| `Generic[T]` | `<T>` | 泛型类 |

## 类型检查工具

Python 类型提示配合静态检查工具使用：

```bash
# mypy - 静态类型检查
pip install mypy
mypy your_code.py

# pyright - Microsoft 的类型检查器
pip install pyright
pyright your_code.py
```

```python
# 示例：mypy 会报错
def greet(name: str) -> str:
    return f"Hello, {name}"

greet(123)  # Error: Argument 1 to "greet" has incompatible type "int"; expected "str"
```

## 练习题

1. **添加类型注解**：为以下函数添加类型注解
   ```python
   def process(data, options):
       return data
   ```

2. **类型对照**：Python 类型转 Java
   ```python
   # a) list[str]
   # b) dict[str, int]
   # c) str | None
   # d) Callable[[int], str]
   ```

3. **修复类型错误**：
   ```python
   from typing import Union

   def get_name(user: Union[str, None]) -> str:
       return user  # 错误在哪？
   ```

---

**答案**：

```python
# 1.
def process(data: dict, options: dict) -> dict:
    return data

# 2.
# a) List<String>
# b) Map<String, Integer>
# c) @Nullable String 或 Optional<String>
# d) Function<Integer, String> 或 IntFunction<String>

# 3.
def get_name(user: str | None) -> str:
    if user is None:
        return "Anonymous"
    return user
# 问题：user 可能为 None，但返回类型是 str
# 解决：添加 None 检查
```
