"""Custom routers for BocomADP.

## Directory layout

- ``health.py``          — liveness / readiness probes
- ``platform_health.py`` — 平台健康检查（/platform/health）
- ``stats.py``        — example business router
- ``models.py``       — model listing + switching API
- ``custom/``         — your product-specific routers

Routers are mounted on the FastAPI app in ``main.py`` via
``app.include_router(...)``.
"""

from .models import models_router
from .health import health_router
from .platform_health import platform_health_router
from .stats import stats_router
from .uploads import uploads_router
from .credential_model import credential_model_router
from .oss_download import oss_download_router
from .session_usage import session_usage_router
from .agent_tools import agent_tools_router

__all__ = [
    "models_router",
    "health_router",
    "platform_health_router",
    "stats_router",
    "uploads_router",
    "credential_model_router",
    "oss_download_router",
    "session_usage_router",
    "agent_tools_router",
]
