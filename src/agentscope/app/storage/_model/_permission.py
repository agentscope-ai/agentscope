# -*- coding: utf-8 -*-
"""Persisted approval settings shared by schedules and channel sessions."""
from typing import Any

from pydantic import BaseModel, model_validator

from ....permission import PermissionContext, PermissionMode


class AccessSettings(BaseModel):
    """Store approval mode separately from grants and restrictions."""

    permission_mode: PermissionMode = PermissionMode.ASK
    read_only: bool = False
    allow_workspace_edits: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize_permissions(cls, data: Any) -> Any:
        """Keep omitted settings unset unless a legacy mode implies them."""
        if not isinstance(data, dict) or "permission_mode" not in data:
            return data
        context = PermissionContext(mode=data["permission_mode"])
        return {
            **data,
            "permission_mode": context.mode,
            **context.model_dump(
                include=set(cls.model_fields),
                exclude_unset=True,
            ),
        }


class PermissionSettings(AccessSettings):
    """Session settings that allow configurable human approval."""

    interactive: bool = True

    def to_permission_context(self) -> PermissionContext:
        """Build a runtime context without losing migrated constraints."""
        return PermissionContext(
            mode=self.permission_mode,
            read_only=self.read_only,
            allow_workspace_edits=self.allow_workspace_edits,
            interactive=self.interactive,
        )
