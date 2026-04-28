# 02 - 异步编程 async/await

## 为什么需要异步？

### Java 对照：CompletableFuture

```java
// Java 同步调用 - 阻塞等待
String result = httpClient.get(url);  // 阻塞

// Java 异步调用 - 非阻塞
CompletableFuture<String> future = httpClient.getAsync(url);
future.thenApply(result -> {
    // 处理结果
    return result.toUpperCase();
});
```

### Python 同步问题

```python
# 同步代码 - 假设每个请求需要1秒
import requests

# 3个请求需要3秒（串行）
r1 = requests.get(url1)  # 1秒
r2 = requests.get(url2)  # 1秒
r3 = requests.get(url3)  # 1秒
# 总计: 3秒
```

```python
# 异步代码 - 3个请求同时进行
import aiohttp

async def fetch_all():
    async with aiohttp.ClientSession() as session:
        # 3个请求同时发出，总计1秒
        task1 = fetch(session, url1)
        task2 = fetch(session, url2)
        task3 = fetch(session, url3)
        results = await asyncio.gather(task1, task2, task3)
    return results
# 总计: ~1秒
```

## 核心概念

```
同步模型                          异步模型
─────────────────────            ─────────────────────
                                
执行: ████████████              执行: ▓▓▓ (并发)
等待: ░░░░░░░░░░░              等待: ░░░░░░░░░░ (IO期间)
                                
总耗时: N × 单次时间             总耗时: max(各请求时间)
```

`★ Insight ─────────────────────────────────────`
- `async def` 定义协程（coroutine），类似懒执行的函数
- `await` 暂停当前协程，等待另一个协程完成
- asyncio 是 Python 的事件循环管理器，类似 Java 的 ForkJoinPool
`─────────────────────────────────────────────────`

## 基础语法

### 定义协程

```python
# 普通函数
def hello():
    return "Hello"

# 协程函数 - Java 没有直接对应，可理解为懒执行的 Supplier
async def hello_async():
    return "Hello"

# 调用协程需要事件循环
result = asyncio.run(hello_async())
```

### await 关键字

```python
import asyncio

async def task1():
    print("Task 1 开始")
    await asyncio.sleep(1)  # 模拟 IO 操作
    print("Task 1 完成")
    return "Result 1"

async def task2():
    print("Task 2 开始")
    await asyncio.sleep(0.5)  # 模拟 IO 操作
    print("Task 2 完成")
    return "Result 2"

async def main():
    # 串行执行 - 1.5秒
    r1 = await task1()
    r2 = await task2()

    # 并行执行 - ~1秒
    r1, r2 = await asyncio.gather(task1(), task2())

asyncio.run(main())
```

### AgentScope 源码示例

**文件**: `src/agentscope/agent/_agent_base.py`

```python
class AgentBase:
    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """接收消息，不生成回复 - 类似 Java 的 void 方法"""
        raise NotImplementedError(
            f"The observe function is not implemented in "
            f"{self.__class__.__name__} class."
        )

    async def reply(self, *args: Any, **kwargs: Any) -> Msg:
        """主要逻辑，生成回复 - async 方法"""
        raise NotImplementedError(
            f"The reply function is not implemented in "
            f"{self.__class__.__name__} class."
        )

    async def __call__(self, *args: Any, **kwargs: Any) -> Msg:
        """可调用对象 - 触发 reply 并处理异常"""
        self._reply_id = shortuuid.uuid()

        reply_msg: Msg | None = None
        try:
            self._reply_task = asyncio.current_task()
            reply_msg = await self.reply(*args, **kwargs)  # await 异步调用
        except asyncio.CancelledError:
            reply_msg = await self.handle_interrupt(*args, **kwargs)
        finally:
            if reply_msg:
                await self._broadcast_to_subscribers(reply_msg)
            self._reply_task = None

        return reply_msg
```

**Java 对照理解**：

```java
// 粗略的 Java 对应
public class AgentBase {
    public Future<Msg> call(Object... args) {
        String replyId = UUID.randomUUID().toString();
        try {
            return CompletableFuture.completedFuture(reply(args));
        } catch (CancellationException e) {
            return CompletableFuture.completedFuture(handleInterrupt(args));
        }
    }

    public abstract Msg reply(Object... args);
}
```

## Task 与 Future

```python
import asyncio

async def my_task():
    return "Done"

# 方法1: asyncio.create_task - 创建 Task（类似 Java Future）
async def method1():
    task = asyncio.create_task(my_task())
    result = await task  # 等待完成
    return result

# 方法2: asyncio.gather - 并行执行多个协程
async def method2():
    results = await asyncio.gather(
        my_task(),
        my_task(),
        my_task()
    )
    return results

# 方法3: asyncio.wait - 等待多个 Task 完成
async def method3():
    tasks = [asyncio.create_task(my_task()) for _ in range(3)]
    done, pending = await asyncio.wait(tasks)
    return [t.result() for t in done]
```

| Python | Java | 说明 |
|--------|------|------|
| `async def` | `CompletableFuture.supplyAsync()` | 异步执行单元 |
| `await` | `future.get()` | 等待结果 |
| `asyncio.create_task()` | `CompletableFuture.runAsync()` | 创建异步任务 |
| `asyncio.gather()` | `CompletableFuture.allOf()` | 等待多个任务 |
| `asyncio.sleep()` | `Thread.sleep()` | 异步睡眠 |

## async with（异步上下文管理器）

```python
# 同步上下文管理器（Java try-with-resources）
with open("file.txt") as f:
    content = f.read()

# 异步上下文管理器
async with aiohttp.ClientSession() as session:
    async with session.get(url) as response:
        content = await response.text()
```

**AgentScope 源码示例**

**文件**: `src/agentscope/pipeline/_msghub.py`

```python
class MsgHub:
    """MsgHub 使用异步上下文管理器管理订阅生命周期"""

    async def __aenter__(self) -> "MsgHub":
        """进入上下文 - 类似 Java 的 try 块入口"""
        self._reset_subscriber()
        if self.announcement is not None:
            await self.broadcast(msg=self.announcement)
        return self

    async def __aexit__(self, *args: Any, **kwargs: Any) -> None:
        """退出上下文 - 类似 Java 的 finally 块"""
        if self.enable_auto_broadcast:
            for agent in self.participants:
                agent.remove_subscribers(self.name)

    async def broadcast(self, msg: list[Msg] | Msg) -> None:
        """广播消息给所有订阅者"""
        for agent in self.participants:
            await agent.observe(msg)

# 使用方式
async def demo():
    async with MsgHub(participants=[agent1, agent2]) as hub:
        # 在这个块内，agent 的消息会自动广播
        await agent1()
        await agent2()
    # 退出时自动清理订阅
```

**Java 对照**：

```java
// Java 没有内置的异步 try-with-resources
// 但可以手动实现类似的 AutoCloseable

public class MsgHub implements AutoCloseable {
    @Override
    public void close() {
        // 清理订阅
        for (Agent agent : participants) {
            agent.removeSubscribers(name);
        }
    }
}

// 使用
try (MsgHub hub = new MsgHub(agents)) {
    agent1.call();
    agent2.call();
} // 自动调用 close()
```

## 常见错误

### 1. 忘记 await

```python
# ❌ 错误 - 协程不会自动执行
async def bad():
    result = some_async_function()  # 返回协程对象，不执行
    print(result)  # 打印 <coroutine object...>

# ✅ 正确
async def good():
    result = await some_async_function()  # 执行并获取结果
    print(result)
```

### 2. 在同步函数中调用 async

```python
# ❌ 错误 - 同步函数不能 await
def sync_bad():
    await asyncio.sleep(1)  # SyntaxError

# ✅ 正确 - 同步函数调用异步代码
def sync_good():
    asyncio.run(async_main())  # 在同步函数中启动事件循环

# 或者使用 asyncio.run() 在顶层
async def async_main():
    await asyncio.sleep(1)

asyncio.run(async_main())
```

## 练习题

1. **判断输出**：以下代码输出什么？
   ```python
   import asyncio

   async def main():
       print("A")
       await asyncio.sleep(0)
       print("B")

   asyncio.run(main())
   ```

2. **修复代码**：以下代码有什么问题？
   ```python
   async def fetch_data():
       return "data"

   result = fetch_data()
   print(result)
   ```

3. **并行执行**：使用 `asyncio.gather` 同时执行：
   ```python
   async def task(n):
       await asyncio.sleep(n)
       return n

   # 同时执行 task(1), task(2), task(3)
   ```

---

**答案**：

```python
# 1. 输出: A, B（sleep(0) 让出控制权，但很快恢复）
# 注意：即使 sleep(0) 也会切换，所以 B 会等 A 完成

# 2. 修复 - 需要 await
result = await fetch_data()
print(result)
# 或者使用 asyncio.run
result = asyncio.run(fetch_data())

# 3. 并行执行
async def main():
    results = await asyncio.gather(
        task(1),
        task(2),
        task(3)
    )
    print(results)  # [1, 2, 3]

asyncio.run(main())
# 总耗时: ~3秒（最长的那个），而不是 6秒
```
