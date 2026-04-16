# -*- coding: utf-8 -*-
"""Permission rule model for tool usage."""
from pydantic import BaseModel


class PermissionRule(BaseModel):
    """Permission rule for tool usage.

    A permission rule defines whether a specific tool or tool operation
    should be allowed, denied, or require user confirmation. The
    rule_content field has different semantics depending on the tool_name:

    - For "Bash": rule_content is a substring pattern matched against the
      command Example: rule_content="npm install" matches "npm install express"

    - For "Write"/"Read": rule_content is a glob pattern matched against file
      paths Example: rule_content="src/**" matches "src/main.py"

    - For other tools: rule_content is a tool-specific filter pattern
    """

    tool_name: str
    """The name of the tool this rule applies to (e.g., "Bash",
    "Write", "Read")."""

    rule_content: str | None
    """Optional filter pattern - semantics depend on tool_name."""

    behavior: str
    """The permission behavior ("allow", "deny", or "ask")."""

    source: str
    """Where this rule originated from (e.g., "userSettings",
    "projectSettings")."""
