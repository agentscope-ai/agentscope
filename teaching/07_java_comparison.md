# 第七章：Java 开发者视角

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
│   Tool (@function)        │  @Bean / Util类                      │
│   Memory                  │  Cache / Redis / Mem0                │
│   MsgHub                  │  EventBus / Kafka                    │
│   Pipeline                │  Workflow Engine / Camunda           │
│   sys_prompt              │  application.yml 配置                 │
│   tools=[...]             │  @Autowired List<Tool>              │
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

```python
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

```python
# Python: 装饰器
@function  # 类似 @Bean / @Component
@cacheable("orders")  # 类似 @Cacheable
def find_order_by_id(order_id: int) -> Order:
    return db.find(Order, order_id)

@async_task  # 类似 @Async
def process_order(order: Order):
    # 异步处理
    pass
```

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

```python
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
| **工厂模式** | FactoryBean | model_register 装饰器 |
| **单例模式** | @Scope("singleton") | Python 模块级单例 |
| **策略模式** | Strategy Interface | 不同 Model 实现 (*ChatModel) |
| **模板方法** | Abstract Class | AgentBase 抽象类 |
| **装饰器模式** | Decorator | @function 装饰器 |
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

```python
# Python: 注册表模式
from agentscope.model import model_register, ChatModelBase

@model_register("openai")
class OpenAIChatModel(ChatModelBase):
    def __init__(self, config):
        ...

@model_register("anthropic")
class AnthropicChatModel(ChatModelBase):
    def __init__(self, config):
        ...

# 使用
model = get_model("openai", config={...})

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

```python
# Python: asyncio (类似 Project Reactor / WebFlux)
import asyncio

async def process_async(input: str) -> str:
    # 异步执行
    await asyncio.sleep(1)  # 模拟 IO 操作
    return result

# 运行
result = asyncio.run(process_async("input"))

# 或使用 AgentScope 内置并发
from agentscope.pipeline import ConcurrentExecutor

executor = ConcurrentExecutor(agents=[agent1, agent2, agent3])
results = executor.run("并行任务")
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

```python
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

```python
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
agentscope.init(project="my-project", debug=True)
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
- Java 开发者专用: https://java.agentscope.io/
- Discord: https://discord.gg/eYMpfnkG8h

## 7.12 下一步

恭喜你完成学习！建议：

1. **实践**: 运行 `examples/` 中的示例
2. **探索**: 阅读 `src/agentscope/` 核心源码
3. **贡献**: 尝试修复 issues 或添加功能
4. **深入**: 学习 RAG、Realtime、Voice Agent 等高级功能
5. **关注**: 跟踪 AgentScope 2.0 开发进展

祝学习愉快！
