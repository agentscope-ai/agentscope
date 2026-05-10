# 第二十九章 上帝类 vs 模块拆分

`_toolkit.py` 有 1684 行。文件开头的注释已经承认了问题：

```python
# TODO: We should consider to split this `Toolkit` class in the future.
# pylint: disable=too-many-lines
```

第 4-6 行，TODO 注释和 pylint 禁用同时出现。这是一个在 "应该拆" 和 "暂不拆" 之间悬而未决的决策。

## 一、决策回顾

`Toolkit` 类在第 117 行开始定义，横跨了 1567 行代码到文件末尾（第 1685 行是空行）。pylint 不仅禁用了 `too-many-lines`（文件级），还在类定义处禁用了 `too-many-public-methods`：

```python
class Toolkit(StateModule):  # pylint: disable=too-many-public-methods
```

这个类继承了 `StateModule`（来自 `src/agentscope/module.py`），意味着它还需要实现状态序列化接口。

### 1.1 全部公共方法

让我们列出这个类的全部公共方法：

1. `__init__`（第 152 行）：初始化四个核心字典和一个列表
2. `create_tool_group`（第 187 行）：创建工具组
3. `update_tool_groups`（第 222 行）：更新工具组激活状态
4. `remove_tool_groups`（第 241 行）：移除工具组
5. `register_tool_function`（第 274 行）：注册工具函数，12 个参数，260 行方法体
6. `remove_tool_function`（第 536 行）：移除工具函数
7. `get_json_schemas`（第 558 行）：获取活跃工具的 JSON schema
8. `set_extended_model`（第 621 行）：为工具设置扩展 schema
9. `remove_mcp_clients`（第 649 行）：移除 MCP 客户端的工具
10. `call_tool_function`（第 853 行）：执行工具函数（核心方法）
11. `register_mcp_client`（第 1035 行）：注册 MCP 客户端的工具
12. `state_dict` / `load_state_dict`（第 1180/1193 行）：状态序列化
13. `get_activated_notes`（第 1232 行）：获取激活组的说明
14. `reset_equipped_tools`（第 1250 行）：动态重置工具组
15. `clear`（第 1314 行）：清空工具箱
16. `register_agent_skill`（第 1328 行）：注册 Agent 技能
17. `remove_agent_skill`（第 1396 行）：移除 Agent 技能
18. `get_agent_skill_prompt`（第 1411 行）：获取技能提示词
19. `register_middleware`（第 1441 行）：注册中间件
20. `view_task`（第 1541 行）：查看异步任务状态
21. `cancel_task`（第 1580 行）：取消异步任务
22. `wait_task`（第 1630 行）：等待异步任务完成

22 个公共方法，涵盖了五个领域：工具函数管理、MCP 客户端管理、Agent 技能管理、中间件管理、异步任务管理。每个领域都足够独立，可以拆成单独的类。

### 1.2 共享状态

它们共享同一个状态。`__init__` 方法（第 152-185 行）初始化了核心数据结构：

```python
self.tools: dict[str, RegisteredToolFunction] = {}       # 第 170 行
self.groups: dict[str, ToolGroup] = {}                    # 第 171 行
self.skills: dict[str, AgentSkill] = {}                   # 第 172 行
self._middlewares: list = []                              # 第 173 行
self._async_tasks: dict[str, asyncio.Task] = {}           # 第 184 行
self._async_results: dict[str, ToolResponse] = {}         # 第 185 行
```

`call_tool_function` 需要读取 `self.tools` 来找到函数（第 888 行），需要检查 `self.groups` 来判断工具组是否活跃（第 891-893 行），需要执行 `self._middlewares` 链（通过 `@_apply_middlewares` 装饰器，第 852 行），可能还需要写入 `self._async_tasks`（第 945 行）。如果把工具管理和执行拆成两个类，执行类需要持有管理类的引用，管理类也需要通知执行类状态变化。拆分的代价是双向依赖。

### 1.3 耦合的具体体现

耦合不只是在概念层面，而是深入到方法内部的逻辑分支。以 `get_json_schemas`（第 558 行）为例：这个方法本应只负责生成 schema 列表，但它在第 597-613 行包含了一段特殊逻辑——检查是否存在 `reset_equipped_tools` 工具，如果存在就动态创建 Pydantic 模型来扩展其参数：

```python
if "reset_equipped_tools" in self.tools:
    fields = {}
    for group_name, group in self.groups.items():
        if group_name == "basic":
            continue
        fields[group_name] = (bool, Field(default=False, description=group.description))
    extended_model = create_model("_DynamicModel", **fields)
    self.set_extended_model("reset_equipped_tools", extended_model)
```

这段代码同时读写了 `self.tools` 和 `self.groups`，还调用了 `self.set_extended_model` 来修改工具的扩展模型。schema 生成、分组管理和动态模型扩展三个职责交织在一个方法中。

再看 `remove_tool_groups`（第 241 行）。删除一个组需要两步操作：从 `self.groups` 中移除组（第 265 行），然后遍历 `self.tools` 清理属于该组的所有工具（第 268-271 行）：

```python
for group_name in group_names:
    self.groups.pop(group_name, None)
tool_names = deepcopy(list(self.tools.keys()))
for tool_name in tool_names:
    if self.tools[tool_name].group in group_names:
        self.tools.pop(tool_name)
```

分组管理和工具注册在这里是不可分割的操作。拆分后，`ToolGroupManager.remove_group()` 需要回调 `ToolRegistry.remove()`，引入循环依赖或事件系统。

## 二、被否方案

最直觉的拆分是按职责分治：

```python
class ToolRegistry:
    """负责工具的注册、移除和 schema 生成"""
    def register(self, func, **kwargs): ...
    def remove(self, name): ...
    def get_schemas(self) -> list[dict]: ...

class ToolExecutor:
    """负责工具的执行、中间件、异步任务"""
    def __init__(self, registry: ToolRegistry):
        self.registry = registry
    async def call(self, tool_call: ToolUseBlock): ...
    def register_middleware(self, mw): ...

class ToolGroupManager:
    """负责工具组的创建、激活和停用"""
    def __init__(self, registry: ToolRegistry):
        self.registry = registry
    def create_group(self, name, desc): ...
    def activate(self, names): ...

class MCPManager:
    """负责 MCP 客户端的注册和移除"""
    def __init__(self, registry: ToolRegistry, group_mgr: ToolGroupManager):
        ...
    async def register_client(self, client, **kwargs): ...

class SkillManager:
    """负责 Agent 技能的注册和提示词生成"""
    def register_skill(self, skill_dir): ...
    def get_prompt(self) -> str | None: ...

class AsyncTaskManager:
    """负责异步工具任务的查看、取消和等待"""
    def view(self, task_id): ...
    def cancel(self, task_id): ...
    async def wait(self, task_id, timeout): ...

class Toolkit:
    """外观类，组合以上所有管理器"""
    def __init__(self):
        self.registry = ToolRegistry()
        self.groups = ToolGroupManager(self.registry)
        self.executor = ToolExecutor(self.registry)
        self.mcp = MCPManager(self.registry, self.groups)
        self.skills = SkillManager()
        self.async_tasks = AsyncTaskManager()

    # 委托方法
    def register_tool_function(self, **kwargs):
        self.registry.register(**kwargs)

    async def call_tool_function(self, tool_call):
        return await self.executor.call(tool_call)
```

这种设计的类图很漂亮。每个类职责单一，符合 SOLID 原则。但实际使用会变得复杂：

```python
# 拆分后：用户需要知道该调哪个管理器
toolkit.registry.register(func)          # 注册
toolkit.groups.activate(["search"])       # 激活
result = await toolkit.executor.call(tc)  # 执行
toolkit.mcp.register_client(client)       # MCP
```

对比现在的用法：

```python
# 现在：所有操作在 Toolkit 上完成
toolkit.register_tool_function(func)
toolkit.update_tool_groups(["search"], active=True)
result = await toolkit.call_tool_function(tc)
await toolkit.register_mcp_client(client)
```

### 2.1 拆分后的耦合问题

即使采用 Facade 模式保留公共 API，内部实现仍然需要解决跨管理器的数据访问问题。`call_tool_function`（第 853 行）的执行逻辑依赖五个状态源：

- `self.tools`（第 888 行）：查找注册的工具函数
- `self.groups`（第 891-893 行）：检查工具组是否活跃
- `self._middlewares`（通过装饰器，第 852 行）：中间件链
- `self._async_tasks`（第 945 行）：异步任务注册
- `tool_func.preset_kwargs`（第 912-914 行）：预设参数合并

拆分后，`ToolExecutor.call()` 需要同时访问 `ToolRegistry`（获取工具）、`ToolGroupManager`（检查激活状态）、自身的中间件列表和异步任务字典。方法签名可能变成：

```python
class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        group_manager: ToolGroupManager,
    ):
        self._registry = registry
        self._group_manager = group_manager
        self._middlewares: list = []
        self._async_tasks: dict = {}

    async def call(self, tool_call: ToolUseBlock):
        tool_func = self._registry.get(tool_call["name"])        # 跨对象
        if not self._group_manager.is_active(tool_func.group):   # 跨对象
            return error_response(...)
```

两个跨对象引用替代了两个 `self.` 属性访问。代码量增加了，但核心逻辑没有改变。

### 2.2 状态序列化的困境

`Toolkit` 继承了 `StateModule`，需要实现 `state_dict` 和 `load_state_dict`。当前的实现（第 1180-1230 行）只序列化 `active_groups`。如果拆分成多个管理器，每个管理器都需要自己的序列化逻辑，`Toolkit` 的 `state_dict` 需要聚合所有管理器的状态：

```python
def state_dict(self):
    return {
        **self.registry.state_dict(),
        **self.groups.state_dict(),
        **self.executor.state_dict(),
        **self.skills.state_dict(),
    }
```

加载时需要正确地将状态分发给各个管理器。这增加了状态管理的复杂度，而当前的实现只需要一行：

```python
return {"active_groups": [name for name, group in self.groups.items() if group.active]}
```

## 三、后果分析

### 3.1 好处：内聚性

1684 行的单一类有两个实际好处。

第一，调试直观。`call_tool_function`（第 853 行）在执行时需要读取 `self.tools`（第 888 行）、检查 `self.groups`（第 891-893 行）、应用 `self._middlewares`（通过 `@_apply_middlewares` 装饰器，第 852 行）、处理 `self._async_tasks`（第 932-944 行）。这四个状态在同一个对象上，没有跨对象引用，没有属性委托，没有 getter/setter。调试时设一个断点在 `call_tool_function`，你能看到所有相关状态。

第二，使用简单。开发者只需要知道一个类：`Toolkit`。所有的 `register_*`、`remove_*`、`call_*` 方法都在同一个对象上。API 表面积虽大，但入口只有一个。不存在"我应该调 `toolkit.registry` 还是 `toolkit.executor`"的困惑。

第三，无循环导入风险。所有功能在一个文件中实现，不存在模块间的循环依赖问题。在一个组件之间共享状态密集的系统中，拆分文件最常引入的问题就是循环导入。

### 3.2 好处：代码复用的自然发生

因为所有方法在同一个类中，内部方法的复用自然而然。`register_mcp_client`（第 1035 行）在注册 MCP 工具时直接调用 `self.register_tool_function`（第 1166 行），无需通过接口或回调：

```python
self.register_tool_function(
    tool_func=func_obj,
    group_name=group_name,
    preset_kwargs=preset_kwargs,
    postprocess_func=postprocess_func,
    namesake_strategy=namesake_strategy,
)
```

同样，`_execute_tool_in_background`（第 685 行）复用了 `call_tool_function` 中的错误处理模式：McpError 捕获（第 737-745 行）、通用异常捕获（第 747-755 行）、CancelError 处理（第 819-832 行）。这些模式在两处代码中独立实现，但共享相同的状态和返回类型。如果拆分成不同的类，这种模式共享需要通过基类或工具函数来实现。

### 3.3 代价：认知负载

1684 行的文件在代码审查时是负担。`register_tool_function` 单个方法从第 274 行到第 534 行，跨越了 260 行。这比很多完整类的代码还长。在编辑器中导航这个文件需要大量滚动。

认知负载也是问题。一个新贡献者要理解 `Toolkit`，需要同时理解工具注册、MCP 集成、Agent 技能、中间件、异步任务五个领域。每个领域都有自己的边界条件和错误处理。第 738-755 行的异常处理（`McpError`、通用 `Exception`）和第 819-843 行的异步取消处理交织在一起，增加了理解难度。

方法体中的重复模式加剧了这个问题。`call_tool_function`（第 971-1014 行）和 `_execute_tool_in_background`（第 712-755 行）有几乎相同的异常处理分支：

```python
# call_tool_function 中（第 971-1014 行）
except mcp.shared.exceptions.McpError as e:
    res = ToolResponse(content=[TextBlock(type="text", text=f"Error occurred when calling MCP tool: {e}")])

# _execute_tool_in_background 中（第 737-755 行）
except mcp.shared.exceptions.McpError as e:
    res = ToolResponse(content=[TextBlock(type="text", text=f"Error occurred when calling MCP tool: {e}")])
```

两段代码结构完全相同，但在同一个大类中重复出现。上帝类的一个隐蔽问题：方法越多，内部重复越难被发现。

### 3.4 代价：测试粒度

测试的粒度也受影响。如果你想测试异步任务管理，你需要构造一个完整的 `Toolkit` 实例，因为 `view_task`、`cancel_task`、`wait_task` 直接操作 `self._async_tasks` 和 `self._async_results`。无法单独测试这些方法而不引入整个 `Toolkit` 的依赖。

同样，测试 `get_json_schemas` 需要先注册工具、创建分组、设置激活状态——需要经过 `register_tool_function` 的 260 行逻辑。如果 schema 生成是独立类，你可以只构造 `SchemaGenerator` 实例，注入预设的工具列表来测试。

### 3.5 代价：合并冲突

在多人协作中，1684 行的单文件是合并冲突的温床。所有开发者对工具系统的改动都集中在同一个文件中。一个开发者在添加新的 MCP 功能，另一个在修改异步任务逻辑，两者的改动可能都涉及 `self.tools` 字典的访问模式。即使改动在不同方法中，git 的合并策略也可能产生冲突。

## 四、横向对比

**LangChain** 的工具系统是分散的。`BaseTool` 是基类，`@tool` 装饰器创建工具实例，`Toolkit` 只是一个容器。工具的执行逻辑在 `AgentExecutor` 中，不在 `Toolkit` 里。这意味着 LangChain 的 `Toolkit` 很轻量（通常不到 200 行），但代价是执行逻辑和工具管理分散在多个类中。开发者需要理解 `Tool`、`AgentExecutor`、`ToolNode` 之间的关系才能追踪一个工具调用的完整路径。

**AutoGen** 的工具注册更扁平。每个 Agent 持有自己的工具列表，没有独立的 `Toolkit` 类。工具函数直接绑定到 Agent，注册和执行都在 Agent 内部。这避免了上帝类问题，但失去了工具的跨 Agent 共享能力。如果两个 Agent 需要使用同一组工具，每个 Agent 都需要独立注册一遍。

**CrewAI** 的工具系统最小化。`Tool` 基类加上 `@tool` 装饰器，没有分组管理、中间件或异步执行。简单是优点，但面对复杂场景时能力不足。CrewAI 的选择暗示它的目标场景是简单的任务编排，而非需要动态工具管理的复杂 Agent 系统。

**Semantic Kernel**（微软）采用了插件式设计。`KernelPlugin` 类似 `Toolkit`，但粒度更细——每个插件是一个独立的类文件。`Kernel` 本身充当所有插件的注册中心。工具的执行通过 `Kernel.invoke` 统一入口，但中间件和过滤器在 `Kernel` 层而非插件层。这种设计将 `Toolkit` 的注册职责和执行职责分到了不同的层级。

AgentScope 的 `Toolkit` 是五个框架中最重的工具管理类。它承担了注册、分组、MCP 集成、技能管理、中间件、异步执行六个职责。源码中的 TODO 注释（第 4 行）已经承认了这个设计需要重构，但重构的时机和方式还没有确定。

值得注意的是，AgentScope 的选择也反映了其功能定位。LangChain 和 CrewAI 没有工具分组的概念，AutoGen 没有中间件链，Semantic Kernel 没有内建的异步任务管理。AgentScope 的 `Toolkit` 之所以膨胀，是因为它试图在工具管理层解决其他框架留给开发者自行解决的问题。

## 五、你的判断

上帝类是一个阶段性的设计决策，不是一个永久的设计目标。

`Toolkit` 之所以膨胀到 1684 行，是因为它的职责随着框架的发展不断扩展。最初可能只有工具注册和执行，后来加了 MCP 支持，再后来加了分组管理、中间件、异步任务、Agent 技能。每次扩展都是"加一个方法就行"，直到"加一个方法"累积成了 22 个公共方法。

拆分的关键问题是：如何在不增加使用复杂度的前提下，将内部实现分散到多个类？一个可能的答案是 Facade 模式：`Toolkit` 作为外观类保留所有公共方法，但内部委托给专门的子模块。用户只看到 `Toolkit`，但实现分布在 `ToolRegistry`、`ToolExecutor`、`ToolGroupManager` 等内部类中。

但 Facade 模式引入了间接层。对于当前的开发阶段，直接维护 1684 行的文件可能比引入间接层更实际。

另一个方向是提取独立的功能模块。异步任务管理（`view_task`、`cancel_task`、`wait_task`，共 145 行）和 Agent 技能管理（`register_agent_skill`、`remove_agent_skill`、`get_agent_skill_prompt`，共 112 行）与其他功能的耦合最弱。它们不读取 `self.groups`，不修改 `self._middlewares`，只操作各自的字典。这两个模块是最安全的拆分候选——提取它们不会引入循环依赖，也不会破坏现有的调用链。

中间件和 MCP 管理的拆分则复杂得多。中间件链直接包装 `call_tool_function`，MCP 注册复用 `register_tool_function` 的完整逻辑。拆分它们需要引入回调或事件机制，增加的复杂度可能超过收益。

拆分的时机可以用一个简单的指标判断：当这个文件增长到 2500 行以上，或者第三个贡献者抱怨难以理解时，就应该动手。当前的 1684 行处于"已经很长但尚可管理"的区间。文件顶部的 TODO 注释是最好的信号——作者自己已经意识到了问题，只是还没到必须解决的时刻。

你怎么判断？1684 行是应该拆的信号，还是可以继续增长的余地？
