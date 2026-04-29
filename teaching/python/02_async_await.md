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

**文件**: `src/agentscope/agent/_agent_base.py:185-467`

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
public abstract class AgentBase {
    private String replyId;

    public CompletableFuture<Msg> call(Object... args) {
        this.replyId = UUID.randomUUID().toString();
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
| `asyncio.sleep()` | `CompletableFuture.delayedExecutor()` | 非阻塞睡眠（非 `Thread.sleep`） |

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

### AgentScope 源码示例

**文件**: `src/agentscope/pipeline/_msghub.py:73-87`

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

## 异步迭代器与生成器

```python
# 异步迭代器
class AsyncCounter:
    def __init__(self, max: int) -> None:
        self.max = max
        self.current = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.current < self.max:
            await asyncio.sleep(0.1)
            self.current += 1
            return self.current
        raise StopAsyncIteration

# 异步生成器
async def async_gen(n: int):
    for i in range(n):
        await asyncio.sleep(0.1)
        yield i

# 使用
async def main():
    async for i in async_gen(3):
        print(i)
```

**Java 对照**：

```java
// Java 没有直接的异步迭代器对应
// 但可以使用 Flow (Reactive Streams) 实现类似功能
public class AsyncCounter implements Publisher<Integer> {
    private int max;
    private int current = 0;

    @Override
    public void subscribe(Subscriber<? super Integer> subscriber) {
        // 实现订阅逻辑
    }
}
```

## 并发控制

### 信号量（Semaphore）

```python
import asyncio

# 控制并发数量
semaphore = asyncio.Semaphore(2)

async def limited_task(n: int):
    async with semaphore:
        print(f"Task {n} started")
        await asyncio.sleep(1)
        print(f"Task {n} finished")
        return n

async def main():
    # 最多同时运行2个任务
    results = await asyncio.gather(
        limited_task(1),
        limited_task(2),
        limited_task(3),
        limited_task(4),
    )
    # 总耗时: ~2秒（2+2，而不是 4）

asyncio.run(main())
```

### 锁（Lock）

```python
import asyncio

# 异步锁 - 保护共享资源
lock = asyncio.Lock()
counter = 0

async def increment():
    global counter
    async with lock:
        # 同一时间只有一个协程能进入
        old = counter
        await asyncio.sleep(0.1)  # 模拟计算
        counter = old + 1
        return counter

async def main():
    results = await asyncio.gather(
        increment(),
        increment(),
        increment(),
    )
    # counter 最终值为 3（无锁则可能为 1 或 2）

asyncio.run(main())
```

**Java 对照**：

```java
// Java Lock（简化版，省略 import 和 class 外壳）
Lock lock = new ReentrantLock();
int counter = 0;

public int increment() throws InterruptedException {
    lock.lock();
    try {
        int old = counter;
        Thread.sleep(100); // 同步睡眠
        counter = old + 1;
        return counter;
    } finally {
        lock.unlock();
    }
}
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

### 3. 异步函数中使用阻塞调用

```python
# ❌ 错误 - 阻塞调用会阻塞整个事件循环
async def bad_async():
    time.sleep(10)  # 阻塞！整个事件循环卡住
    # requests.get()  # 同样阻塞！

# ✅ 正确 - 使用异步兼容的库
async def good_async():
    await asyncio.sleep(10)  # 非阻塞
    async with aiohttp.ClientSession() as session:
        await session.get(url)  # 异步 HTTP
```

**Java 对照**：

```java
// Java 同样需要注意：阻塞调用会阻塞线程
// CompletableFuture.runAsync() 使用的线程池可能被阻塞
// 解决方案：使用非阻塞 IO 或专用线程池
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

4. **并发控制**：使用信号量限制最多同时执行2个任务：
   ```python
   async def worker(n):
       await asyncio.sleep(n)
       return f"Task {n} done"

   # 4个任务，但最多同时2个
   ```

5. **异步上下文管理器**：写出以下代码的输出：
   ```python
   import asyncio

   class Acounter:
       def __init__(self):
           self.count = 0

       async def __aenter__(self):
           self.count += 1
           return self

       async def __aexit__(self, *args):
           self.count -= 1

   async def main():
       counter = Acounter()
       async with counter:
           print(f"Inside: {counter.count}")  # 输出?
       print(f"Outside: {counter.count}")  # 输出?

   asyncio.run(main())
   ```

---

**答案**：

```python
# 1. 输出: A, B
# asyncio.sleep(0) 让出一次控制权给事件循环，但由于没有其他协程，
# 立即恢复执行，所以顺序就是 A 然后 B

# 2. 修复 - 需要用 await 或 asyncio.run 获取结果
# 方式1：在异步上下文中使用 await
async def main():
    result = await fetch_data()
    print(result)

asyncio.run(main())

# 方式2：在同步上下文中使用 asyncio.run
result = asyncio.run(fetch_data())
print(result)  # "data"

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

# 4. 并发控制
async def main():
    semaphore = asyncio.Semaphore(2)

    async def limited_task(n):
        async with semaphore:
            result = await worker(n)
            return result

    results = await asyncio.gather(
        limited_task(1),
        limited_task(2),
        limited_task(3),
        limited_task(4),
    )
    # 总耗时: ~6秒
    # 分析：task(1)和task(2)先开始（semaphore=2）
    # t=1s: task(1)完成，task(3)开始
    # t=2s: task(2)完成，task(4)开始
    # t=4s: task(3)完成（从t=1开始sleep3秒）
    # t=6s: task(4)完成（从t=2开始sleep4秒）

# 5. 输出:
# Inside: 1
# Outside: 0
# __aexit__ 在块退出时自动调用，count 减回 0
```
