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
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit, ToolResponse

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
    return ToolResponse(result=result)

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
    toolkit=toolkit
)

# 5. 运行
import asyncio

async def main():
    response = await agent("北京今天天气怎么样？")
    print(f"Agent回复: {response.content}")

asyncio.run(main())
```

---

## 🔍 代码解读

### 1. 工具函数定义
```python
def get_weather(city: str) -> ToolResponse:
    ...
    return ToolResponse(result=result)
```
- 普通函数，返回 `ToolResponse` 对象
- Agent可以调用它

### 2. Toolkit注册
```python
toolkit = Toolkit()
toolkit.register_tool_function(get_weather, group_name="weather")
```
- 创建工具箱并注册工具函数
- `group_name` 控制工具的分组

### 3. ReActAgent
```python
agent = ReActAgent(
    name="WeatherAssistant",
    model=...,
    sys_prompt="...",
    toolkit=toolkit
)
```
- 创建带工具的Agent
- Agent会自动决定何时调用工具

### 4. 异步调用
```python
response = await agent("北京今天天气怎么样？")
```
- Agent调用是异步的
- 需要`await`等待结果

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
