## AgentBase

**继承**: `StateModule`

**源码**: `src/agentscope/agent/_agent_base.py`

### 概述

Base class for asynchronous agents.

### 属性

| 属性 | 类型 |
|------|------|
| `id` | `str` |
| `supported_hook_types` | `list[str]` |
| `_class_pre_reply_hooks` | `dict[str, Callable[['AgentBase', dict[str, Any]], dict[str, Any] | None]]` |
| `_class_post_reply_hooks` | `dict[str, Callable[['AgentBase', dict[str, Any], Msg], Msg | None]]` |
| `_class_pre_print_hooks` | `dict[str, Callable[['AgentBase', dict[str, Any]], dict[str, Any] | None]]` |
| `_class_post_print_hooks` | `dict[str, Callable[['AgentBase', dict[str, Any], Any], Any]]` |
| `_class_pre_observe_hooks` | `dict[str, Callable[['AgentBase', dict[str, Any]], dict[str, Any] | None]]` |
| `_class_post_observe_hooks` | `dict[str, Callable[['AgentBase', dict[str, Any], None], None]]` |

### 方法

- `__init__() -> None`
  - Initialize the agent.
- `async observe(msg: Msg | list[Msg] | None) -> None`
  - Receive the given message(s) without generating a reply.
- `async reply() -> Msg`
  - The main logic of the agent, which generates a reply based on the
- `async print(msg: Msg, last: bool, speech: AudioBlock | list[AudioBlock] | None) -> None`
  - The function to display the message.
- `_process_audio_block(msg_id: str, audio_block: AudioBlock) -> None`
  - Process audio block content.
- `_print_text_block(msg_id: str, name_prefix: str, text_content: str, thinking_and_text_to_print: list[str]) -> None`
  - Print the text block and thinking block content.
- `_print_last_block(block: ToolUseBlock | ToolResultBlock | ImageBlock | VideoBlock | AudioBlock, msg: Msg) -> None`
  - Process and print the last content block, and the block type
- `async __call__() -> Msg`
  - Call the reply function with the given arguments.
- `async _broadcast_to_subscribers(msg: Msg | list[Msg] | None) -> None`
  - Broadcast the message to all subscribers.
- `_strip_thinking_blocks(msg: Msg | list[Msg]) -> Msg | list[Msg]`
  - Remove thinking blocks from message(s) before sharing with other
- `_strip_thinking_blocks_single(msg: Msg) -> Msg`
  - Remove thinking blocks from a single message.
- `async handle_interrupt() -> Msg`
  - The post-processing logic when the reply is interrupted by the
- `async interrupt(msg: Msg | list[Msg] | None) -> None`
  - Interrupt the current reply process.
- `register_instance_hook(hook_type: AgentHookTypes, hook_name: str, hook: Callable) -> None`
  - Register a hook to the agent instance, which only takes effect
- `remove_instance_hook(hook_type: AgentHookTypes, hook_name: str) -> None`
  - Remove an instance-level hook from the agent instance.
- `register_class_hook(cls: Any, hook_type: AgentHookTypes, hook_name: str, hook: Callable) -> None`
  - The universal function to register a hook to the agent class, which
- `remove_class_hook(cls: Any, hook_type: AgentHookTypes, hook_name: str) -> None`
  - Remove a class-level hook from the agent class.
- `clear_class_hooks(cls: Any, hook_type: AgentHookTypes | None) -> None`
  - Clear all class-level hooks.
- `clear_instance_hooks(hook_type: AgentHookTypes | None) -> None`
  - If `hook_type` is not specified, clear all instance-level hooks.
- `reset_subscribers(msghub_name: str, subscribers: list['AgentBase']) -> None`
  - Reset the subscribers of the agent.
- `remove_subscribers(msghub_name: str) -> None`
  - Remove the msghub subscribers by the given msg hub name.
- `disable_console_output() -> None`
  - This function will disable the console output of the agent, e.g.
- `set_console_output_enabled(enabled: bool) -> None`
  - Enable or disable the console output of the agent. E.g. in a
- `set_msg_queue_enabled(enabled: bool, queue: Queue | None) -> None`
  - Enable or disable the message queue for streaming outputs.

### Java 对照

```java
public class AgentBase extends StateModule {
    // async observe
    public void observe(Msg | list[Msg] | None msg) { /* ... */ }
    // async reply
    public Msg reply() { /* ... */ }
    // async print
    public void print(Msg msg, boolean last, AudioBlock | list[AudioBlock] | None speech) { /* ... */ }
    // async handle_interrupt
    public Msg handle_interrupt() { /* ... */ }
    // async interrupt
    public void interrupt(Msg | list[Msg] | None msg) { /* ... */ }
    // register_instance_hook
    public void register_instance_hook(AgentHookTypes hook_type, String hook_name, Callable hook) { /* ... */ }
    // remove_instance_hook
    public void remove_instance_hook(AgentHookTypes hook_type, String hook_name) { /* ... */ }
    // register_class_hook
    public void register_class_hook(Object cls, AgentHookTypes hook_type, String hook_name, Callable hook) { /* ... */ }
    // remove_class_hook
    public void remove_class_hook(Object cls, AgentHookTypes hook_type, String hook_name) { /* ... */ }
    // clear_class_hooks
    public void clear_class_hooks(Object cls, AgentHookTypes | None hook_type) { /* ... */ }
    // clear_instance_hooks
    public void clear_instance_hooks(AgentHookTypes | None hook_type) { /* ... */ }
    // reset_subscribers
    public void reset_subscribers(String msghub_name, list['AgentBase'] subscribers) { /* ... */ }
    // remove_subscribers
    public void remove_subscribers(String msghub_name) { /* ... */ }
    // disable_console_output
    public void disable_console_output() { /* ... */ }
    // set_console_output_enabled
    public void set_console_output_enabled(boolean enabled) { /* ... */ }
    // set_msg_queue_enabled
    public void set_msg_queue_enabled(boolean enabled, Queue | None queue) { /* ... */ }
}
```
