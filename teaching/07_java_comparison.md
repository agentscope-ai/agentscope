# 第七章：Java 开发者视角

## 学习目标

> 学完本节，你将能够：
> - [L2 理解] 将 AgentScope 核心概念映射到 Java/Spring 等价物
> - [L3 应用] 使用正确的 Python/AgentScope 语法替代 Java 设计模式
> - [L4 分析] 比较 Java 和 Python 在依赖注入、并发、异常处理上的差异

**预计时间**：15 分钟
**先修要求**：Java/Spring 开发经验，已完成 [第四章：核心概念](04_core_concepts.md)

## 7.1 概念映射表

作为 Java 开发者，你可以将 AgentScope 的概念映射到你熟悉的 Java 技术：

```
┌─────────────────────────────────────────────────────────────────┐
│                    概念映射表                                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   AgentScope (Python)     │        Java (Spring)                │
│   ─────────────────────────────────────────────────────────     │
│   agentscope.init()       │  @SpringBootApplication + main()     │
│   ReActAgent              │  @Service (带 AI 能力)               │
│   DeepResearchAgent       │  @Service + RAG Pipeline             │
│   Model (*ChatModel)     │  RestTemplate / WebClient            │
│   Tool (Toolkit.register  │  @Bean / Util类                      │
│    _tool_function())      │                                      │
│   Memory                  │  Cache / Redis / Mem0                │
│   MsgHub                  │  EventBus / Kafka                    │
│   Pipeline                │  Workflow Engine / Camunda           │
│   sys_prompt              │  application.yml 配置                 │
│   toolkit=Toolkit(...)    │  @Autowired Toolkit                 │
│   memory=...              │  @Cacheable / RedisTemplate          │
│   speech={...}            │  WebFlux + TTS API                  │
│   SequentialPipeline     │  Sequential Workflow                │
│   FanoutPipeline         │  Parallel Fork/Join                 │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 7.2 代码模式对比

### 依赖注入

```java
// Java: 构造器注入
@Service
public class OrderService {
    private final OrderRepository repository;
    private final Cache cache;

    public OrderService(OrderRepository repository, Cache cache) {
        this.repository = repository;
        this.cache = cache;
    }
}
```

```python showLineNumbers
# Python/AgentScope: 直接传参
class MyAgent:
    def __init__(self, model, memory):
        self.model = model
        self.memory = memory

# 创建时注入
agent = MyAgent(model=my_model, memory=my_memory)
```

### 注解 vs 装饰器

```java
// Java: 注解
@Service
@Transactional(readOnly = true)
public class OrderService {

    @Cacheable("orders")
    public Order findById(Long id) {
        return repository.findById(id);
    }

    @Async
    public void processOrder(Order order) {
        // 异步处理
    }
}
```

```python showLineNumbers
# Python: AgentScope 工具注册
from agentscope.tool import Toolkit

toolkit = Toolkit()

# 注册工具函数（类似 @Bean 方法注册）
toolkit.register_tool_function(find_order_by_id)
toolkit.register_tool_function(process_order)
```

> **注意**：Python 装饰器（如 `@property`、`@abstractmethod`）用于通用目的，AgentScope 的工具注册使用 `Toolkit` 类而非装饰器。

### 配置管理

```java
// Java: application.yml
// spring:
//   application:
//     name: my-agent
//   data:
//     redis:
//       host: localhost
//       port: 6379
// openai:
//   api-key: ${OPENAI_API_KEY}

@Service
public class MyService {
    @Value("${spring.application.name}")
    private String appName;
}
```

```python showLineNumbers
# Python: agentscope.init() 或环境变量
import agentscope
import os

# 方式一: 环境变量（推荐）
os.environ["OPENAI_API_KEY"] = "sk-xxxxx"

# 方式二: init() 参数
agentscope.init(
    project="my-agent",  # 注意: 参数名是 project，不是 project_name
)
# api_key 参数已移除，请使用环境变量
```

## 7.3 设计模式对应

| GoF 设计模式 | Java 实现 | AgentScope/Python 实现 |
|-------------|----------|----------------------|
| **工厂模式** | FactoryBean | 直接实例化（OpenAIChatModel(...)） |
| **单例模式** | @Scope("singleton") | Python 模块级单例 |
| **策略模式** | Strategy Interface | 不同 Model 实现 (*ChatModel) |
| **模板方法** | Abstract Class | AgentBase 抽象类 |
| **装饰器模式** | Decorator | Toolkit.register_tool_function() |
| **观察者模式** | Observer / EventListener | callbacks 参数 |
| **中介者模式** | Mediator / EventBus | MsgHub 消息中心 |
| **管道模式** | Pipeline / Chain | Sequential/FanoutPipeline |

### 工厂模式示例

```java
// Java: 工厂 + 反射
@Configuration
public class ModelFactory {
    @Bean
    public Model createModel(@Value("${model.type}") String type) {
        return switch (type) {
            case "openai" -> new OpenAIModel();
            case "anthropic" -> new AnthropicModel();
            default -> throw new IllegalArgumentException();
        };
    }
}
```

```python showLineNumbers
# Python: 直接实例化（无注册表模式）
from agentscope.model import OpenAIChatModel, AnthropicChatModel

# 根据需要选择模型，直接创建实例
model = OpenAIChatModel(model_name="gpt-4o")

# 或使用 Anthropic
model = AnthropicChatModel(model_name="claude-sonnet-4-20250514")

# 模型类名统一使用 *ChatModel 后缀
# OpenAIChatModel, AnthropicChatModel, DashScopeChatModel
```

## 7.4 生命周期对比

```
┌─────────────────────────────────────────────────────────────────┐
│                        生命周期对比                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   Spring Boot               │        AgentScope                 │
│   ─────────────────────     │    ─────────────────────          │
│                             │                                   │
│   main()                    │    agentscope.init()             │
│       │                     │        │                          │
│       ▼                     │        ▼                          │
│   @SpringBootApplication     │    init(project=...)             │
│       │                     │        │                          │
│       ▼                     │        ▼                          │
│   Bean 创建                 │    Agent 创建                     │
│   @PostConstruct            │    __init__                      │
│       │                     │        │                          │
│       ▼                     │        ▼                          │
│   请求处理                   │    agent("message")              │
│   @RequestMapping           │    reply()                        │
│       │                     │        │                          │
│       ▼                     │        ▼                          │
│   @PreDestroy               │    (无自动销毁)                    │
│   容器关闭                   │    手动清理                       │
│                             │                                   │
└─────────────────────────────────────────────────────────────────┘
```

## 7.5 异常处理对比

```java
// Java: 检查型异常
public class OrderService {

    public Order createOrder(OrderReq req) throws OrderException {
        try {
            validate(req);
            return repository.save(req);
        } catch (ValidationException e) {
            throw new OrderException("Validation failed", e);
        }
    }

    // 调用方必须处理
    public void caller() {
        try {
            service.createOrder(req);
        } catch (OrderException e) {
            log.error("Failed to create order", e);
        }
    }
}
```

```python
# Python: 非检查型异常
class OrderService:
    def create_order(self, req: OrderReq) -> Order:
        try:
            self._validate(req)
            return self.repository.save(req)
        except ValidationException as e:
            raise OrderException(f"Validation failed: {e}")

# 调用方可以选择处理
def caller():
    try:
        service.create_order(req)
    except OrderException as e:
        logger.error(f"Failed to create order: {e}")
```

## 7.6 并发模型对比

```java
// Java: ExecutorService / @Async
@Service
public class AsyncService {

    @Async
    public CompletableFuture<String> processAsync(String input) {
        // 异步执行
        return CompletableFuture.completedFuture(result);
    }

    // 或使用 ExecutorService
    @Bean
    public Executor taskExecutor() {
        return Executors.newFixedThreadPool(10);
    }
}
```

```python showLineNumbers
# Python: asyncio (类似 Project Reactor / WebFlux)
import asyncio

async def process_async(input: str) -> str:
    # 异步执行
    await asyncio.sleep(1)  # 模拟 IO 操作
    return result

# 运行
result = asyncio.run(process_async("input"))

# AgentScope: FanoutPipeline 实现并行执行
from agentscope.pipeline import FanoutPipeline

fanout = FanoutPipeline(agents=[agent1, agent2, agent3])
results = await fanout("并行任务")
```

## 7.7 测试对比

```java
// Java: JUnit 5 + Mockito
@ExtendWith(MockitoExtension.class)
class OrderServiceTest {

    @Mock
    private OrderRepository repository;

    @InjectMocks
    private OrderService service;

    @Test
    void testCreateOrder() {
        when(repository.save(any())).thenReturn(new Order());

        Order result = service.createOrder(new OrderReq());

        assertNotNull(result);
        verify(repository).save(any());
    }
}
```

```python showLineNumbers
# Python: pytest + unittest.mock
import pytest
from unittest.mock import Mock

class TestOrderService:
    @pytest.fixture
    def mock_repository(self):
        return Mock()

    @pytest.fixture
    def service(self, mock_repository):
        return OrderService(repository=mock_repository)

    def test_create_order(self, service, mock_repository):
        mock_repository.save.return_value = Order()

        result = service.create_order(OrderReq())

        assert result is not None
        mock_repository.save.assert_called_once()
```

## 7.8 日志对比

```java
// Java: SLF4J + Logback
@Slf4j
@Service
public class OrderService {

    public void process() {
        log.info("Processing order: {}", orderId);
        log.debug("Order details: {}", order);

        try {
            // 处理逻辑
        } catch (Exception e) {
            log.error("Failed to process order", e);
        }
    }
}

// logback.xml
// <configuration>
//   <appender name="STDOUT" class="ch.qos.logback.core.ConsoleAppender">
//     <encoder>
//       <pattern>%d{HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n</pattern>
//     </encoder>
//   </appender>
// </configuration>
```

```python showLineNumbers
# Python: logging (标准库)
import logging

logger = logging.getLogger(__name__)

def process():
    logger.info("Processing order: %s", order_id)
    logger.debug("Order details: %s", order)

    try:
        # 处理逻辑
    except Exception as e:
        logger.error("Failed to process order", exc_info=True)

# AgentScope 自动配置日志
import agentscope
agentscope.init(project="my-project")
# 日志级别通过 logging_level 参数设置
```

## 7.9 微服务类比

如果你做过 Spring Cloud 微服务，这些概念对应关系会很有用：

| 微服务概念 | Spring Cloud | AgentScope |
|-----------|--------------|------------|
| **服务注册发现** | Eureka / Nacos | AgentScope Studio |
| **配置中心** | Config Server | `agentscope.init()` 参数 |
| **熔断器** | Resilience4j | AgentScope 内置重试 |
| **链路追踪** | Sleuth / Zipkin | OpenTelemetry |
| **服务间通信** | Feign / RestTemplate | MsgHub / A2A |
| **消息队列** | Kafka / RabbitMQ | MsgHub |
| **可观测性** | Micrometer | AgentScope Studio + Tracing |
| **模型微调** | - | Tuner (零代码修改) |

## 7.10 学习建议

### Java 开发者学习 Python/AgentScope 的建议

1. **先理解概念，再看语法**
   - Agent、Model、Tool 的关系比语法更重要
   - 设计模式是相通的，只是实现不同

2. **善用 IDE**
   - IntelliJ IDEA 支持 Python
   - PyCharm 有更好的 Python 支持

3. **习惯 Python 的灵活性**
   - 没有强类型检查 → 用 type hints 弥补
   - 缩进敏感 → 注意代码格式
   - 动态类型 → 用 pytest 确保类型正确

4. **参考源码**
   - AgentScope 源码质量高，可读性好
   - 从 `src/agentscope/agent/_react_agent.py` 开始

5. **注意 API 差异**
   - `init()` 参数: `project` 而非 `project_name`
   - `api_key` 通过环境变量设置，非参数传递
   - 模型类名统一使用 `*ChatModel` 后缀
   - 工具导入: `from agentscope.tool import xxx`

## 7.11 AgentScope 2.0 展望

AgentScope 2.0 正在开发中，将带来重大架构升级。当前 v1.0 仍为主力版本。

**2.0 核心方向**：
- Voice Agent 全链路支持
- Real-time Multimodal Models
- 实时语音交互

**关注渠道**：
- GitHub: https://github.com/agentscope-ai/agentscope
- 官方文档: https://doc.agentscope.io/
- Discord: https://discord.gg/eYMpfnkG8h

## 总结

- AgentScope 使用直接实例化而非工厂模式：`OpenAIChatModel(model_name=...)`
- 工具通过 `Toolkit.register_tool_function()` 注册，而非装饰器
- Python 的 `async/await` 对应 Java 的 `CompletableFuture`
- AgentScope 的 `MsgHub` 对应 Spring 的 `EventBus`/`Kafka`

## 练习题

### 练习 7.1: 概念映射验证 [基础]

**题目**：
请将以下 Java/Spring 概念翻译成 AgentScope 中的对应实现：

| Java/Spring 概念 | AgentScope 实现 |
|------------------|----------------|
| `@Service` | ? |
| `@Autowired` 依赖注入 | ? |
| `@Cacheable` | ? |
| `@Async` 异步方法 | ? |
| `ApplicationEventPublisher` | ? |
| `CompletableFuture<T>` | ? |

**验证方式**：
对照文档中的概念映射表进行检查。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

| Java/Spring 概念 | AgentScope 实现 |
|------------------|----------------|
| `@Service` | `ReActAgent`（带 LLM 能力的 Service） |
| `@Autowired` 依赖注入 | 直接传参：`def __init__(self, model): self.model = model` |
| `@Cacheable` | `Memory`（InMemory / Redis / SQLAlchemy） |
| `@Async` 异步方法 | `async/await`：`async def xxx(): await ...` |
| `ApplicationEventPublisher` | `MsgHub`（消息订阅广播） |
| `CompletableFuture<T>` | `asyncio.Future` 或 `await`（异步调用） |

**关键映射关系**：
- AgentScope 使用**直接实例化**，而非 Spring 的依赖注入容器
- Python 的 `async def` + `await` 对应 Java 的 `@Async` + `CompletableFuture`
- `MsgHub` 的订阅-发布模式类似 Spring 的 `ApplicationEventPublisher`
</details>

---

### 练习 7.2: 异常处理对比 [中级]

**题目**：
请将以下 Java 异常处理代码翻译成 Python 代码，并说明两者的关键区别：

```java
// Java 代码
public class OrderService {

    public Order createOrder(OrderReq req) throws OrderException {
        try {
            validate(req);
            return repository.save(req);
        } catch (ValidationException e) {
            throw new OrderException("Validation failed", e);
        } finally {
            cleanup();
        }
    }
}
```

**验证方式**：
对比代码结构和异常处理模式。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**Python 翻译**：

```python showLineNumbers
# Python 代码
class OrderService:
    def create_order(self, req: OrderReq) -> Order:
        try:
            self._validate(req)
            return self._repository.save(req)
        except ValidationException as e:
            raise OrderException(f"Validation failed: {e}")
        finally:
            self._cleanup()
```

**关键区别**：

| 方面 | Java | Python |
|------|------|--------|
| 异常声明 | `throws` 关键字在方法签名中 | 无需声明，异常是隐式的 |
| 检查型异常 | 有（`throws` 声明的异常） | 无（所有异常都是非检查型） |
| 多异常捕获 | `catch (A e \| B e)` (Java 7+) | `except (A, B) as e:` |
| 资源清理 | `try-with-resources` | `with` 语句（上下文管理器） |
| 自定义异常 | `extends Exception` | `class MyError(Exception): pass` |

**Python 的优势**：
- 无需在方法签名声明异常，代码更简洁
- 异常处理更灵活

**Java 的优势**：
- 检查型异常强制调用方处理错误
- 编译器能帮助发现未处理的异常

**Python with 语句示例**（替代 try-finally）：
```python
# 推荐写法
with open("file.txt") as f:
    content = f.read()
# 自动调用 f.close()
```
</details>

---

### 练习 7.3: 异步编程对比 [中级]

**题目**：
某 Java 服务使用 `@Async` 实现异步调用，请将其翻译成 AgentScope/Python 代码：

**Java 代码（Spring Boot）**：
```java
@Service
public class AsyncService {

    @Async
    public CompletableFuture<String> processAsync(String input) {
        // 异步处理
        return CompletableFuture.completedFuture(process(input));
    }
}

// 调用
asyncService.processAsync("data").thenAccept(result -> {
    System.out.println(result);
});
```

**验证方式**：
检查异步调用模式是否正确对应。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**Python/AgentScope 代码**：

```python showLineNumbers
import asyncio

class AsyncService:
    async def process_async(self, input: str) -> str:
        """异步处理"""
        # 模拟异步 IO 操作
        await asyncio.sleep(0.1)  # 代替实际的异步调用
        return self._process(input)

    def _process(self, input: str) -> str:
        """实际处理逻辑"""
        return f"processed: {input}"

# 调用方式 1：await（推荐）
async def main():
    service = AsyncService()
    result = await service.process_async("data")
    print(result)

# 调用方式 2：gather 并行执行多个异步任务
async def parallel_example():
    service = AsyncService()
    results = await asyncio.gather(
        service.process_async("data1"),
        service.process_async("data2"),
        service.process_async("data3"),
    )
    print(results)  # ['processed: data1', 'processed: data2', 'processed: data3']

# 运行
asyncio.run(main())
```

**关键对比**：

| Java | Python/AgentScope |
|------|-------------------|
| `@Async` 注解 | `async def` 关键字 |
| `CompletableFuture<T>` | `asyncio.Future` 或直接 `await` |
| `.thenAccept()` | `await` 或 `asyncio.gather()` |
| `ExecutorService` | `asyncio` 事件循环（内置） |

**AgentScope 中的异步**：
- AgentScope 所有 Agent 调用都是异步的
- 使用 `await agent(...)` 调用
- 多 Agent 并行：`await FanoutPipeline([a1, a2, a3])("task")`
</details>

---

### 练习 7.4: 设计模式映射 [基础]

**题目**：
以下代码使用了哪些设计模式？请在 Java 和 AgentScope 两边都指出来：

```python
# Python/AgentScope
class MyAgent(ReActAgentBase):
    async def reply(self, msg):
        # 使用策略模式：不同 Model 有不同策略
        response = await self.model(msg)
        return response
```

**验证方式**：
检查是否正确识别设计模式。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**识别出的设计模式**：

| 设计模式 | Java 实现 | Python/AgentScope 实现 |
|----------|-----------|------------------------|
| **策略模式** | 不同 Model 实现同一接口 | `self.model` 可以是 OpenAI/Claude/Ollama |
| **模板方法** | `AgentBase` 定义 `reply()` 骨架 | `ReActAgentBase` 定义推理循环骨架 |
| **观察者模式** | `ApplicationEventPublisher` | `MsgHub` + `observe()` |
| **装饰器模式** | `@Transactional` | `Toolkit.register_tool_function()` |

**策略模式详解**：

```python showLineNumbers
# Python 策略模式
class MyAgent:
    def __init__(self, model: ChatModelBase):  # ChatModelBase 是策略接口
        self.model = model  # 可以注入任何实现

# 使用时
agent1 = MyAgent(OpenAIChatModel(...))   # OpenAI 策略
agent2 = MyAgent(AnthropicChatModel(...))  # Anthropic 策略
```

**模板方法详解**：

```python showLineNumbers
# AgentBase 定义模板方法
class AgentBase:
    async def reply(self, msg):  # 模板方法
        await self.pre_process()  # 钩子方法
        result = await self.do_reply(msg)  # 子类实现
        await self.post_process()  # 钩子方法
        return result

# ReActAgent 实现具体逻辑
class ReActAgent(AgentBase):
    async def do_reply(self, msg):
        # 具体实现
        ...
```
</details>

---

### 练习 7.5: Spring Cloud 对比 [挑战]

**题目**：
某公司使用 Spring Cloud 构建微服务，现在想引入 AgentScope 构建 AI Agent。请分析：

1. AgentScope 的哪些功能可以替代 Spring Cloud 的哪些组件？
2. 如果要将 AgentScope 集成到现有的 Spring Cloud 体系中，需要注意什么？

**验证方式**：
检查对微服务架构和 AgentScope 特性的理解。

<details>
<summary>参考答案</summary>

**答案/解题思路**：

**1. Spring Cloud vs AgentScope 组件对比**：

| Spring Cloud 组件 | AgentScope 对应 | 说明 |
|------------------|-----------------|------|
| Eureka / Nacos | AgentScope Studio | 服务注册与发现 |
| Config Server | `agentscope.init()` 参数 | 配置管理 |
| Resilience4j | AgentScope 内置重试 | 熔断和容错 |
| Sleuth / Zipkin | OpenTelemetry + Studio | 链路追踪 |
| Feign / RestTemplate | MsgHub / A2A | 服务间通信 |
| Kafka / RabbitMQ | MsgHub | 消息队列 |
| Gateway | 独立实现 | API 网关 |
| Micrometer | AgentScope Studio | 可观测性 |

**2. 集成注意事项**：

**通信协议**：
- Spring Cloud 使用 HTTP/gRPC
- AgentScope 的 A2A 协议也是基于 HTTP
- 可以通过 `A2AAgent` 与 Spring 服务互调

**消息格式**：
- Spring Cloud Events 通常是 JSON
- AgentScope 的 `Msg` 也是类似结构
- 需要统一序列化格式

**数据存储**：
- Spring Data 用于持久化
- AgentScope Memory 可选 Redis/SQLAlchemy
- 可以复用现有数据库

**集成架构示例**：

```
┌─────────────────────────────────────────────────────────────┐
│                    Spring Cloud 体系                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                │
│  │  Gateway │  │ Config   │  │  Eureka  │                │
│  └────┬─────┘  └──────────┘  └──────────┘                │
│       │                                                      │
└───────┼──────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│                  AgentScope Agent                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                │
│  │ReActAgent│  │  MsgHub  │  │ Memory   │                │
│  └──────────┘  └──────────┘  └──────────┘                │
│                      │                                       │
└──────────────────────┼──────────────────────────────────────┘
                       │
                       ▼
              通过 A2A 或 HTTP
                       │
                       ▼
         ┌──────────────────────────────┐
         │    Spring Boot 微服务        │
         │    (提供业务能力)            │
         └──────────────────────────────┘
```

**建议**：
1. 将 AgentScope 作为独立的服务层，通过 A2A 或 HTTP 与 Spring Cloud 通信
2. 使用 MsgHub 实现 Agent 间的消息传递
3. 复用现有的 Redis/Kafka 基础设施
</details>

## 下一章

→ [深度模块](module_agent_deep.md) - 深入学习 Agent 模块源码

## 7.12 下一步

恭喜你完成学习！建议：

1. **实践**: 运行 `examples/` 中的示例
2. **探索**: 阅读 `src/agentscope/` 核心源码
3. **贡献**: 尝试修复 issues 或添加功能
4. **深入**: 学习 RAG、Realtime、Voice Agent 等高级功能
5. **关注**: 跟踪 AgentScope 2.0 开发进展

祝学习愉快！
