## CompressionConfig

**继承**: `BaseModel`

**源码**: `src/agentscope/agent/_react_agent.py`

### 概述

The compression related configuration in AgentScope

### 属性

| 属性 | 类型 |
|------|------|
| `enable` | `bool` |
| `agent_token_counter` | `TokenCounterBase` |
| `trigger_threshold` | `int` |
| `keep_recent` | `int` |
| `compression_prompt` | `str` |
| `summary_template` | `str` |
| `summary_schema` | `Type[BaseModel]` |
| `compression_model` | `ChatModelBase | None` |
| `compression_formatter` | `FormatterBase | None` |

### Java 对照

```java
public class CompressionConfig extends BaseModel {
}
```
