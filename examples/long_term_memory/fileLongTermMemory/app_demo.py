# -*- coding: utf-8 -*-
"""AgentScope app service with workspace-scoped file LTM enabled.

Start Redis first, then run:

    python app_demo.py

The standard AgentScope service endpoints are exposed on port 8000. This
version uses local workspaces, with one workspace directory per service Agent.

Requires:
    pip install "agentscope[service,storage,workspace]"
    docker run --rm -p 6379:6379 redis:7

Create model credentials and a session through the normal AgentScope service
API or Web UI after the server starts. The example focuses on extension-point
wiring: AgentScope still owns agent assembly, authentication, persistence,
message delivery, and workspace lifecycle.

File LTM does not configure a separate chat model. Each service Agent already
has a model selected through its normal credentials/configuration, and static
memory extraction reuses that Agent model.
"""
import os
from pathlib import Path

import uvicorn
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware

from agentscope.app import create_app
from agentscope.app.message_bus import RedisMessageBus
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.middleware import FileLongTermMemoryMiddleware


# LocalWorkspaceManager creates one directory below this root for each service
# agent. USER.md and MEMORY.md therefore follow the same agent/workspace scope.
WORKSPACES_DIR = Path(__file__).with_name("app_workspaces")

# Redis backs both durable service state and cross-process message delivery.
# Environment overrides make the demo usable with a remote Redis instance.
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# One shared instance is intentional. ChatService resolves a workspace for the
# active agent and passes it as Agent.offloader. The middleware keys stores,
# locks, and snapshots by workspace ID. Both factories below must bind this
# same object so state-injected tools can resolve the store registered by the
# middleware hooks.
#
# No chat model is passed here. Static extraction reuses the model belonging
# to whichever service Agent is currently running the middleware hook.
file_ltm = FileLongTermMemoryMiddleware(
    mode="both",
    extraction_interval=8,
)


async def extra_agent_middlewares(
    user_id: str,
    agent_id: str,
    session_id: str,
) -> list:
    """Attach file LTM hooks to every service-assembled Agent.

    The extension signature includes user, agent, and session identifiers for
    applications that need custom policy. File LTM obtains its isolation from
    the runtime workspace, so this demo does not consume those values.
    """
    del user_id, agent_id, session_id
    return [file_ltm]


async def extra_agent_tools(
    user_id: str,
    agent_id: str,
    session_id: str,
) -> list:
    """Expose tools bound to the shared middleware registry.

    Tools receive the live AgentState from the toolkit at execution time. The
    middleware maps that session to the workspace resolved by its hooks, which
    keeps concurrent agents isolated without creating per-request tool types.
    """
    del user_id, agent_id, session_id
    return await file_ltm.list_tools()


# create_app assembles the FastAPI application and calls the two factories
# above whenever it builds an Agent. The selected WorkspaceManager initializes
# and closes workspaces supplied through Agent.offloader.
app = create_app(
    storage=RedisStorage(host=REDIS_HOST, port=REDIS_PORT),
    message_bus=RedisMessageBus(host=REDIS_HOST, port=REDIS_PORT),
    workspace_manager=LocalWorkspaceManager(basedir=str(WORKSPACES_DIR)),
    extra_agent_middlewares=extra_agent_middlewares,
    extra_agent_tools=extra_agent_tools,
    extra_middlewares=[
        # CORS is intentionally permissive for a local demo. Production
        # deployments should restrict allowed origins and methods.
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ],
)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
