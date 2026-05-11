# 附录 A：Python 进阶速查

本书用到的 Python 进阶概念速查。

---

## async/await

```python
async def fetch_data():
    result = await some_io_operation()
    return result

# 运行
import asyncio
asyncio.run(fetch_data())
```

## 元类

```python
class MyMeta(type):
    def __new__(mcs, name, bases, attrs):
        # 类创建时自动执行
        return super().__new__(mcs, name, bases, attrs)

class MyClass(metaclass=MyMeta):
    pass
```

## TypedDict

```python
from typing_extensions import TypedDict
from typing import Required

class MyData(TypedDict, total=False):
    type: Required[str]
    value: int
```

## ContextVar

```python
from contextvars import ContextVar

my_var = ContextVar("my_var", default="default")
my_var.set("new_value")
print(my_var.get())  # "new_value"
```

## 描述符（descriptor）

```python
class MyDescriptor:
    def __get__(self, obj, objtype=None):
        return self.value

    def __set__(self, obj, value):
        self.value = value
```

## AsyncGenerator

```python
async def stream_data():
    for i in range(3):
        yield i

async for item in stream_data():
    print(item)
```
