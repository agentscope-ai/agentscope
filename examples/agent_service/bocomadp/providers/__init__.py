"""Multi-provider model routing.

Provides :class:`ProviderManager` — a registry of model providers
(OpenAI, DashScope, Anthropic, etc.) with runtime model switching.

Usage::

    pm = ProviderManager()
    pm.register("openai", OpenAIChatModel(...))
    pm.set_active("openai", "gpt-4o")
    model = pm.get_model()  # → current active model instance

The :class:`ProviderManager` is injected into :class:`AgentBuilder`
so every request gets the currently-active model without restart.
"""

from .ellm_key import EllmKeyRefresher, fetch_ellm_key
from .provider_manager import ProviderManager, ProviderEntry

__all__ = [
    "ProviderManager",
    "ProviderEntry",
    "fetch_ellm_key",
    "EllmKeyRefresher",
]
