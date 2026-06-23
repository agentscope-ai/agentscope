# -*- coding: utf-8 -*-
"""Long-term memory middlewares for AgentScope agents.

Import the public middleware classes from the middleware package::

    from agentscope.middleware import FileLongTermMemoryMiddleware
    from agentscope.middleware import Mem0Middleware

Additional backends can sit alongside ``_fileLongTermMemory/`` and ``_mem0/``
under this package and be re-exported here.
"""

from ._fileLongTermMemory import FileLongTermMemoryMiddleware
from ._mem0 import Mem0Middleware

__all__ = ["FileLongTermMemoryMiddleware", "Mem0Middleware"]
