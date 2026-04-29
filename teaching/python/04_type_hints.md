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

**文件**: `src/agentscope/agent/_agent_base.py:30-138`

```python
class AgentBase:
    # AgentBase 支持的钩子类型（共6种）
    supported_hook_types: list[str] = [
        "pre_reply",     # 回复前
        "post_reply",    # 回复后
        "pre_print",    # 打印前
        "post_print",    # 打印后
        "pre_observe",  # 观察前
        "post_observe", # 观察后
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

### AgentScope 中的 Union 使用

**文件**: `src/agentscope/message/_message_base.py:24-73`

```python
def __init__(
    self,
    name: str,
    content: str | Sequence[ContentBlock],  # str 或 ContentBlock 序列
    role: Literal["user", "assistant", "system"],
    metadata: dict[str, JSONSerializableObject] | None = None,  # 可选字典
    timestamp: str | None = None,
    invocation_id: str | None = None,
) -> None:
```

**Java 对照**：

```java
// Java 没有直接等价物，使用重载或 Object
public void process(Object value) {}

// Java 没有联合类型，通常使用方法重载
public void process(int value) { ... }
public void process(String value) { ... }

// 可选类型：Java 用 @Nullable 注解（不是 Optional 参数）
@Nullable
public String greet(@Nullable String name) {
    if (name == null) return "Hello, stranger";
    return "Hello, " + name;
}
```

## Callable（函数类型）

```python
from typing import Callable

# Callable[[参数类型...], 返回类型]
def apply(func: Callable[[int, int], int], x: int, y: int) -> int:
    """接收一个函数作为参数"""
    return func(x, y)

# Java 对照：
# IntBinaryOperator apply(IntBinaryOperator func, int x, int y)

# 使用
result = apply(lambda a, b: a + b, 1, 2)  # 3
result = apply(lambda a, b: a * b, 3, 4)   # 12

# 无参数的 Callable
def execute(func: Callable[[], None]) -> None:
    func()

# 返回 Callable
def create_adder(n: int) -> Callable[[int], int]:
    return lambda x: x + n

add5 = create_adder(5)
result = add5(10)  # 15
```

## Literal（字面量类型）

```python
from typing import Literal

# 限制为特定值，类似 Java 枚举
def move(direction: Literal["up", "down", "left", "right"]) -> None:
    pass

move("up")    # OK
move("north") # 静态检查报错（mypy/pyright），运行时不报错

# AgentScope 中的用法
role: Literal["user", "assistant", "system"]
```

**Java 对照**：

```java
// Java 枚举
public enum Direction { UP, DOWN, LEFT, RIGHT }

public void move(Direction direction) {}

move(Direction.UP);    // OK
// move("north")       // 编译错误
```

### AgentScope 中的 Literal 使用

**文件**: `src/agentscope/message/_message_base.py:30`

```python
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

# 复杂类型别名
MsgContent: TypeAlias = str | Sequence[ContentBlock]
HookFunc: TypeAlias = Callable[
    ["AgentBase", dict[str, Any]],
    dict[str, Any] | None
]
```

**Java 对照**：

```java
// Java 没有类型别名机制
// 最接近的方式是定义一个包装类或使用继承
// 方式1：包装类（开销大）
public class AgentId { private final String value; ... }

// 方式2：常量命名约定（只是命名提示，不是类型安全）
// 无法在方法签名中区分 AgentId 和普通 String
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

**Java 对照**：

```java
// Java 接口（必须显式实现）
interface Readable {
    byte[] read(int size);
}

class File implements Readable {
    @Override
    public byte[] read(int size) {
        return "file content".getBytes();
    }
}

// Java 没有 Protocol 的结构化类型，只能用 Object + 反射模拟
public static void process(Object r) {
    // 运行时通过反射调用 read 方法，无编译时检查
}
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

    def put(self, content: T) -> None:
        self.content = content

# 泛型函数
def first(lst: list[T]) -> T | None:
    return lst[0] if lst else None

# 多泛型参数
def pair(a: T, b: U) -> tuple[T, U]:
    return (a, b)

# 使用
box: Box[str] = Box("hello")
result: str = box.get()

first_result: int | None = first([1, 2, 3])
p: tuple[int, str] = pair(1, "one")

# Java 对照：
# class Box<T> { T content; T get() { return content; } }
# Box<String> box = new Box<>("hello");
# T first(List<T> lst) { return lst.isEmpty() ? null : lst.get(0); }
```

### 泛型约束

```python
from typing import TypeVar

# 约束泛型必须继承自某个类
class Animal:
    def speak(self) -> str:
        raise NotImplementedError

class Dog(Animal):
    def speak(self) -> str:
        return "Woof"

# TypeVar 约束
S = TypeVar('S', bound=Animal)

def make_speak(animal: S) -> str:
    return animal.speak()

# 使用
dog = Dog()
result = make_speak(dog)  # OK
# make_speak("string")    # Error - str 不是 Animal 子类
```

**Java 对照**：

```java
// Java 泛型约束
public <T extends Animal> String makeSpeak(T animal) {
    return animal.speak();
}

// T 必须是 Animal 或其子类
```

## 复杂类型示例

### AgentScope 中的复杂类型

**文件**: `src/agentscope/agent/_agent_base.py:46-138`

```python
# 复杂的 Hook 函数类型
_class_pre_reply_hooks: dict[
    str,
    Callable[
        [
            "AgentBase",      # self 参数类型
            dict[str, Any],   # kwargs 参数类型
        ],
        dict[str, Any] | None,  # 返回类型
    ],
] = OrderedDict()

# AgentBase 的 reply 方法签名
async def reply(self, *args: Any, **kwargs: Any) -> Msg:
    ...
```

### AgentScope Msg 类型

**文件**: `src/agentscope/message/_message_base.py:24-73`

```python
class Msg:
    def __init__(
        self,
        name: str,
        content: str | Sequence[ContentBlock],  # 联合类型
        role: Literal["user", "assistant", "system"],  # 字面量类型
        metadata: dict[str, JSONSerializableObject] | None = None,  # 可选字典
        timestamp: str | None = None,
        invocation_id: str | None = None,
    ) -> None:
        self.name = name
        self.content = content
        self.role = role
        self.metadata = metadata or {}
        self.id = shortuuid.uuid()
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
| `Literal["a", "b"]` | 枚举值 | 字面量 |
| `TypeAlias` | typedef | 类型别名 |
| `Protocol` | 接口 | 结构化类型 |

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

### IDE 集成

- **VS Code**: 安装 Pylance 插件
- **PyCharm**: 内置支持类型检查
- **Ruff**: 快速检查工具，支持类型相关规则

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

4. **定义类型别名**：为以下代码定义合适的类型别名
   ```python
   def send_message(
       message: dict[str, str | int | bool],
       recipients: list[dict[str, str]]
   ) -> dict[str, str | None]:
       ...
   ```

5. **Protocol 使用**：定义一个 `Describable` Protocol，并实现它

6. **泛型函数**：实现一个 `batch_process` 函数，处理 `list[T]` 并返回 `list[T]`

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

# 4.
from typing import TypeAlias

JSONValue: TypeAlias = str | int | bool
MessageData: TypeAlias = dict[str, JSONValue]
Recipient: TypeAlias = dict[str, str]
SendResult: TypeAlias = dict[str, str | None]

def send_message(
    message: MessageData,
    recipients: list[Recipient]
) -> SendResult:
    ...

# 5.
from typing import Protocol

class Describable(Protocol):
    def describe(self) -> str:
        """返回描述信息"""
        ...

class Person:
    def __init__(self, name: str) -> None:
        self.name = name

    def describe(self) -> str:
        return f"Person: {self.name}"

def print_description(obj: Describable) -> None:
    print(obj.describe())

# 6.
from typing import TypeVar, Callable

T = TypeVar('T')

def batch_process(items: list[T], processor: Callable[[T], T]) -> list[T]:
    """处理列表中的每个元素"""
    return [processor(item) for item in items]

# 使用
numbers = [1, 2, 3, 4]
doubled = batch_process(numbers, lambda x: x * 2)  # [2, 4, 6, 8]
```

## 附录：本文关键字简写对照表

| 简写 | 全称 | 说明 |
|------|------|------|
| `str` | **str**ing | 字符串 |
| `int` | **int**eger | 整数 |
| `float` | **float**ing point | 浮点数 |
| `bool` | **bool**ean | 布尔 |
| `dict` | **dict**ionary | 字典 |
| `list` | **list** | 列表 |
| `set` | **set** | 集合 |
| `tuple` | **tuple** | 元组 |
| `Any` | **any** type | 任意类型 |
| `Optional[T]` | **option**al | 等价于 `T \| None` |
| `Union` | **union** | 联合类型（`T1 \| T2`） |
| `Callable` | **callable** | 可调用类型（函数） |
| `Literal` | **literal** | 字面量类型 |
| `TypeVar` | **type var**iable | 泛型变量（`<T>`） |
| `Generic` | **generic** | 泛型基类 |
| `Protocol` | **protocol** | 结构化类型（接口） |
| `TypeAlias` | **type alias** | 类型别名 |
| `Sequence` | **sequence** | 序列类型（list/tuple 的抽象） |
