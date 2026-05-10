# P8-1 天气查询Agent

> **目标**：完成第一个实战项目——天气查询Agent

---

## 📋 需求分析

**我们要做一个**：能回答"北京今天天气怎么样"的Agent

**核心功能**：
1. 接收用户输入
2. 调用天气API获取数据
3. 返回天气信息

---

## 🏗️ 技术方案

```
┌─────────────────────────────────────────────────────────────┐
│                    天气查询Agent架构                        │
│                                                             │
│  用户 ──► Agent ──► Toolkit(search_weather) ──► API      │
│                              │                             │
│                              ▼                             │
│                         返回天气信息                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 💻 完整代码

```python showLineNumbers
# P8-1_weather_agent.py
import agentscope
from agentscope.agent import ReActAgent
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit, ToolResponse
from agentscope.message import TextBlock

# 1. 定义天气查询工具函数（无需装饰器）
def get_weather(city: str) -> ToolResponse:
    """查询城市天气

    Args:
        city: 城市名称，如"北京"、"上海"

    Returns:
        ToolResponse: 包含天气信息的响应
    """
    # 模拟天气数据（实际项目中调用真实API）
    weather_data = {
        "北京": "晴，25°C，适宜外出",
        "上海": "多云，28°C，略有闷热",
        "广州": "大雨，26°C，建议带伞",
    }
    result = weather_data.get(city, "抱歉，暂不支持该城市")
    return ToolResponse(content=[TextBlock(type="text", text=result)])

# 2. 初始化
agentscope.init(project="WeatherAgent")

# 3. 创建工具箱并注册工具
toolkit = Toolkit()
toolkit.register_tool_function(get_weather, group_name="weather")

# 4. 创建Agent
agent = ReActAgent(
    name="WeatherAssistant",
    model=OpenAIChatModel(
        api_key="your-api-key",
        model="gpt-4"
    ),
    sys_prompt="你是一个友好的天气预报助手。请根据用户询问的城市，使用天气查询工具获取信息并回答。",
    formatter=OpenAIChatFormatter(),
    toolkit=toolkit
)

# 5. 运行
import asyncio

async def main():
    response = await agent(Msg(
        name="user",
        content="北京今天天气怎么样？",
        role="user"
    ))
    print(f"Agent回复: {response.content}")

asyncio.run(main())
```

---


## 💡 Java开发者注意

天气查询Agent类似Java的Service+工具类模式：

```python
# Python Agent
agent = ReActAgent(
    name="WeatherAssistant",
    toolkit=toolkit,  # 类似Java的依赖注入
    ...
)
```

```java
// Java类似实现
@Service
public class WeatherAgent {
    @Autowired
    private WeatherService weatherService;  // 类似toolkit
    
    public String chat(String userInput) {
        // 根据输入决定是否调用服务
        if (needWeather(userInput)) {
            String city = extractCity(userInput);
            return weatherService.getWeather(city);  // 类似调用tool
        }
    }
}
```

| Python组件 | Java对应 | 说明 |
|-----------|----------|------|
| ReActAgent | @Service Bean | 管理的Agent实例 |
| Toolkit | @Autowired Services | 工具集合 |
| ToolResponse | DTO | 返回结果封装 |
| sys_prompt | @Description | 角色定义 |

---

## 🔍 代码解读

### 1. 工具函数定义

```python showLineNumbers
def get_weather(city: str) -> ToolResponse:
    """查询城市天气"""
    weather_data = {
        "北京": "晴，25°C，适宜外出",
        "上海": "多云，28°C，略有闷热",
        "广州": "大雨，26°C，建议带伞",
    }
    result = weather_data.get(city, "抱歉，暂不支持该城市")
    return ToolResponse(content=[TextBlock(type="text", text=result)])
```

**设计要点**：
- 函数签名：`city: str` 是输入参数
- 返回类型：`-> ToolResponse` 是统一的工具返回值类型
- 模拟数据：用字典存储天气信息演示用

---

### 2. Toolkit注册

```python showLineNumbers
toolkit = Toolkit()
toolkit.register_tool_function(get_weather, group_name="weather")
```

**设计要点**：
- `Toolkit()` 创建工具箱实例
- `register_tool_function()` 注册工具函数
- `group_name="weather"` 给工具分组

---

### 3. ReActAgent

```python showLineNumbers
agent = ReActAgent(
    name="WeatherAssistant",
    model=OpenAIChatModel(api_key="your-api-key", model="gpt-4"),
    sys_prompt="你是一个友好的天气预报助手...",
    toolkit=toolkit
)
```

**设计要点**：
- `name` 是Agent的名字，用于日志和追踪
- `model` 是AI大脑
- `sys_prompt` 定义Agent角色和行为
- `toolkit` 是Agent可以使用的工具集合

---

### 4. 异步调用

```python showLineNumbers
async def main():
    response = await agent(Msg(
        name="user",
        content="北京今天天气怎么样？",
        role="user"
    ))
    print(f"Agent回复: {response.content}")

asyncio.run(main())
```

**设计要点**：
- `async def` 定义异步函数
- `await agent()` 等待异步结果
- `asyncio.run()` 运行异步主函数

---

## 🔬 项目实战思路分析

### 项目结构

```
weather_agent/
├── P8-1_weather_agent.py    # 主程序
├── requirements.txt          # 依赖
└── README.md                # 说明文档
```

### 开发步骤

```
Step 1: 定义工具函数
        ↓
Step 2: 创建工具箱并注册
        ↓
Step 3: 创建Agent
        ↓
Step 4: 测试运行
```

### 调试技巧

```python
# 开启调试模式，查看Agent的思考过程
agentscope.init(
    project="WeatherAgent",
    logging_level="DEBUG"  # 设置为DEBUG可以看到详细日志
)
```

**日志输出示例**：
```
DEBUG: Agent思考: 用户问天气，我需要调用get_weather工具
DEBUG: Agent行动: get_weather(city="北京")
DEBUG: Agent观察: "晴，25°C，适宜外出"
```

---

## 🚀 运行效果

```
用户输入: 北京今天天气怎么样？

Agent思考: 用户问天气，我需要调用get_weather工具
Agent行动: get_weather(city="北京")
Agent观察: "晴，25°C，适宜外出"

Agent回复: 北京今天天气晴朗，气温25°C，非常适宜外出！
```

---

## 🐛 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| Agent不调用工具 | prompt不够清晰 | 强调"使用天气查询工具" |
| 返回"暂不支持" | 城市数据缺失 | 添加更多城市数据或调用真实API |

---

## 🎯 扩展思考

1. **如何添加真实天气API？**
   - 使用心知天气API、OpenWeatherMap等
   - 替换`get_weather`函数内部实现

2. **如何支持多天预报？**
   - 添加`get_forecast(city, days)`工具
   - 修改prompt引导用户询问

3. **如何添加缓存？**
   - 使用Redis缓存天气数据
   - 减少API调用次数

---

★ **项目总结** ─────────────────────────────────────
- 学会了创建带Tool的Agent
- 理解了ReAct循环自动调用工具
- 完成了第一个完整可用的Agent
─────────────────────────────────────────────────

## 🎯 思考题

<details>
<summary>1. 为什么AgentScope使用Toolkit而不是直接传递函数列表？</summary>

**答案**：
- **解耦**：Toolkit作为容器，工具函数不需要关心Agent的存在
- **分组**：通过`group_name`对工具进行逻辑分组，方便管理
- **可扩展**：可以在Toolkit层面添加工具发现、权限控制等功能
- **对比Java**：类似Spring的`ApplicationContext`管理Bean，而不是直接传函数引用
</details>

<details>
<summary>2. ReActAgent是如何决定调用哪个工具的？</summary>

**答案**：
- **LLM决策**：Agent内部的LLM分析用户输入和上下文，决定是否需要调用工具
- **函数匹配**：LLM生成的工具调用请求与已注册的工具函数进行匹配
- **参数提取**：从用户意图中提取工具所需的参数（如城市名）
- **类似Java**：策略模式 + 反射机制，但LLM的匹配更灵活
</details>

<details>
<summary>3. 如何让Agent支持更多工具而不只是天气查询？</summary>

**答案**：
- **注册多个工具**：在Toolkit中注册多个工具函数
  ```python
  toolkit.register_tool_function(get_weather, group_name="weather")
  toolkit.register_tool_function(search_news, group_name="search")
  toolkit.register_tool_function(set_alarm, group_name="util")
  ```
- **扩展group_name**：按功能模块分组工具
- **动态注册**：支持运行时动态添加新工具
- **工具描述**：每个工具函数要有清晰的docstring，让LLM理解何时使用
</details>
