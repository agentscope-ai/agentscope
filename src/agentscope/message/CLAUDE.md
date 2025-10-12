```
Module: `src/agentscope/message`
Responsibility: Message types and serialization for agent communication.
Key Types: `Message`, `Msg`, `MessageContent`

Key Functions/Methods
- `Message(content, role, name)` — standardized message structure for all agent communications
  - Purpose: Provides consistent data structure for message passing between agents
  - Inputs: Message content, sender role, message metadata
  - Returns: Serialized Message object ready for agent processing

- `Msg(text, role, name)` — simplified message constructor
  - Purpose: Quick message creation for common agent interactions
  - References: `src/agentscope/message/__init__.py`

Related SOP: `docs/message_protocol.md`