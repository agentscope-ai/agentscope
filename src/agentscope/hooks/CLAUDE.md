```
Module: `src/agentscope/hooks`
Responsibility: Hook system for extensibility and customization.
Key Types: `HookManager`, `HookRegistry`

Key Functions/Methods
- `register_hook(hook_type, callback)` — adds customization points throughout agent lifecycle
  - Purpose: Enables realtime steering, observation events, and custom processing.

Related SOP: `docs/hook_system.md`