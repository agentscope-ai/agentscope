## MsgHub

**源码**: `src/agentscope/pipeline/_msghub.py`

### 概述

MsgHub class that controls the subscription of the participated agents.

Example:
    In the following example, the reply message from `agent1`, `agent2`,
    and `agent3` will be broadcast to all the other agents in the MsgHub.

    .. code-block:: python

        async with MsgHub(participants=[agent1, agent2, agent3]):
            agent1()
            agent2()

    Actually, it has the same effect as the following code, but much more
    easy and elegant!

    .. code-block:: python

        x1 = ag

### 方法

- `__init__(participants: Sequence[AgentBase], announcement: list[Msg] | Msg | None, enable_auto_broadcast: bool, name: str | None) -> None`
  - Initialize a MsgHub context manager.
- `async __aenter__() -> 'MsgHub'`
  - Will be called when entering the MsgHub.
- `async __aexit__() -> None`
  - Will be called when exiting the MsgHub.
- `_reset_subscriber() -> None`
  - Reset the subscriber for agent in `self.participant`
- `add(new_participant: list[AgentBase] | AgentBase) -> None`
  - Add new participant into this hub
- `delete(participant: list[AgentBase] | AgentBase) -> None`
  - Delete agents from participant.
- `async broadcast(msg: list[Msg] | Msg) -> None`
  - Broadcast the message to all participants.
- `set_auto_broadcast(enable: bool) -> None`
  - Enable automatic broadcasting of the replied message from any

### Java 对照

```java
public class MsgHub extends Object {
    // add
    public void add(list[AgentBase] | AgentBase new_participant) { /* ... */ }
    // delete
    public void delete(list[AgentBase] | AgentBase participant) { /* ... */ }
    // async broadcast
    public void broadcast(list[Msg] | Msg msg) { /* ... */ }
    // set_auto_broadcast
    public void set_auto_broadcast(boolean enable) { /* ... */ }
}
```
