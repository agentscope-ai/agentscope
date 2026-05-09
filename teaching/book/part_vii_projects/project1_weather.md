# 项目1：天气查询Agent

> **难度**：⭐（入门）
> **预计时间**：2小时

---

## 🎯 学习目标

- 掌握Toolkit基本用法
- 实现函数工具注册
- 处理工具返回结果

---

## 1. 需求分析

### 功能需求

用户输入城市名，Agent返回该城市的天气信息。

```
用户: "北京天气怎么样？"
Agent: "北京今天天气晴朗，温度25度，空气质量良好。"
```

### 技术要点

- 定义天气查询工具函数
- 使用Toolkit注册工具
- ReActAgent调用工具处理请求

---

## 2. 系统设计

```
┌─────────────────────────────────────────┐
│                  User                    │
└─────────────────┬───────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────┐
│            ReActAgent                   │
│  ┌─────────────────────────────────┐   │
│  │  Reasoning: 需要查询天气          │   │
│  │  Acting: 调用get_weather工具    │   │
│  │  Observation: 获取天气结果        │   │
│  └─────────────────────────────────┘   │
└─────────────────┬───────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────┐
│              Toolkit                    │
│         get_weather(city)              │
└─────────────────────────────────────────┘
```

---

## 3. 代码实现

```python
import agentscope
from agentscope.agent import ReActAgent
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit

# 1. 创建工具
toolkit = Toolkit()

@toolkit.register_tool_function(
    name="get_weather",
    description="获取指定城市的天气信息"
)
def get_weather(city: str) -> str:
    """模拟天气查询"""
    weather_data = {
        "北京": "晴朗，25度，空气质量良好",
        "上海": "多云，28度，有轻微污染",
        "广州": "雷阵雨，30度，湿度较大",
    }
    return weather_data.get(city, "暂无数据")

# 2. 创建模型
model = OpenAIChatModel(
    model_name="gpt-4",
    api_key="your-api-key"
)

# 3. 创建Agent
agent = ReActAgent(
    name="天气助手",
    model=model,
    sys_prompt="你是一个天气助手，用户问你城市天气时，使用get_weather工具查询。",
    formatter=OpenAIChatFormatter(),
    toolkit=toolkit
)

# 4. 运行
async def main():
    response = await agent(Msg(
        name="user",
        content="北京天气怎么样？",
        role="user"
    ))
    print(response.content)

agentscope.run(main())
```

---

## 4. 运行结果

```
>>> asyncio.run(main())
北京今天天气晴朗，温度25度，空气质量良好。
```

---

## 5. 扩展思考

<details>
<summary>点击查看</summary>

1. **如何处理API错误？**
   - 在工具函数中添加异常处理
   - 返回友好的错误信息

2. **如何添加更多城市？**
   - 连接真实的天气API
   - 扩展toolkit注册

3. **如何支持多语言？**
   - 在工具返回后添加翻译步骤

</details>

---

★ **Insight** ─────────────────────────────────────
- **Toolkit = 工具仓库**，注册和管理函数
- **@toolkit.register** = 将函数暴露给Agent
- **工具返回** = 通过ToolResultBlock传递
─────────────────────────────────────────────────
