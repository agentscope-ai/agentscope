```
Module: `src/agentscope/session`
Responsibility: Session-level state management for multi-agent applications.
Key Types: `SessionManager`, `StateProvider`

Key Functions/Methods
- `save_session_state(session_id, **modules)` — persists state across sessions
  - Inputs: Session ID, module states
  - Returns: Operation status with metadata
  - Side‑effects: File I/O for state persistence
  - References: `src/agentscope/session/_json_session.py`