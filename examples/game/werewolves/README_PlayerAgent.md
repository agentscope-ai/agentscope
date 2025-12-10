# PlayerAgent - 狼人杀游戏智能体

## 概述

PlayerAgent 是一个符合比赛要求的狼人杀游戏智能体，基于 AgentScope 的 ReActAgentBase 开发，提供了完整的游戏策略功能和状态管理能力。

## 特性

✅ **比赛要求完全满足**

- 构造函数仅接受 `name` 参数
- 实现了 `observe` 函数
- 支持基于 BaseModel 的结构化输出
- 支持 Session 状态管理 (`state_dict()` 和 `load_state_dict()`)
- `__call__` 函数返回合法的 Msg 对象

✅ **游戏功能**

- 角色识别和状态管理
- 游戏阶段感知（白天/夜晚）
- 玩家行为分析和怀疑度追踪
- 基于角色的策略制定
- 记忆和学习能力

✅ **技术特性**

- 支持 ReAct 架构（推理-行动分离）
- 结构化输出支持
- 状态持久化
- 可配置模型（真实模型或模拟模型）

## 快速开始

### 基本使用

```python
from agent import PlayerAgent, PlayerResponse
from agentscope.message import Msg

# 创建智能体
agent = PlayerAgent(name="Player1")

# 观察游戏消息
msg = Msg("moderator", "Your role is werewolf", "assistant")
await agent.observe(msg)

# 生成响应 - 支持多种输入格式
response = await agent(msg)  # 单条消息
response = await agent([msg1, msg2])  # 消息列表（使用最后一条）
response = await agent(None)  # 无消息输入
print(response.content)

# 使用结构化输出（通过 metadata）
structured_msg = Msg("user", "What should we do?", "user", metadata={"structured_model": PlayerResponse})
structured_response = await agent(structured_msg)
print(structured_response.metadata)
```

### 状态管理

```python
# 保存状态
state = agent.state_dict()

# 加载状态到新智能体
new_agent = PlayerAgent(name="NewPlayer")
new_agent.load_state_dict(state)
```

### 使用真实模型

```python
# 使用真实模型（需要 API 密钥）
agent = PlayerAgent(
    name="Player1",
    use_mock_model=False,  # 使用真实模型
    api_key="your-api-key"  # 或设置 DASHSCOPE_API_KEY 环境变量
)

# 或使用环境变量
import os
os.environ["DASHSCOPE_API_KEY"] = "your-api-key"
agent = PlayerAgent(name="Player1", use_mock_model=False)
```

### 使用模拟模型（测试用）

```python
# 使用模拟模型（无需 API 密钥）
agent = PlayerAgent(name="Player1", use_mock_model=True)
```

## API 参考

### PlayerAgent 类

```python
class PlayerAgent(ReActAgentBase, StateModule):
    def __init__(
        self,
        name: str,
        use_mock_model: bool = False,
        api_key: Optional[str] = None,
    ) -> None:
        """
        初始化 PlayerAgent。

        Args:
            name: 玩家名称（唯一必需参数）
            use_mock_model: 是否使用模拟模型（默认 False）
            api_key: API 密钥（可选，也可通过环境变量设置）
        """
```

### 主要方法

#### `async observe(msg: Msg) -> None`

观察并处理游戏消息，更新内部状态。

#### `async __call__(msg: Msg | list[Msg] | None = None) -> Msg`

主要调用方法，支持以下签名：

- `await agent(msg)` - 单条消息
- `await agent([msg1, msg2])` - 消息列表（使用最后一条）
- `await agent(None)` - 无消息输入
- 结构化输出通过消息的 metadata 传递

#### `state_dict() -> dict`

获取智能体状态字典。

#### `load_state_dict(state_dict: dict, strict: bool = True) -> None`

从状态字典加载智能体状态。

### PlayerResponse 结构化模型

```python
class PlayerResponse(BaseModel):
    response: str  # 玩家响应内容
    confidence: float  # 决策置信度 (0.0-1.0)
    strategy: str  # 策略类型 (aggressive, defensive, neutral)
```

## 游戏策略

### 角色特定策略

- **狼人 (Werewolf)**: 隐藏身份，消灭村民
- **预言家 (Seer)**: 查验可疑玩家身份
- **女巫 (Witch)**: 策略性使用解药和毒药
- **猎人 (Hunter)**: 死亡时可以带走一名玩家
- **村民 (Villager)**: 通过投票找出狼人

### 怀疑度系统

智能体维护对其他玩家的怀疑度评分，基于：

- 投票模式分析
- 发言内容分析
- 游戏行为观察

## 测试

运行测试脚本验证功能：

```bash
python test_agent.py
```

测试内容包括：

- 构造函数验证
- observe 功能测试
- 结构化输出测试
- 常规调用测试
- 状态管理测试
- 集成场景测试

## 文件说明

- `agent.py` - 主要智能体实现（使用模拟模型）
- `agent_with_real_model.py` - 支持真实模型的完整版本
- `test_agent.py` - 功能测试脚本
- `README_PlayerAgent.md` - 本文档

## 依赖

- agentscope
- pydantic
- asyncio

## 环境要求

- Python 3.8+
- AgentScope 已安装
- API 密钥（使用真实模型时）

## 注意事项

1. 使用真实模型时需要有效的 API 密钥
2. 模拟模型仅用于测试，响应内容固定
3. 状态管理支持游戏暂停和恢复
4. 结构化输出可用于游戏分析和决策优化

## 扩展开发

可以通过以下方式扩展智能体功能：

- 添加更多角色特定策略
- 改进怀疑度算法
- 增加记忆和学习机制
- 集成更复杂的推理模型

---

**PlayerAgent** - 为狼人杀游戏竞赛而生的智能体解决方案
