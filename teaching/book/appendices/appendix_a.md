# 附录A：Java vs Python vs AgentScope 术语对照表

## 按Java概念索引

| Java概念 | Python对应 | AgentScope对应 | 解释 |
|----------|------------|----------------|------|
| class | class | AgentBase/Msg | 类定义 |
| this | self | self | 当前实例 |
| void | None | None | 空返回 |
| try/catch | try/except | try/except | 异常处理 |
| interface | Protocol/ABC | FormatterBase | 接口定义 |
| @Aspect | @decorator | @decorator | 拦截器 |
| Thread | asyncio | Task | 异步任务 |
| Map | dict | - | 字典 |
| List | list | - | 列表 |
| CompletableFuture | asyncio.Future | Task | 异步结果 |
| JUnit | pytest | - | 单元测试 |
| synchronized | asyncio.Lock | - | 异步锁 |
| Stream API | 列表推导式 | - | 函数式转换 |
| Optional | 条件表达式 | - | None处理 |
| Lambda | lambda | - | 匿名函数 |
| static field | 类属性 | 类属性 | 类级别变量 |
| static method | @classmethod | 少用 | 类方法 |
| getter/setter | @property | @property | 属性访问器 |
| extends | class Child(Parent) | 少用继承 | 继承 |
| implements | class Child(ABC) | Protocol | 接口实现 |
| HttpSession | - | Memory | 会话存储 |
| ObjectMapper | - | Formatter | 格式转换 |
| EventBus | - | MsgHub | 事件总线 |
| Filter/Interceptor | - | Hook | 拦截器 |
| Service | - | Tool | 服务/工具 |
| RestTemplate | - | ChatModel | HTTP调用 |

---

## 按AgentScope概念索引

| AgentScope | Python对应 | Java对应 | 解释 |
|------------|------------|----------|------|
| Agent | class | Service Bean | 智能体 |
| ReActAgent | - | StatefulHandler | 推理行动Agent |
| Msg | dataclass | POJO/DTO | 消息对象 |
| ChatModelBase | ABC | Interface | 模型基类 |
| Formatter | class | ObjectMapper | 格式转换器 |
| Toolkit | list + @decorator | Utils | 工具箱 |
| Tool | function | Service Method | 工具方法 |
| Memory | class | Session/Cache | 记忆存储 |
| InMemoryMemory | dict | HashMap | 内存存储 |
| RedisMemory | redis-py | Redis Cache | Redis存储 |
| Pipeline | class | Chain of Responsibility | 流水线 |
| SequentialPipeline | list | Stream Pipeline | 顺序处理 |
| FanoutPipeline | - | Fork/Join | 并行处理 |
| MsgHub | class | EventBus | 消息中枢 |
| Hook | class | Interceptor | 拦截器 |
| Runtime | - | Application Server | 运行时 |
| sys_prompt | str | @Description | 系统提示词 |
