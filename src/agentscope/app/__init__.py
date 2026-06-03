# -*- coding: utf-8 -*-
"""The FastAPI based agent service module, which contains all service-related
components and a configurable FastAPI app factory.
"""

from ._app import create_app

__all__ = [
    "create_app",
]
