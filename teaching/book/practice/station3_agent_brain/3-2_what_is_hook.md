# 3-2 Hook是什么

> **目标**：理解Hook拦截器模式以及如何自定义Hook

---

## 🎯 这一章的目标

学完之后，你能：
- 理解Hook的6种类型
- 创建自定义Hook函数
- 使用Hook做日志、监控、拦截

---

## 🚀 先跑起来

```python showLineNumbers

# 定义Hook回调函数（普通函数，不是类）
def my_pre_reply_hook(agent, message):
    """回复前拦截 - 打印日志"""
    print(f"Agent {agent.name} 即将回复: {message.content[:50]}...")
    return message  # 返回原始消息，或者修改后的消息

def my_post_reply_hook(agent, response, message):
    """回复后拦截 - 记录度量"""
    print(f"Agent {agent.name} 已回复: {message.content[:50]}...")
    return response

# 创建Agent后，通过register_instance_hook注册Hook
agent = ReActAgent(
    name="MyAgent",
    model=model,
    sys_prompt="你是一个有帮助的助手",
    formatter=formatter,
)
agent.register_instance_hook("pre_reply", "my_pre_hook", my_pre_reply_hook)
agent.register_instance_hook("post_reply", "my_post_hook", my_post_reply_hook)
```

---

## 🔍 Hook类型

**AgentBase有6种Hook类型**：

```
┌─────────────────────────────────────────────────────────────┐
│                    AgentBase Hook类型                      │
│                                                             │
│   pre_reply ──► reply() ──► post_reply                    │
│        │                                        │           │
│        ▼                                        ▼           │
│   pre_observe ◄─────── observe() ◄─────── post_observe     │
│                                                             │
│   pre_print ──► print() ──► post_print                    │
└─────────────────────────────────────────────────────────────┘
```

**ReActAgentBase额外增加4种Hook类型**（共10种）：

```
┌─────────────────────────────────────────────────────────────┐
│               ReActAgentBase 额外Hook类型                  │
│                                                             │
│   pre_reasoning ──► _reasoning() ──► post_reasoning        │
│   pre_acting ──► _acting() ──► post_acting                 │
└─────────────────────────────────────────────────────────────┘
```

| Hook类型 | 触发时机 | 典型用途 |
|----------|----------|----------|
| `pre_reply` | reply()前 | 日志、修改消息 |
| `post_reply` | reply()后 | 监控、统计 |
| `pre_observe` | observe()前 | 过滤结果 |
| `post_observe` | observe()后 | 记录日志 |
| `pre_print` | print()前 | 格式化输出 |
| `post_print` | print()后 | 记录日志 |
| `pre_reasoning` | 推理前 | 记录输入 |
| `post_reasoning` | 推理后 | 记录思考过程 |
| `pre_acting` | 行动前 | 记录工具调用 |
| `post_acting` | 行动后 | 记录工具结果 |

---

## 🔍 追踪Hook的执行

```mermaid
sequenceDiagram
    participant User as 用户
    participant PreH as pre_reply Hook
    participant Agent as Agent
    participant Model as Model
    participant PostH as post_reply Hook
    participant User2 as 用户

    User->>PreH: 进入
    PreH->>Agent: pre_reply处理
    Agent->>Model: 调用Model
    Model-->>Agent: 返回回复
    Agent->>PostH: post_reply处理
    PostH-->>User2: 最终输出
```

---

## 🔬 关键代码段解析

### 代码段1：Hook的拦截原理

```python showLineNumbers
# Hook签名：pre_reply返回修改后的kwargs，post_reply返回修改后的response
def my_pre_reply_hook(self, kwargs):
    """回复前拦截 - kwargs包含传给reply()的参数"""
    msg = kwargs.get("msg")  # 获取消息
    print(f"Agent {self.name} 即将回复: {msg.content[:50]}...")
    return kwargs  # 返回修改后的kwargs，或返回None跳过

def my_post_reply_hook(self, kwargs, response):
    """回复后拦截 - kwargs是原始参数，response是回复消息"""
    print(f"Agent {self.name} 已回复: {response.content[:50]}...")
    return response  # 返回修改后的response，或返回None使用原值
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| Hook为什么能拦截？ | Agent执行时会调用注册的回调函数 |
| `return kwargs`有什么用？ | 可以返回修改后的参数字典 |
| Hook和Decorator模式有什么关系？ | Hook本质上是AOP拦截器，类似Decorator但不改变原对象 |
| kwargs包含什么？ | 传给reply()的所有参数，如`msg`、`session_id`等 |

```
┌─────────────────────────────────────────────────────────────┐
│                 Hook拦截原理                               │
│                                                             │
│   用户请求                                                  │
│       │                                                    │
│       ▼                                                    │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  pre_reply Hook                                     │  │
│   │  - 可以修改消息                                     │  │
│   │  - 可以记录日志                                     │  │
│   │  - 返回修改后的消息                                 │  │
│   └─────────────────────────────────────────────────────┘  │
│       │                                                    │
│       ▼                                                    │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  Agent 核心逻辑（不改变）                            │  │
│   └─────────────────────────────────────────────────────┘  │
│       │                                                    │
│       ▼                                                    │
│   ┌─────────────────────────────────────────────────────┐  │
│   │  post_reply Hook                                    │  │
│   │  - 可以记录响应                                     │  │
│   │  - 可以监控性能                                     │  │
│   │  - 可以修改返回（可选）                             │  │
│   └─────────────────────────────────────────────────────┘  │
│       │                                                    │
│       ▼                                                    │
│   返回给用户                                              │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：Hook是**观察者模式**的应用。在不改变核心逻辑的情况下，插入额外的处理逻辑。

---

### 代码段2：Hook的使用方式

```python showLineNumbers
# 创建Agent后注册Hook
agent = ReActAgent(
    name="MyAgent",
    model=model,
    sys_prompt="你是一个有帮助的助手",
    formatter=formatter,
)

# 定义Hook回调函数
def my_pre_reply_hook(agent, message):
    print(f"{agent.name} 收到消息")
    return message

def my_post_reply_hook(agent, response, message):
    print(f"{agent.name} 回复完成")
    return response

# 注册实例级Hook（仅对当前Agent实例生效）
agent.register_instance_hook("pre_reply", "my_pre_hook", my_pre_reply_hook)
agent.register_instance_hook("post_reply", "my_post_hook", my_post_reply_hook)

# 或者注册类级别Hook（对所有该类的Agent实例生效）
# ReActAgent.register_class_hook("pre_reply", "global_hook", global_pre_hook)
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| 如何注册Hook？ | 使用`register_instance_hook()`方法 |
| 实例级和类级Hook区别？ | 实例级只对当前Agent，类级对所有该类Agent |
| 可以同时用多个Hook吗？ | 可以，按注册顺序执行 |

```
┌─────────────────────────────────────────────────────────────┐
│                 多个Hook的执行顺序                          │
│                                                             │
│   pre_reply_Hook1 ──► pre_reply_Hook2 ──► Agent ──►       │
│       │                                                    │
│       ▼                                                    │
│   post_reply_Hook1 ◄── post_reply_Hook2 ◄──             │
│                                                             │
│   按注册顺序执行（先进先出）                              │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：多个Hook按注册顺序执行，每个Hook都可以在`pre`和`post`阶段进行处理。

---

### 代码段3：Hook的典型应用场景

```python showLineNumbers
# 场景1：日志记录
def logging_pre_reply(agent, message):
    logger.info(f"{agent.name} 收到消息: {message.content[:100]}")
    return message

def logging_post_reply(agent, response, message):
    logger.info(f"{agent.name} 回复: {response.content[:100]}")
    return response

# 场景2：敏感词过滤
def content_filter_pre_reply(agent, message):
    # 过滤敏感词
    message.content = filter_sensitive_words(message.content)
    return message

# 场景3：性能监控
def metrics_pre_reply(agent, message):
    agent._hook_start_time = time.time()
    return message

def metrics_post_reply(agent, response, message):
    elapsed = time.time() - agent._hook_start_time
    metrics.record(f"{agent.name}.latency", elapsed)
    return response
```

**思路说明**：

| 场景 | Hook类型 | 用途 |
|------|----------|------|
| 日志记录 | pre_reply + post_reply | 记录所有交互 |
| 敏感词过滤 | pre_reply | 在发送前过滤内容 |
| 性能监控 | pre_reply + post_reply | 测量延迟 |
| 访问控制 | pre_reply | 拦截未授权请求 |

---

## 💡 Java开发者注意

Hook类似Java的**AOP拦截器**或**Servlet Filter**：

```java
// Java Servlet Filter
public class MyFilter implements Filter {
    @Override
    public void doFilter(ServletRequest req, ServletResponse res, FilterChain chain) {
        // pre_process
        preProcess(req);

        chain.doFilter(req, res);  // 执行实际逻辑

        // post_process
        postProcess(res);
    }
}

// Python Hook（普通函数）
def my_pre_reply_hook(agent, message):
    pre_process(message)
    return message

def my_post_reply_hook(agent, response, message):
    post_process(response)
    return response
```

| Hook | Java AOP | 说明 |
|------|----------|------|
| pre_reply | @Before | 方法执行前拦截 |
| post_reply | @AfterReturning | 方法执行后拦截 |
| observe | @After | 无论成功失败都执行 |
| pre_print | 拦截打印 | 控制输出格式 |

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **Hook和Tool有什么区别？**
   - Hook：拦截处理流程，做日志、监控
   - Tool：被Agent调用，完成具体任务
   - Hook不改变Agent的决策，只是"观察"

2. **Hook能修改Agent的回复吗？**
   - pre_reply可以返回修改后的消息
   - post_reply可以记录但不能修改已发送的

3. **什么场景下用Hook？**
   - 日志记录
   - 性能监控
   - 敏感词过滤
   - 输出格式化

</details>

---

★ **Insight** ─────────────────────────────────────
- **Hook是拦截器**：在不改变核心逻辑的情况下，增加处理
- **6种类型**覆盖了Agent处理的各个阶段
- Hook是**普通函数**，不是类
- 类似Java的AOP拦截器或Servlet Filter
─────────────────────────────────────────────────
