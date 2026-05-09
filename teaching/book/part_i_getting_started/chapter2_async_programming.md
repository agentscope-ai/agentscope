# 第2章 Python异步编程

> **目标**：理解Python异步编程模型，掌握async/await在AgentScope中的应用

---

## 🎯 学习目标

学完之后，你能：
- 理解协程与异步编程的概念
- 掌握async/await语法
- 理解事件循环机制
- 在AgentScope中正确使用异步API

---

## 🚀 先跑起来

```python
import asyncio

async def agent_reply():
    # 模拟模型调用（异步操作）
    await asyncio.sleep(1)  # 等待1秒
    return "Agent的回复"

async def main():
    # 调用异步函数
    result = await agent_reply()
    print(f"收到: {result}")

# 运行事件循环
asyncio.run(main())
```

**输出**：
```
收到: Agent的回复
```

---

## 🔍 同步 vs 异步

### 同步代码（Java风格）

```python
# 同步代码 - 阻塞等待
def call_model():
    response = http.get("https://api.example.com/chat")
    return response
```

**执行流程**：
```
[请求1] → [等待] → [响应1] → [请求2] → [等待] → [响应2]
   总时间 = 响应1时间 + 响应2时间
```

### 异步代码（Python风格）

```python
# 异步代码 - 非阻塞
async def call_model():
    response = await http.get("https://api.example.com/chat")
    return response

async def call_multiple():
    results = await asyncio.gather(
        call_model(),  # 任务1
        call_model()   # 任务2
    )
    return results
```

**执行流程**：
```
[请求1] → [请求2] → [等待] → [响应1] + [响应2]
   总时间 = max(响应1时间, 响应2时间)
```

---

## 🔬 关键概念解析

### 1. 协程（Coroutine）

协程是"可暂停和恢复的函数"：

```python
async def my_agent():
    print("1. 开始处理")
    await asyncio.sleep(1)  # 暂停，可被其他协程使用
    print("2. 处理完成")
    return "结果"
```

**关键点**：
- `async def` 定义协程函数
- 调用协程函数返回协程对象，不执行函数体
- `await` 执行协程直到遇到下一个暂停点

### 2. 事件循环（Event Loop）

事件循环是异步代码的"调度器"：

```python
async def task1():
    print("Task 1 start")
    await asyncio.sleep(1)
    print("Task 1 end")

async def task2():
    print("Task 2 start")
    await asyncio.sleep(0.5)
    print("Task 2 end")

async def main():
    # 并发执行两个任务
    await asyncio.gather(task1(), task2())

asyncio.run(main())
```

**输出**：
```
Task 1 start
Task 2 start
Task 2 end    # 0.5秒后
Task 1 end    # 1秒后
```

### 3. await关键字

`await`有三重作用：

```python
async def example():
    # 1. 等待协程完成
    result = await async_function()
    
    # 2. 等待可等待对象（实现了__await__）
    await some_object
    
    # 3. 表达式可以继续执行
    value = await get_data() + await get_more_data()
```

### 4. 并发 vs 并行

**并发（Concurrency）**：交替执行，看起来像同时
```python
async def fetch(url):
    return await http_get(url)

async def main():
    # 并发：I/O等待时切换
    results = await asyncio.gather(*[fetch(u) for u in urls])
```

**并行（Parallelism）**：真正同时执行
```python
import concurrent.futures

def fetch_sync(url):
    return requests.get(url)

with concurrent.futures.ThreadPoolExecutor() as pool:
    results = list(pool.map(fetch_sync, urls))
```

---

## 💡 Java开发者注意

### Python async vs Java CompletableFuture

**Python**：
```python
async def get_user(user_id):
    user = await db.fetch("SELECT * FROM users WHERE id = ?", user_id)
    return user
```

**Java**：
```java
CompletableFuture<User> getUser(String userId) {
    return CompletableFuture.supplyAsync(() ->
        db.query("SELECT * FROM users WHERE id = ?", userId)
    );
}
```

### Python async vs Java线程

**Python（轻量级）**：
```python
# 10万个协程可以轻松运行
async def handle_client(socket):
    data = await socket.recv()
    await socket.send(process(data))

async def main():
    server = await asyncio.start_server(handle_client, '0.0.0.0', 8080)
```

**Java（重量级）**：
```java
// 每个客户端需要一个线程
void handleClient(Socket socket) {
    var data = socket.read();
    socket.write(process(data));
}
// 需要线程池限制线程数量
```

---

## 🔬 AgentScope中的异步

### MsgHub是异步的

```python
# MsgHub使用async with管理生命周期
async with MsgHub(participants=[agent1, agent2]) as hub:
    await hub.broadcast(Msg(name="system", content="开始协作"))
# 退出时自动清理
```

### Agent的回复是异步的

```python
# ReActAgent的reply方法是async
async def chat():
    agent = ReActAgent(...)
    response = await agent(Msg(name="user", content="你好"))
    print(response.content)
```

### 常见错误

**错误1：忘记await**
```python
# 错误：协程不会自动执行
hub = MsgHub(participants=[agent1, agent2])
hub.broadcast(msg)  # 不会执行！

# 正确：
await hub.broadcast(msg)
```

**错误2：在同步代码中调用异步函数**
```python
# 错误
def sync_function():
    result = await async_function()  # SyntaxError

# 正确：在async函数中调用
async def async_function2():
    result = await async_function()
```

**错误3：混用同步和异步HTTP库**
```python
# 错误：同步库会阻塞事件循环
import requests  # 同步库

async def fetch_all():
    results = [requests.get(url) for url in urls]  # 阻塞！

# 正确：使用异步库
import aiohttp  # 异步库

async def fetch_all():
    async with aiohttp.ClientSession() as session:
        tasks = [session.get(url) for url in urls]
        results = await asyncio.gather(*tasks)
```

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **为什么AgentScope大量使用async/await？**
   - Agent需要等待模型响应（I/O密集）
   - 异步允许同时运行多个Agent
   - 比线程更轻量

2. **`await`会阻塞线程吗？**
   - 不会。它会让出控制权给事件循环
   - 事件循环可以执行其他协程
   - 直到await的操作完成，协程才恢复

3. **什么时候用asyncio.gather？**
   - 多个独立的异步任务需要并发执行
   - 收集所有结果
   - 类似Java的`CompletableFuture.allOf()`

</details>

---

★ **Insight** ─────────────────────────────────────
- **async/await** = Python的协程语法，非阻塞I/O
- **事件循环** = 协程的调度器，交替执行
- **await让出控制权** = 协程可暂停，其他协程执行
- **协程 vs 线程** = 协程是"合作式"多任务，线程是"抢占式"
─────────────────────────────────────────────────
