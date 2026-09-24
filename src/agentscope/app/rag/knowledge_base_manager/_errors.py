# -*- coding: utf-8 -*-
"""Knowledge base manager exception hierarchy.

The router maps each exception to an HTTP status code in
:mod:`agentscope.app._router._knowledge_base`:

- :class:`KnowledgeBaseNotFoundError` → ``404``
- :class:`DimensionPolicyError`       → ``409``

Keeping the mapping inside the router (and not raising
``HTTPException`` from the manager) lets the same manager be reused by
non-HTTP entry points (e.g. CLI tools, the agent middleware) without
pulling in FastAPI.
"""


class KnowledgeBaseError(Exception):
    """Base class for knowledge base manager errors."""


class KnowledgeBaseNotFoundError(KnowledgeBaseError):
    """Raised when a knowledge base record cannot be located.

    The record is missing entirely, or it exists but does not belong
    to the authenticated user.  The two are reported identically to
    avoid leaking existence of other users' knowledge bases.
    """


class DimensionPolicyError(KnowledgeBaseError):
    """Raised when a create-time embedding model violates the manager's
    dimension policy.

    Carries both the offending dimension and the manager's policy so
    the router can surface a precise error message without having to
    re-derive either side.
    """

    def __init__(
        self,
        message: str,
        *,
        requested_dimension: int,
        policy_dimension: int | None,
    ) -> None:
        """Initialize the error.

        Args:
            message (`str`):
                Human-readable error message.
            requested_dimension (`int`):
                The dimension the caller asked for.
            policy_dimension (`int | None`):
                The dimension the policy enforces, or ``None`` if the
                policy admits any dimension.
        """
        super().__init__(message)
        self.requested_dimension = requested_dimension
        self.policy_dimension = policy_dimension
