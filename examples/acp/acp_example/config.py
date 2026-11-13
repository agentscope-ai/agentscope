# -*- coding: utf-8 -*-
"""Environment-based configuration for the ACP example.

Everything a user can tune without editing code is read from environment
variables here (the "EXPOSED" column of the fixed-vs-exposed matrix in
DESIGN.md §7). Everything else is fixed by the example.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Resolved environment configuration."""

    model_provider: str
    model_name: str | None
    authority: str
    permission_mode: str
    agent_name: str
    workspace_backend: str

    @property
    def is_shell_authority(self) -> bool:
        """Whether fs/terminal operations are delegated to the ACP client."""
        return self.authority == "shell"


def load_config() -> Config:
    """Read the example's configuration from environment variables."""
    authority = os.environ.get("ACP_AUTHORITY", "shell").lower()
    if authority not in ("shell", "workspace"):
        raise ValueError(
            f"ACP_AUTHORITY must be 'shell' or 'workspace', got {authority!r}",
        )
    return Config(
        model_provider=os.environ.get(
            "ACP_MODEL_PROVIDER",
            "dashscope",
        ).lower(),
        model_name=os.environ.get("ACP_MODEL_NAME") or None,
        authority=authority,
        permission_mode=os.environ.get("ACP_PERMISSION_MODE", "default"),
        agent_name=os.environ.get("ACP_AGENT_NAME", "agentscope-acp"),
        workspace_backend=os.environ.get(
            "ACP_WORKSPACE_BACKEND",
            "local",
        ).lower(),
    )
