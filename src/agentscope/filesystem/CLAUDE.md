```
Module: `src/agentscope/filesystem`
Responsibility: Logical file system providing controlled file operations with namespace-based permissions.

Key Types: `FileSystem`, `NamespaceManager`, `PermissionModel`

Key Functions/Methods
- `write(namespace, path, content)` — writes content to specified namespace path
  - Purpose: Enables structured storage for agent operations, logging, and state persistence without direct OS file system exposure.
  - Inputs: namespace (enum), path (string), content (bytes/string)
  - Returns: Success/Error status
  - Side-effects: Updates logical file system state; no disk I/O by default
  - References: `docs/filesystem/SOP.md`