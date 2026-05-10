# A2A 代理协议深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [核心组件](#2-核心组件)
3. [AgentCard解析器](#3-agentcard解析器)
4. [使用示例](#4-使用示例)
5. [练习题](#5-练习题)

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举 A2A 协议的核心组件和解析器类型 | 列举、识别 |
| 理解 | 解释 AgentCard 的作用和多种解析器适用场景 | 解释、比较 |
| 应用 | 使用 FileAgentCardResolver 配置静态 Agent 发现 | 实现、配置 |
| 分析 | 分析不同解析器（File/WellKnown/Nacos）的优缺点 | 分析、对比 |
| 评价 | 评价 A2A 协议在多Agent系统服务发现中的价值 | 评价、推荐 |

## 先修检查

在开始学习本模块之前，请确认您已掌握以下知识：

- [ ] Python 抽象基类（ABC）和 `@abstractmethod`
- [ ] Python 类型提示（TYPE_CHECKING）
- [ ] 多Agent系统基本概念
- [ ] 服务发现机制

**预计学习时间**: 20 分钟

---

## 1. 模块概述

### 1.1 什么是 A2A 协议

A2A（Agent-to-Agent）协议是 AgentScope 中的代理间通信协议，用于实现多Agent系统中的**服务发现**和**代理卡片（AgentCard）**查询。

**核心问题**：在多Agent系统中，如何让一个Agent发现和联系其他Agent？

**解决方案**：通过 AgentCard 描述Agent的能力和地址，其他Agent可以通过 A2A 解析器查询。

### 1.2 目录结构

```
src/agentscope/a2a/
├── __init__.py                    # 模块导出
├── _base.py                       # AgentCardResolverBase 基类
├── _file_resolver.py              # 文件解析器
├── _well_known_resolver.py        # Well-Known URL 解析器
└── _nacos_resolver.py             # Nacos 服务发现解析器
```

### 1.3 核心组件

| 组件 | 文件位置 | 说明 |
|------|----------|------|
| `AgentCardResolverBase` | `_base.py:12` | 解析器抽象基类 |
| `FileAgentCardResolver` | `_file_resolver.py` | 文件-based 解析器 |
| `WellKnownAgentCardResolver` | `_well_known_resolver.py` | Well-Known URL 解析器 |
| `NacosAgentCardResolver` | `_nacos_resolver.py` | Nacos 服务发现解析器 |

---

## 2. 核心组件

### 2.1 AgentCardResolverBase 抽象基类

**文件**: `src/agentscope/a2a/_base.py:12`

```python
class AgentCardResolverBase:
    """Base class for A2A agent card resolvers, responsible for fetching
    agent cards from various sources. Implementations must provide the
    `get_agent_card` method to retrieve the agent card.
    """

    @abstractmethod
    async def get_agent_card(self, *args: Any, **kwargs: Any) -> AgentCard:
        """Get Agent Card from the configured source.

        Returns:
            `AgentCard`:
                The resolved agent card object.
        """
```

**设计模式**：模板方法模式
- 基类定义接口 `get_agent_card()`
- 子类实现具体的解析逻辑

---

## 3. AgentCard解析器

### 3.1 FileAgentCardResolver

从本地文件加载 AgentCard。

**适用场景**：开发环境、静态配置

```python
from agentscope.a2a import FileAgentCardResolver

resolver = FileAgentCardResolver(file_path="agent_cards.json")
agent_card = await resolver.get_agent_card(agent_id="assistant")
```

### 3.2 WellKnownAgentCardResolver

从 Well-Known URL 端点获取 AgentCard。

**适用场景**：生产环境、已知服务端点

```python
from agentscope.a2a import WellKnownAgentCardResolver

resolver = WellKnownAgentCardResolver(base_url="https://agent.example.com")
agent_card = await resolver.get_agent_card(agent_id="assistant")
```

### 3.3 NacosAgentCardResolver

从 Nacos 服务发现平台获取 AgentCard。

**适用场景**：微服务架构、需要动态服务发现

```python
from agentscope.a2a import NacosAgentCardResolver

resolver = NacosAgentCardResolver(
    server_addresses=["http://nacos.example.com:8848"],
    namespace="public"
)
agent_card = await resolver.get_agent_card(agent_id="assistant")
```

---

## 4. 使用示例

### 4.1 AgentCard 结构示例

AgentCard 描述了一个Agent的元信息：

```python
# 假设的 AgentCard 结构
{
    "agent_id": "assistant",
    "name": "AI Assistant",
    "description": "通用AI助手",
    "capabilities": ["chat", "reasoning", "tool_use"],
    "endpoint": "http://agent.example.com:8000",
    "version": "1.0.0"
}
```

### 4.2 多Agent服务发现

```python
from agentscope.a2a import FileAgentCardResolver

# 创建解析器
resolver = FileAgentCardResolver(file_path="agent_cards.json")

# 发现Agent
async def discover_agent(agent_id: str):
    agent_card = await resolver.get_agent_card(agent_id)
    print(f"发现Agent: {agent_card.name}")
    print(f"端点: {agent_card.endpoint}")
    return agent_card
```

---

## 5. 练习题

### 5.1 基础题

1. 解释 A2A 协议在多Agent系统中的作用
2. 列举三种 AgentCardResolver 并说明适用场景

### 5.2 提高题

3. 设计一个新的 AgentCardResolver 实现，从 Redis 获取 AgentCard
4. 比较 FileAgentCardResolver 和 NacosAgentCardResolver 的优缺点

---

## 小结

| 组件 | 作用 | 适用场景 |
|------|------|----------|
| AgentCardResolverBase | 解析器抽象基类 | 定义接口规范 |
| FileAgentCardResolver | 文件解析器 | 开发环境、静态配置 |
| WellKnownAgentCardResolver | Well-Known URL 解析器 | 生产环境、已知端点 |
| NacosAgentCardResolver | Nacos 解析器 | 微服务、动态发现 |

A2A 协议为多Agent系统提供了标准化的服务发现机制，使得Agent之间可以相互发现和通信。

---

*文档版本: 1.0*
*最后更新: 2026-05-10*
