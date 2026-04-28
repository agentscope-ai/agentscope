# 06 - 上下文管理器

## Java 对照：try-with-resources

```java
// Java try-with-resources - 自动关闭资源
try (FileInputStream fis = new FileInputStream("file.txt");
     BufferedReader reader = new BufferedReader(fis)) {
    String line = reader.readLine();
    // 使用资源
} // 自动调用 close()

// 或者实现 AutoCloseable
public class DatabaseConnection implements AutoCloseable {
    @Override
    public void close() {
        // 清理资源
    }
}

try (DatabaseConnection conn = new DatabaseConnection()) {
    conn.query();
} // 自动调用 close()
```

## Python 上下文管理器

```python
# Python with 语句 - 类似 Java try-with-resources
with open("file.txt") as f:
    content = f.read()
# 自动调用 f.close()

# async with - 异步版本
async with aiohttp.ClientSession() as session:
    async with session.get(url) as response:
        content = await response.text()
```

`★ Insight ─────────────────────────────────────`
- `with` 语句自动管理资源（类似 Java try-with-resources）
- `__enter__` = try 块入口，`__exit__` = finally 块
- `async with` 用于异步资源管理
`─────────────────────────────────────────────────`

## 协议定义

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

## AgentScope 源码示例

**文件**: `src/agentscope/pipeline/_msghub.py`

```python
class MsgHub:
    """消息中心 - 异步上下文管理器"""

    async def __aenter__(self) -> "MsgHub":
        """进入上下文 - 设置订阅者"""
        self._reset_subscriber()
        if self.announcement is not None:
            await self.broadcast(msg=self.announcement)
        return self

    async def __aexit__(self, *args: Any, **kwargs: Any) -> None:
        """退出上下文 - 清理订阅者"""
        if self.enable_auto_broadcast:
            for agent in self.participants:
                agent.remove_subscribers(self.name)

# 使用
async with MsgHub(participants=[agent1, agent2]) as hub:
    await agent1()
    await agent2()
# 退出时自动清理订阅
```

**Java 对照**：

```java
// Java 异步版本的 AutoCloseable（实际没有内置）
public class MsgHub implements AutoCloseable {
    @Override
    public void close() {
        // 清理订阅者
    }
}

// 注意：Java 没有 async try-with-resources，需要手动处理
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

### Session 示例

**文件**: `src/agentscope/session/_json_session.py`

```python
from contextlib import contextmanager

class JsonSession:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.data = None

    @contextmanager
    async def session(self):
        """异步上下文管理器"""
        self.data = await self.load()
        try:
            yield self.data
        finally:
            await self.save(self.data)

# 使用
async with session.json_session("data.json") as data:
    data["key"] = "value"
# 自动保存
```

## contextmanager 进阶

### 异常处理

```python
from contextlib import contextmanager

@contextmanager
def safe_operation():
    try:
        # 正常操作
        yield "success"
    except ValueError as e:
        # 捕获特定异常
        print(f"ValueError: {e}")
        yield "fallback"
    except Exception as e:
        # 其他异常可以重新抛出
        raise
    finally:
        print("清理资源")
```

### 组合上下文

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
def logger():
    """日志上下文"""
    print("Start")
    yield
    print("End")

# 组合使用
with timer():
    with logger():
        print("Doing work")

# 输出:
# Start
# Doing work
# End
# Elapsed: 0.00s
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

## 使用场景

| 场景 | Python | Java |
|------|--------|------|
| 文件操作 | `with open()` | try-with-resources |
| 数据库连接 | `with db.connection()` | try-with-resources |
| 锁 | `with lock:` | `lock.lock(); try {} finally {lock.unlock();}` |
| 临时状态 | `with mock.patch():` | @Before/@After |
| 计时 | `with timer():` | StopWatch |

## 练习题

1. **创建上下文管理器**：创建一个 `Timer` 上下文管理器，测量代码执行时间

2. **实现协议**：为以下类实现上下文管理器协议
   ```python
   class DatabasePool:
       def get_connection(self): ...
       def release_connection(self, conn): ...
   ```

3. **修复代码**：
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

---

**答案**：

```python
# 1.
from contextlib import contextmanager
import time

@contextmanager
def timer():
    start = time.time()
    yield
    print(f"Elapsed: {time.time() - start:.3f}s")

with timer():
    sum(i * i for i in range(1000000))

# 2.
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
# 是的，资源会泄漏！
# 正确写法：
@contextmanager
def good_resource():
    resource = get_resource()
    try:
        yield resource
    finally:
        release_resource()  # 必须清理
```
