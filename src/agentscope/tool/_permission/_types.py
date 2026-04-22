# -*- coding: utf-8 -*-
# pylint: disable=line-too-long
"""Permission system types and engine for tool usage control.

This module implements a permission system that controls tool execution based
on configurable rules. The permission system supports different matching
strategies depending on the tool type:

- For Bash tools: rule_content is a substring pattern matched against commands
- For Write/Read tools: rule_content is a glob pattern matched against file
 paths
- For other tools: rule_content uses generic matching logic
"""

from enum import Enum


class PermissionMode(Enum):
    """The mode of permission.

    Permission modes control how the system handles tool execution requests.
    Different modes are suitable for different scenarios:

    +---------------+--------------------------------------------------+--------------------------------+
    | Mode          | Behavior                                         | Use Case                       |
    +===============+==================================================+================================+
    | DEFAULT       | All operations require explicit permission       | Default mode, most secure      |
    |               | (unless there are explicit allow rules)          |                                |
    +---------------+--------------------------------------------------+--------------------------------+
    | ACCEPT_EDITS  | - Auto-allow file writes in working directories | User present, rapid iteration  |
    |               | - Auto-allow file reads in working directories  | development                    |
    |               | - Auto-allow filesystem commands (mkdir, rm, mv) |                                |
    |               | - Other operations follow normal rules           |                                |
    +---------------+--------------------------------------------------+--------------------------------+
    | EXPLORE       | Read-only mode:                                  | Exploring codebase, planning   |
    |               | - Allow: Read, Grep, Glob (read-only tools)     | implementation                 |
    |               | - Deny: Write, Edit, Bash (modification tools)  |                                |
    +---------------+--------------------------------------------------+--------------------------------+
    | BYPASS        | All operations automatically allowed             | Testing environment, sandbox,  |
    |               | (no permission checks)                           | fully trusted scenarios        |
    +---------------+--------------------------------------------------+--------------------------------+
    | DONT_ASK      | Convert all ASK decisions to DENY                | Scheduled tasks, background    |
    |               | (user not available to answer prompts)           | execution when user is away    |
    +---------------+--------------------------------------------------+--------------------------------+

    Attributes:
        DEFAULT: Default mode - requires explicit permission for each action
        ACCEPT_EDITS: Accept edits mode - automatically allows file edits within working directories
        EXPLORE: Explore mode - read-only, no writes or command execution allowed
        BYPASS: Bypass mode - allows all actions without permission checks
        DONT_ASK: Don't ask mode - converts all ASK decisions to DENY (for unattended execution)
    """  # noqa: E501

    DEFAULT = "default"
    ACCEPT_EDITS = "accept_edits"
    EXPLORE = "explore"
    BYPASS = "bypass"
    DONT_ASK = "dont_ask"


class PermissionBehavior(Enum):
    """The behavior of permission.

    Attributes:
        ALLOW: Allow the operation
        DENY: Deny the operation
        ASK: Ask the user for permission
        PASSTHROUGH: Let the permission engine continue with rule matching
            (used by tools to defer decision to the engine)
    """

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    PASSTHROUGH = "passthrough"
