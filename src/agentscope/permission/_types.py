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
    | DEFAULT       | Every operation asks for permission unless:      | Default mode, most secure      |
    |               | - an allow rule matches, OR                      |                                |
    |               | - the tool's ``check_permissions`` explicitly    |                                |
    |               |   returns ALLOW for the invocation (currently   |                                |
    |               |   only ``Bash`` auto-allows recognized          |                                |
    |               |   read-only commands such as ``ls``/``git       |                                |
    |               |   status``). Read/Glob/Grep return PASSTHROUGH  |                                |
    |               |   and fall through to the default ASK unless    |                                |
    |               |   an allow rule matches.                        |                                |
    +---------------+--------------------------------------------------+--------------------------------+
    | ACCEPT_EDITS  | - Auto-allow file writes in working directories | User present, rapid iteration  |
    |               | - Auto-allow file reads in working directories  | development                    |
    |               | - Auto-allow filesystem commands (mkdir, rm,     |                                |
    |               |   mv, cp, ...) **only when all target paths**   |                                |
    |               |   **resolve inside a working directory**         |                                |
    |               | - Other operations follow normal rules           |                                |
    +---------------+--------------------------------------------------+--------------------------------+
    | EXPLORE       | Read-only mode:                                  | Exploring codebase, planning   |
    |               | - Allow: read-only tools (``Read``/``Grep``/    | implementation                 |
    |               |   ``Glob``) and read-only bash commands         |                                |
    |               |   (e.g. ``ls``, ``git status``)                  |                                |
    |               | - Deny: any modification tool / command         |                                |
    |               | - User-configured DENY or ASK rules take         |                                |
    |               |   precedence over the read-only auto-allow      |                                |
    +---------------+--------------------------------------------------+--------------------------------+
    | BYPASS        | Skip all permission checks except explicit       | Sandboxed environments         |
    |               | user-configured deny / ask rules and tool       | (container, VM), unattended    |
    |               | DENY. **Safety ASKs from tools are NOT**         | runs where you fully trust     |
    |               | **enforced** — including ``rm -rf /``, writes   | the agent                      |
    |               | to ``~/.bashrc``, command-injection patterns,    |                                |
    |               | etc. Use deny rules to protect specific paths.   |                                |
    |               | For unattended runs that still need safety,      |                                |
    |               | prefer DONT_ASK.                                 |                                |
    +---------------+--------------------------------------------------+--------------------------------+
    | DONT_ASK      | Convert every ASK (including safety ASKs and     | Scheduled tasks, background    |
    |               | ASK-rule hits) to DENY. Safe-by-default for     | execution when user is away    |
    |               | unattended execution.                            |                                |
    +---------------+--------------------------------------------------+--------------------------------+

    Attributes:
        DEFAULT: Default mode - explicit permission per action. The
            only auto-allow path is the tool's own ``check_permissions``
            returning ALLOW (currently just ``Bash`` for recognized
            read-only commands like ``ls``/``git status``).
        ACCEPT_EDITS: Accept edits mode - automatically allows file
            edits within working directories (including filesystem
            bash commands whose every target is in a working dir).
        EXPLORE: Explore mode - read-only; modifications are denied.
        BYPASS: Bypass mode - skips safety checks; relies on user
            deny / ask rules as the only guardrail.
        DONT_ASK: Don't ask mode - converts all ASK decisions to DENY
            (for unattended execution).
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
