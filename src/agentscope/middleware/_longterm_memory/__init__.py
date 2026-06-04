# -*- coding: utf-8 -*-
"""Long-term memory middlewares for AgentScope agents.

Currently only the mem0 backend is implemented. Import the public class
from the middleware package::

    from agentscope.middleware import Mem0Middleware

Future backends (e.g. dedicated vector stores, custom user-profile
services) can sit alongside ``_mem0/`` under this package and be
re-exported here.
"""

from ._mem0 import Mem0Middleware

__all__ = ["Mem0Middleware"]
