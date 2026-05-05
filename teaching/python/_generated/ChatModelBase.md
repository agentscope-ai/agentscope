## ChatModelBase

**源码**: `src/agentscope/model/_model_base.py`

### 概述

Base class for chat models.

### 属性

| 属性 | 类型 |
|------|------|
| `model_name` | `str` |
| `stream` | `bool` |

### 方法

- `__init__(model_name: str, stream: bool) -> None`
  - Initialize the chat model base class.
- `async __call__() -> ChatResponse | AsyncGenerator[ChatResponse, None]`
- `_validate_tool_choice(tool_choice: str, tools: list[dict] | None) -> None`
  - Validate tool_choice parameter.

### Java 对照

```java
public class ChatModelBase extends Object {
}
```
