"""Middleware registry — manages both ASGI and Agent middlewares.

Two types of middleware coexist:

1. **ASGI middleware** (in ``error_handler.py``, ``request_log.py``,
   ``trace_middleware.py``) — wraps the FastAPI app. Registered via
   ``create_app(extra_middlewares=[...])``.

2. **Agent middleware** (in ``agent_middleware.py``, ``custom/``) —
   wraps the agent's reply loop. Registered via
   :class:`MiddlewareRegistry` and injected into :class:`AgentBuilder`.

## Directory layout

- ``__init__.py``         — this file
- ``registry.py``         — :class:`MiddlewareRegistry` (agent MW manager)
- ``error_handler.py``    — ASGI: global error handler
- ``request_log.py``      — ASGI: access log
- ``agent_middleware.py`` — Agent: example middleware (logging)
- ``audit.py``            — Agent: 企业审计留痕
- ``factory.py``          — Agent: 企业中间件主动 build 工厂
- ``custom/``             — your product-specific agent middlewares
"""

from .audit import AuditMiddleware
from .factory import build_enterprise_middlewares
from .registry import MiddlewareRegistry

__all__ = [
    "AuditMiddleware",
    "MiddlewareRegistry",
    "build_enterprise_middlewares",
]
