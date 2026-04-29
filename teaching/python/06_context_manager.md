# 06 - 上下文管理器

## Java 对照：try-with-resources

```java
// Java try-with-resources - 自动关闭资源
try (BufferedReader reader = new BufferedReader(
        new FileReader("file.txt"))) {
    String line = reader.readLine();
    // 使用资源
} // 自动调用 close()

// 或者实现 AutoCloseable
public class DatabaseConnection implements AutoCloseable {
    @Override
    public void close() {
        // 清理资源：关闭连接、释放锁等
    }
}

try (DatabaseConnection conn = new DatabaseConnection()) {
    conn.query();
} // 自动调用 close()
```

`★ Insight ─────────────────────────────────────`
- Python `with` vs Java `try-with-resources`：功能完全等价
- `__enter__`/`__exit__` 对应 Java 的 `try` 块开始/结束
- Java 的 checked exception 在 Python 中不存在
- Python 的 `async with` 在 Java 中没有直接对应
`─────────────────────────────────────────────────`

## Python 上下文管理器基础

```python
# Python with 语句 - 类似 Java try-with-resources
with open("file.txt") as f:
    content = f.read()
# 自动调用 f.close()

# async with - 异步版本（Java 没有对应）
async with aiohttp.ClientSession() as session:
    async with session.get(url) as response:
        content = await response.text()
```

## 协议定义（双下划线方法）

```python
class ContextManager:
    def __enter__(self):
        """进入上下文 - 返回绑定到 as 的对象"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文 - 无论是否异常都会执行"""
        # exc_type/val/tb 是异常信息，如果无异常则为 None
        return False  # 返回 True 阻止异常传播

# 使用
with ContextManager() as cm:
    # 在 with 块内
    pass
# 退出时自动调用 __exit__
```

### 执行流程

```python
class TraceContext:
    def __enter__(self):
        print("1. __enter__ called")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        print(f"2. __exit__ called - exc_type={exc_type}")
        return False

with TraceContext():
    print("3. Inside with block")

# 输出:
# 1. __enter__ called
# 3. Inside with block
# 2. __exit__ called - exc_type=None
```

### 异常处理详解

```python
class ErrorHandledContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            print(f"Caught exception: {exc_val}")
            return True  # 阻止异常传播
        return False

with ErrorHandledContext():
    raise ValueError("test error")
# 不会抛出！异常被 __exit__ 捕获

print("Continuing after with block")  # 继续执行
```

## AgentScope 源码示例

**文件**: `src/agentscope/pipeline/_msghub.py:73`

```python
class MsgHub:
    """消息中心 - 异步上下文管理器"""

    async def __aenter__(self) -> "MsgHub":
        """Will be called when entering the MsgHub."""
        self._reset_subscriber()
        if self.announcement is not None:
            await self.broadcast(msg=self.announcement)
        return self

    async def __aexit__(self, *args: Any, **kwargs: Any) -> None:
        """Will be called when exiting the MsgHub."""
        if self.enable_auto_broadcast:
            for agent in self.participants:
                agent.remove_subscribers(self.name)

# 使用
async with MsgHub(participants=[agent1, agent2]) as hub:
    await agent1()
    await agent2()
# 退出时自动清理订阅
```

**文件**: `src/agentscope/session/_redis_session.py:184`

```python
class RedisSession:
    async def __aenter__(self) -> "RedisSession":
        """Enter the async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        """Exit the async context manager and close the connection."""
        await self.close()
```

**Java 对照**：

```java
// Java 单机版的 MsgHub 等价
public class MsgHub implements AutoCloseable {
    @Override
    public void close() {
        // 清理订阅者
        if (enableAutoBroadcast) {
            for (Agent agent : participants) {
                agent.removeSubscribers(name);
            }
        }
    }

    // Java 没有 async try-with-resources
    // 必须手动调用 close() 或使用 synchronous wrapper
}
```

## 简化写法：@contextmanager

```python
from contextlib import contextmanager

@contextmanager
def managed_resource():
    """简化上下文管理器"""
    resource = acquire_resource()
    try:
        yield resource  # 相当于 __enter__ 返回值
    finally:
        release_resource()  # 相当于 __exit__

# 使用
with managed_resource() as res:
    res.use()
# 自动处理异常和清理
```

### AgentScope Session 示例

**文件**: `src/agentscope/memory/_working_memory/_sqlalchemy_memory.py:176`

```python
@asynccontextmanager
async def _write_session(self) -> AsyncIterator[None]:
    """获取写锁并自动提交/回滚会话"""
    async with self._lock:
        try:
            yield
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

# 使用
async with self._write_session():
    await self.session.add(entity)
# 自动 commit 或 rollback
```

## contextmanager 进阶

### 1. 异常处理

```python
from contextlib import contextmanager

@contextmanager
def safe_operation():
    """安全的操作上下文 - 注意 @contextmanager 只能 yield 一次"""
    resource = acquire_resource()
    try:
        yield resource  # yield 只能出现一次！
    except ValueError as e:
        # 捕获 with 块内抛出的特定异常
        print(f"ValueError caught: {e}")
        # 异常被捕获后不会继续传播
    finally:
        # 无论是否异常，都会执行清理
        print("清理资源")
        release_resource(resource)

# 正常情况
with safe_operation() as result:
    print(result)  # 正常使用资源

# ValueError 情况 - 异常被 __exit__ 捕获
with safe_operation() as result:
    raise ValueError("test")
# 输出: ValueError caught: test
#       清理资源
```

### 2. 组合多个上下文

```python
from contextlib import contextmanager

@contextmanager
def timer():
    """计时上下文"""
    import time
    start = time.time()
    yield
    print(f"Elapsed: {time.time() - start:.2f}s")

@contextmanager
def logger(name):
    """日志上下文"""
    print(f"[{name}] Start")
    yield
    print(f"[{name}] End")

# 嵌套使用
with timer():
    with logger("outer"):
        with logger("inner"):
            print("Doing work")

# 输出:
# [outer] Start
# [inner] Start
# Doing work
# [inner] End
# [outer] End
# Elapsed: 0.00s
```

### 3. 上下文管理器参数传递

```python
from contextlib import contextmanager

@contextmanager
def db_transaction(conn):
    """数据库事务上下文"""
    conn.begin()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise

# 使用
with db_transaction(conn) as tx:
    tx.execute("INSERT ...")
    tx.execute("UPDATE ...")
# 自动 commit 或 rollback
```

## 异步上下文管理器

```python
class AsyncContextManager:
    async def __aenter__(self):
        await async_setup()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await async_cleanup()
        return False

# 使用
async with AsyncContextManager() as acm:
    await acm.operation()
```

### @asynccontextmanager

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def http_session(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            yield response
            # 清理代码放这里

# 使用
async with http_session("http://example.com") as resp:
    data = await resp.json()
# 自动关闭 session
```

## 使用场景对比

| 场景 | Python | Java |
|------|--------|------|
| 文件操作 | `with open()` | try-with-resources |
| 数据库连接 | `with db.connection()` | try-with-resources |
| 锁管理 | `with lock:` | `lock.lock(); try {} finally {lock.unlock();}` |
| 临时状态 mock | `with mock.patch():` | @Before/@After + try-finally |
| 计时 | `with timer():` | StopWatch |
| 网络请求 | `async with session:` | 手动管理 |
| 线程本地状态 | `with contextvars:` | ThreadLocal |

## closing() - 资源释放辅助

```python
from contextlib import closing
import urllib.request

# 某些资源没有实现上下文管理器协议，但有 close() 方法
# 注：urlopen 在新版 Python 已支持 with，此处仅为 closing() 用法演示
with closing(urllib.request.urlopen("http://example.com")) as page:
    html = page.read()
# 自动调用 page.close()
```

## 练习题

1. **创建计时器**：创建一个 `Timer` 上下文管理器，测量代码执行时间并打印

2. **实现数据库连接池**：为以下类实现上下文管理器协议
   ```python
   class DatabasePool:
       def get_connection(self): ...
       def release_connection(self, conn): ...
   ```

3. **修复资源泄漏**：
   ```python
   @contextmanager
   def bad_resource():
       resource = get_resource()
       yield resource
       # 忘记清理！

   with bad_resource() as r:
       r.use()
   # 资源泄漏了吗？
   ```

4. **理解执行顺序**：以下代码输出什么？
   ```python
   class Test:
       def __enter__(self):
           print("1")
           return self
       def __exit__(self, *args):
           print("2")

   with Test() as t:
       print("3")
   ```

5. **异常传播**：以下代码会输出什么？
   ```python
   class Test:
       def __enter__(self):
           return self
       def __exit__(self, exc_type, exc_val, exc_tb):
           print(f"Caught: {exc_val}")
           return True  # 阻止传播

   with Test():
       raise ValueError("error")
   print("continued")
   ```

---

**答案**：

```python
# 1. 计时器
from contextlib import contextmanager
import time

@contextmanager
def timer():
    start = time.time()
    yield
    print(f"Elapsed: {time.time() - start:.3f}s")

with timer():
    sum(i * i for i in range(1000000))

# 2. 数据库连接池
from contextlib import contextmanager

class DatabasePool:
    def __init__(self):
        self.connections = []

    @contextmanager
    def connection(self):
        conn = self.get_connection()
        try:
            yield conn
        finally:
            self.release_connection(conn)

# 使用
with pool.connection() as conn:
    conn.query()

# 3.
# 是的，资源会泄漏！yield 后没有 finally 清理
# 正确写法：
@contextmanager
def good_resource():
    resource = get_resource()
    try:
        yield resource
    finally:
        release_resource()  # 必须清理

# 4.
# 1
# 3
# 2
# 顺序：__enter__ -> with block -> __exit__

# 5.
# Caught: error
# continued
# 异常被 __exit__ 捕获并阻止传播，程序继续执行
```

## 附录：本文关键字简写对照表

| 简写 | 全称 | 说明 |
|------|------|------|
| `__enter__` | **enter** | 进入上下文（`with` 块开始） |
| `__exit__` | **exit** | 退出上下文（`with` 块结束） |
| `__aenter__` | **a**sync **enter** | 异步进入上下文 |
| `__aexit__` | **a**sync **exit** | 异步退出上下文 |
| `@contextmanager` | **context manager** | 上下文管理器装饰器 |
| `@asynccontextmanager` | **async context manager** | 异步上下文管理器装饰器 |
| `yield` | **yield** | 暂停生成器，返回值 |
| `exc_type` | **exc**eption **type** | 异常类型 |
| `exc_val` | **exc**eption **val**ue | 异常值 |
| `exc_tb` | **exc**eption **t**race**b**ack | 异常回溯 |
| `AutoCloseable` | **auto close**able | Java 自动关闭接口 |
| `closing()` | **closing** | 为有 close() 的对象包装上下文 |
| `commit` | **commit** | 提交事务 |
| `rollback` | **rollback** | 回滚事务 |
