# -*- coding: utf-8 -*-
"""Controls how a declared subagent's runtime workspace root is determined."""
from __future__ import annotations

from enum import Enum


class WorkspaceMode(str, Enum):
    """Controls how a declared subagent's runtime workspace root is determined.

    Decision table::

        workspacePath  mode      runtime-workspace-root
        ─────────────────────────────────────────────────────────────────────────────
        set            ISOLATED  workspacePath  (definition dir is also the runtime root)
        set            SHARED    mainWorkspace  (definition skills/knowledge ignored)
        null           ISOLATED  mainWorkspace/agents/<name>/workspace/  (auto-created)
        null           SHARED    mainWorkspace
        (general-purpose, always SHARED)       mainWorkspace  (fully mirrors main agent)
    """

    ISOLATED = "isolated"
    """The subagent gets its own isolated workspace.

    - If ``workspace_path`` is set, that path is the runtime root and also
      the source for the sys_prompt (``AGENTS.md``).
    - Otherwise the runtime root is auto-created at
      ``main_workspace/agents/<name>/workspace/`` and the inline body is used
      as sys_prompt.
    """

    SHARED = "shared"
    """The subagent shares the main agent's workspace.

    - The runtime root is always ``main_workspace``, regardless of
      ``workspace_path``.
    - If ``workspace_path`` is set, its ``AGENTS.md`` is used as the sys_prompt
      body; but the definition's ``skills/``, ``knowledge/``, and
      ``MEMORY.md`` are ignored.
    - If ``workspace_path`` is absent, the inline body is used as sys_prompt.
    """
