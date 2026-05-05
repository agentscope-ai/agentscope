## ReActAgent

**继承**: `ReActAgentBase`

**源码**: `src/agentscope/agent/_react_agent.py`

### 概述

A ReAct agent implementation in AgentScope, which supports

- Realtime steering
- API-based (parallel) tool calling
- Hooks around reasoning, acting, reply, observe and print functions
- Structured output generation

### 属性

| 属性 | 类型 |
|------|------|
| `finish_function_name` | `str` |

### 方法

- `__init__(name: str, sys_prompt: str, model: ChatModelBase, formatter: FormatterBase, toolkit: Toolkit | None, memory: MemoryBase | None, long_term_memory: LongTermMemoryBase | None, long_term_memory_mode: Literal['agent_control', 'static_control', 'both'], enable_meta_tool: bool, parallel_tool_calls: bool, knowledge: KnowledgeBase | list[KnowledgeBase] | None, enable_rewrite_query: bool, plan_notebook: PlanNotebook | None, print_hint_msg: bool, max_iters: int, tts_model: TTSModelBase | None, compression_config: CompressionConfig | None) -> None`
  - Initialize the ReAct agent
- `sys_prompt() -> str`
  - The dynamic system prompt of the agent.
- `async reply(msg: Msg | list[Msg] | None, structured_model: Type[BaseModel] | None) -> Msg`
  - Generate a reply based on the current state and input arguments.
- `async _reasoning(tool_choice: Literal['auto', 'none', 'required'] | None) -> Msg`
  - Perform the reasoning process.
- `async _acting(tool_call: ToolUseBlock) -> dict | None`
  - Perform the acting process, and return the structured output if
- `async observe(msg: Msg | list[Msg] | None) -> None`
  - Receive observing message(s) without generating a reply.
- `async _summarizing() -> Msg`
  - Generate a response when the agent fails to solve the problem in
- `async handle_interrupt(msg: Msg | list[Msg] | None, structured_model: Type[BaseModel] | None) -> Msg`
  - The post-processing logic when the reply is interrupted by the
- `generate_response() -> ToolResponse`
  - Generate required structured output by this function and return it
- `async _retrieve_from_long_term_memory(msg: Msg | list[Msg] | None) -> None`
  - Insert the retrieved information from the long-term memory into
- `async _retrieve_from_knowledge(msg: Msg | list[Msg] | None) -> None`
  - Insert the retrieved documents from the RAG knowledge base(s) if
- `async _compress_memory_if_needed() -> None`
  - Compress the memory content if needed.

### Java 对照

```java
public class ReActAgent extends ReActAgentBase {
    // sys_prompt
    public String sys_prompt() { /* ... */ }
    // async reply
    public Msg reply(Msg | list[Msg] | None msg, Type[BaseModel] | None structured_model) { /* ... */ }
    // async observe
    public void observe(Msg | list[Msg] | None msg) { /* ... */ }
    // async handle_interrupt
    public Msg handle_interrupt(Msg | list[Msg] | None msg, Type[BaseModel] | None structured_model) { /* ... */ }
    // generate_response
    public ToolResponse generate_response() { /* ... */ }
}
```
