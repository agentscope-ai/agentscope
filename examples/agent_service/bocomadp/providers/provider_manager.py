# -*- coding: utf-8 -*-
"""Provider manager — multi-model routing with runtime switching.

Manages a registry of model providers. Each provider entry wraps a
``ChatModelBase`` subclass instance and its display metadata. The
``ProviderManager`` tracks the "active" provider+model pair so
:class:`AgentBuilder` can fetch the correct model per request.

This is the QwenPaw-style provider layer on top of AgentScope's
``ChatModelBase``. It adds:
- Runtime model switching (no restart needed)
- Provider listing for the frontend ``GET /models`` endpoint
- Capability caching (multimodal support, etc.)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProviderEntry:
    """A registered provider with its model instance and metadata."""

    provider_id: str
    model: Any  # agentscope.model.ChatModelBase
    model_name: str  # display name, e.g. "gpt-4o"
    display_name: str = ""  # human-friendly, e.g. "GPT-4o"
    is_active: bool = False
    supports_multimodal: bool = False
    metadata: dict = field(default_factory=dict)


class ProviderManager:
    """Registry of model providers with runtime switching.

    Maintains a dict of ``provider_id -> ProviderEntry``. The "active"
    entry is the one that :meth:`get_model` returns.
    """

    def __init__(self) -> None:
        self._providers: dict[str, ProviderEntry] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        provider_id: str,
        model: Any,
        *,
        model_name: str = "",
        display_name: str = "",
        supports_multimodal: bool = False,
        metadata: dict | None = None,
    ) -> None:
        """Register a new provider or update an existing one."""
        entry = ProviderEntry(
            provider_id=provider_id,
            model=model,
            model_name=model_name or provider_id,
            display_name=display_name or model_name or provider_id,
            supports_multimodal=supports_multimodal,
            metadata=metadata or {},
        )
        self._providers[provider_id] = entry
        # Auto-activate if this is the first provider
        if not any(e.is_active for e in self._providers.values()):
            entry.is_active = True
        logger.info(
            "provider registered: %s model=%s multimodal=%s",
            provider_id,
            entry.model_name,
            supports_multimodal,
        )

    def unregister(self, provider_id: str) -> None:
        """Remove a provider."""
        if provider_id in self._providers:
            was_active = self._providers[provider_id].is_active
            del self._providers[provider_id]
            if was_active and self._providers:
                # Promote the first remaining provider to active
                next(iter(self._providers.values())).is_active = True
            logger.info("provider unregistered: %s", provider_id)

    # ------------------------------------------------------------------
    # Active model management
    # ------------------------------------------------------------------

    def set_active(self, provider_id: str, model_name: str = "") -> bool:
        """Switch the active provider. Returns True on success."""
        entry = self._providers.get(provider_id)
        if entry is None:
            logger.warning("set_active: provider %s not found", provider_id)
            return False
        for e in self._providers.values():
            e.is_active = e is entry
        if model_name:
            entry.model_name = model_name
        logger.info(
            "active provider set: %s/%s",
            provider_id,
            entry.model_name,
        )
        return True

    def get_model(self) -> Any:
        """Return the active provider's model instance."""
        for entry in self._providers.values():
            if entry.is_active:
                return entry.model
        return None

    def get_active_model(self) -> ProviderEntry | None:
        """Return the active ProviderEntry, or None."""
        for entry in self._providers.values():
            if entry.is_active:
                return entry
        return None

    # ------------------------------------------------------------------
    # Listing (for frontend GET /models)
    # ------------------------------------------------------------------

    def list_providers(self) -> list[dict[str, Any]]:
        """Return all providers as dicts for the frontend."""
        return [
            {
                "provider_id": e.provider_id,
                "model_name": e.model_name,
                "display_name": e.display_name,
                "is_active": e.is_active,
                "supports_multimodal": e.supports_multimodal,
                "metadata": e.metadata,
            }
            for e in self._providers.values()
        ]

    def list_models(self) -> list[dict[str, Any]]:
        """Alias for list_providers — the frontend 'model list'."""
        return self.list_providers()


__all__ = ["ProviderManager", "ProviderEntry"]
