## SummarySchema

**继承**: `BaseModel`

**源码**: `src/agentscope/agent/_react_agent.py`

### 概述

The compressed memory model, used to generate summary of old memories

### 属性

| 属性 | 类型 |
|------|------|
| `task_overview` | `str` |
| `current_state` | `str` |
| `important_discoveries` | `str` |
| `next_steps` | `str` |
| `context_to_preserve` | `str` |

### Java 对照

```java
public class SummarySchema extends BaseModel {
}
```
