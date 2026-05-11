# 附录 A：Python 进阶速查

本附录汇总全书涉及的 Python 进阶概念，供查阅。

---

## async/await

`async` 定义异步函数，`await` 等待异步操作完成。

```python
import asyncio

async def fetch_data():
    await asyncio.sleep(1)  # 模拟 IO 等待
    return "data"

async def main():
    result = await fetch_data()
    print(result)

asyncio.run(main())
```

**要点**：
- `async def` 定义的函数是**协程**，不会立即执行
- `await` 暂停当前协程，让事件循环执行其他任务
- `asyncio.run()` 启动事件循环
- `asyncio.gather()` 并发执行多个协程

---

## TypedDict

`TypedDict` 给字典添加类型提示，不改变运行时行为。

```python
from typing import TypedDict

class Person(TypedDict):
    name: str
    age: int

p: Person = {"name": "Alice", "age": 30}
print(p["name"])  # "Alice"
```

**要点**：
- 运行时就是普通 `dict`
- 类型检查器（mypy）会检查字段名和类型
- `total=False` 表示所有字段可选

---

## ContextVar

`ContextVar` 为每个异步任务提供独立的变量副本。

```python
from contextvars import ContextVar

user_id: ContextVar[str] = ContextVar("user_id", default="")

async def handle_request(name):
    user_id.set(name)
    await asyncio.sleep(0.1)
    print(f"{user_id.get()} processing")  # 不受其他任务影响

async def main():
    await asyncio.gather(
        handle_request("Alice"),
        handle_request("Bob"),
    )
```

---

## 元类（Metaclass）

元类是"创建类的类"。可以拦截类定义过程，修改类的属性和方法。

```python
class MyMeta(type):
    def __new__(mcs, name, bases, attrs):
        # 类定义时自动执行
        if "reply" in attrs:
            attrs["reply"] = wrap(attrs["reply"])
        return super().__new__(mcs, name, bases, attrs)

class Agent(metaclass=MyMeta):
    async def reply(self):  # 自动被 wrap 包装
        ...
```

---

## functools.wraps

装饰器用 `@wraps` 保留原函数的名称和文档。

```python
from functools import wraps

def my_decorator(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper

@my_decorator
def my_function():
    """My docstring"""
    pass

print(my_function.__name__)  # "my_function"（没有 @wraps 会是 "wrapper"）
```

---

## AsyncGenerator

异步生成器用 `async for` 迭代，用 `yield` 产生值。

```python
async def stream_data():
    for i in range(3):
        await asyncio.sleep(0.1)
        yield i

async def main():
    async for value in stream_data():
        print(value)

asyncio.run(main())
```

---

## Pydantic BaseModel

Pydantic 的 `BaseModel` 自动生成 JSON Schema，用于数据验证。

```python
from pydantic import BaseModel

class UserProfile(BaseModel):
    name: str
    age: int = 0

# 自动生成 JSON Schema
schema = UserProfile.model_json_schema()
# {"type": "object", "properties": {"name": {"type": "string"}, ...}}

# 自动验证
user = UserProfile(name="Alice", age=30)
```

---

## inspect.signature

`inspect.signature()` 获取函数的参数签名。

```python
import inspect

def greet(name: str, greeting: str = "Hello"):
    pass

sig = inspect.signature(greet)
for param_name, param in sig.parameters.items():
    print(f"{param_name}: {param.annotation}, default={param.default}")
# name: <class 'str'>, default=<Parameter.empty>
# greeting: <class 'str'>, default=Hello
```

---

## OrderedDict

有序字典，保持插入顺序。Python 3.7+ 的普通 `dict` 也是有序的，但 `OrderedDict` 提供了额外的 `move_to_end()` 等方法。

---

## deepcopy

`copy.deepcopy()` 递归复制对象及其所有嵌套对象。

```python
from copy import deepcopy

original = {"msgs": [Msg("user", "hello", "user")]}
copied = deepcopy(original)  # 完全独立的副本
```
