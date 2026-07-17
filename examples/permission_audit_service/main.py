# -*- coding: utf-8 -*-
# pylint: disable=wrong-import-position,wrong-import-order
"""Start the permission audit example agent service.

Follows ``examples/agent_service/main.py`` but omits RAG/MCP and adds audit and
per-user policy middlewares plus the demo tool. Permission decisions are
observable in the service console while the existing Web UI is reused
unchanged.
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
from agentscope.middleware import MiddlewareBase
from agentscope.tool import ToolBase

# examples/permission_audit_service/ is not a package; import siblings.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_middleware import (  # noqa: E402
    PermissionAuditMiddleware,
    console_audit_sink,
)
from demo_tool import PermissionAuditDemoTool  # noqa: E402
from user_tool_policy import UserToolPolicyMiddleware  # noqa: E402


RESTRICTED_USER_ID = os.getenv(
    "PERMISSION_AUDIT_RESTRICTED_USER_ID",
    "restricted-user",
)
DENIED_TOOLS_BY_USER = {
    RESTRICTED_USER_ID: {PermissionAuditDemoTool.name},
}


async def permission_middlewares_factory(
    user_id: str,
    agent_id: str,
    session_id: str,
) -> list[MiddlewareBase]:
    """Build per-request audit and application-policy middlewares."""
    return [
        # Keep audit outermost so it records the decision returned by the
        # complete permission middleware chain.
        PermissionAuditMiddleware(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            sink=console_audit_sink,
        ),
        UserToolPolicyMiddleware(
            user_id=user_id,
            denied_tools_by_user=DENIED_TOOLS_BY_USER,
        ),
    ]


async def permission_audit_demo_tools(
    _user_id: str,
    _agent_id: str,
    _session_id: str,
) -> list[ToolBase]:
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
    extra_agent_middlewares=permission_middlewares_factory,
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
