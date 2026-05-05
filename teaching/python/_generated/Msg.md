## Msg

**源码**: `src/agentscope/message/_message_base.py`

### 概述

The message class in agentscope.

### 方法

- `__init__(name: str, content: str | Sequence[ContentBlock], role: Literal['user', 'assistant', 'system'], metadata: dict[str, JSONSerializableObject] | None, timestamp: str | None, invocation_id: str | None) -> None`
  - Initialize the Msg object.
- `to_dict() -> dict`
  - Convert the message into JSON dict data.
- `from_dict(cls: Any, json_data: dict) -> 'Msg'`
  - Load a message object from the given JSON data.
- `has_content_blocks(block_type: Literal['text', 'tool_use', 'tool_result', 'image', 'audio', 'video'] | None) -> bool`
  - Check if the message has content blocks of the given type.
- `get_text_content(separator: str) -> str | None`
  - Get the pure text blocks from the message content.
- `get_content_blocks(block_type: Literal['text']) -> Sequence[TextBlock]`
- `get_content_blocks(block_type: Literal['tool_use']) -> Sequence[ToolUseBlock]`
- `get_content_blocks(block_type: Literal['tool_result']) -> Sequence[ToolResultBlock]`
- `get_content_blocks(block_type: Literal['image']) -> Sequence[ImageBlock]`
- `get_content_blocks(block_type: Literal['audio']) -> Sequence[AudioBlock]`
- `get_content_blocks(block_type: Literal['video']) -> Sequence[VideoBlock]`
- `get_content_blocks(block_type: None) -> Sequence[ContentBlock]`
- `get_content_blocks(block_type: ContentBlockTypes | List[ContentBlockTypes] | None) -> Sequence[ContentBlock]`
  - Get the content in block format. If the content is a string,
- `__repr__() -> str`
  - Get the string representation of the message.

### Java 对照

```java
public class Msg extends Object {
    // to_dict
    public Map<String, Object> to_dict() { /* ... */ }
    // from_dict
    public 'Msg' from_dict(Object cls, Map<String, Object> json_data) { /* ... */ }
    // has_content_blocks
    public boolean has_content_blocks(Literal['text', 'tool_use', 'tool_result', 'image', 'audio', 'video'] | None block_type) { /* ... */ }
    // get_text_content
    public str | None get_text_content(String separator) { /* ... */ }
    // get_content_blocks
    public Sequence[TextBlock] get_content_blocks(Literal['text'] block_type) { /* ... */ }
    // get_content_blocks
    public Sequence[ToolUseBlock] get_content_blocks(Literal['tool_use'] block_type) { /* ... */ }
    // get_content_blocks
    public Sequence[ToolResultBlock] get_content_blocks(Literal['tool_result'] block_type) { /* ... */ }
    // get_content_blocks
    public Sequence[ImageBlock] get_content_blocks(Literal['image'] block_type) { /* ... */ }
    // get_content_blocks
    public Sequence[AudioBlock] get_content_blocks(Literal['audio'] block_type) { /* ... */ }
    // get_content_blocks
    public Sequence[VideoBlock] get_content_blocks(Literal['video'] block_type) { /* ... */ }
    // get_content_blocks
    public Sequence[ContentBlock] get_content_blocks(void block_type) { /* ... */ }
    // get_content_blocks
    public Sequence[ContentBlock] get_content_blocks(ContentBlockTypes | List[ContentBlockTypes] | None block_type) { /* ... */ }
}
```
