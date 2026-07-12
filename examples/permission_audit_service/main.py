# -*- coding: utf-8 -*-
"""Start the permission audit example agent service.

Follows ``examples/agent_service/main.py`` but omits RAG/MCP and adds a
``PermissionAuditMiddleware`` factory plus the demo tool, so the full
permission lifecycle (model tool call → permission engine → audit
middleware → confirmation UI → tool execution) is real and observable
in the service console while the existing Web UI is reused unchanged.
"""
import os
import sys

import uvicorn
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware

from agentscope.app import create_app
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager

# examples/permission_audit_service/ is not a package; import siblings.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_middleware import (  # noqa: E402
    PermissionAuditMiddleware,
    console_audit_sink,
)
from demo_tool import PermissionAuditDemoTool  # noqa: E402


async def permission_audit_factory(user_id, agent_id, session_id):
    """Per-assembly audit middleware bound to tenant/session identity."""
    return [
        PermissionAuditMiddleware(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            sink=console_audit_sink,
        ),
    ]


async def permission_audit_demo_tools(user_id, agent_id, session_id):
    """Per-assembly demo tool so every session can exercise audit scenarios."""
    return [PermissionAuditDemoTool()]


storage = RedisStorage(host="localhost", port=6379)

app = create_app(
    storage=storage,
    message_bus=InMemoryMessageBus(),
    workspace_manager=LocalWorkspaceManager(
        basedir=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "workspaces",
        ),
    ),
    extra_agent_middlewares=permission_audit_factory,
    extra_agent_tools=permission_audit_demo_tools,
    extra_middlewares=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ],
)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
