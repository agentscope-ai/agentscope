# -*- coding: utf-8 -*-
"""Provider-independent approval review contracts."""
import json
from typing import Any, Literal, NotRequired, Protocol, TypedDict

from pydantic import BaseModel, ConfigDict, Field


def review_json_size(value: Any) -> int:
    """Count UTF-8 bytes using the same JSON encoding as the request."""
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


class ReviewInputTooLarge(ValueError):
    """The complete approval request exceeds the configured input budget."""

    def __init__(self) -> None:
        super().__init__("Complete approval input exceeds the review budget")


class ReviewInstructions(TypedDict):
    """Application policy and contextual data delivered to the reviewer."""

    application_instructions: str
    untrusted_context: NotRequired[list[str]]


class PermissionReview(BaseModel):
    """A validated assessment of one exact tool invocation."""

    model_config = ConfigDict(extra="forbid")

    risk_level: Literal["low", "medium", "high", "critical"]
    user_authorization: Literal["high", "medium", "low", "unknown"]
    policy_violation: bool = Field(strict=True)
    rationale: str = Field(min_length=1)

    @property
    def allowed(self) -> bool:
        """Apply the same outcome policy for every reviewer provider."""
        if self.policy_violation or self.risk_level == "critical":
            return False
        if self.risk_level == "high":
            return self.user_authorization in ("high", "medium")
        return True


class PermissionReviewer(Protocol):
    """An independent reviewer with no tool-execution authority."""

    async def __call__(self, request: dict[str, Any]) -> PermissionReview:
        """Assess the exact action and its supporting evidence.

        A transcript block with type ``authorization_evidence_ref`` uses its
        zero-based ``index`` into ``request["authorization_evidence"]``.
        Read the original instruction at that position in the conversation.
        """
