# -*- coding: utf-8 -*-
"""In-container MCP Gateway sub-package.

Contains:

- :mod:`._server` — standalone gateway script that runs *inside*
  Docker / E2B containers.
- :class:`.GatewayMixin` — shared host-side logic for managing the
  gateway (deps, config, health, add/remove MCP).
"""

from ._mixin import GatewayMixin

__all__ = ["GatewayMixin"]
