# -*- coding: utf-8 -*-
"""Self-contained backend entry point for AgentScope Desktop."""
import argparse
import os
import re
import secrets
import socket
import sys
import threading
from pathlib import Path
from typing import Any, Protocol, TextIO

import uvicorn
from fastapi import Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware import Middleware
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.responses import Response

from agentscope import setup_logger
from agentscope.app import create_app
from agentscope.app.deps import get_current_user_id
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.rag.blob_store import LocalBlobStore
from agentscope.app.rag.knowledge_base_manager import CollectionPerKbManager
from agentscope.app.storage import AsyncSQLAlchemyStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.rag import QdrantStore
from agentscope.tool._builtin._glob import _default_glob_helper_path
from agentscope.tool._builtin._scripts._glob_helper import (
    main as glob_helper_main,
)
from agentscope.workspace._base import (
    _EQUIP_PARTITION_SHIM,
    _EXTRACT_ARCHIVE_SHIM,
    _EXTRACT_TAR_SHIM,
    _MIGRATE_SKILLS_SHIM,
)

AUTH_TOKEN_ENV = "AGENTSCOPE_DESKTOP_TOKEN"
BACKEND_PORT_PREFIX = "AGENTSCOPE_BACKEND_PORT="
DESKTOP_SHUTDOWN_COMMAND = "AGENTSCOPE_DESKTOP_SHUTDOWN"
DESKTOP_USER_ID = "local-user"
DEFAULT_DATA_DIR = Path.home() / ".agentscope"
_DOCUMENT_DOWNLOAD_PATH = re.compile(
    r"^/knowledge_bases/[^/]+/documents/[^/]+$",
)
_PACKAGED_WORKSPACE_SHIMS = frozenset(
    {
        _EQUIP_PARTITION_SHIM,
        _EXTRACT_ARCHIVE_SHIM,
        _EXTRACT_TAR_SHIM,
        _MIGRATE_SKILLS_SHIM,
    },
)


class ShutdownTarget(Protocol):
    """Server state required by the desktop shutdown monitor."""

    should_exit: bool


def build_sqlite_url(database_path: Path) -> str:
    """Build a cross-platform async SQLite URL for an absolute path."""
    absolute_path = database_path.expanduser().resolve()
    return f"sqlite+aiosqlite:///{absolute_path.as_posix()}"


def build_packaged_tool_path(tool_dir: Path, current_path: str) -> str:
    """Prepend bundled command-line tools to an existing PATH value."""
    if current_path:
        return f"{tool_dir}{os.pathsep}{current_path}"
    return f"{tool_dir}"


def configure_packaged_tool_path() -> None:
    """Expose PyInstaller-bundled tools to local workspace processes."""
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        os.environ["PATH"] = build_packaged_tool_path(
            Path(bundle_dir),
            os.environ.get("PATH", ""),
        )


def configure_desktop_logging(data_dir: Path) -> None:
    """Write AgentScope logs to the desktop data directory."""
    resolved_data_dir = data_dir.expanduser().resolve()
    resolved_data_dir.mkdir(parents=True, exist_ok=True)
    setup_logger(
        "INFO",
        str(resolved_data_dir / "agentscope.log"),
    )


def run_packaged_helper(args: list[str]) -> bool:
    """Run an allowlisted workspace helper in the frozen backend."""
    if not getattr(sys, "frozen", False) or not args:
        return False

    original_argv = sys.argv
    try:
        if (
            len(args) >= 2
            and args[0] == "-c"
            and args[1] in _PACKAGED_WORKSPACE_SHIMS
        ):
            sys.argv = [original_argv[0], *args[2:]]
            # The source must exactly match an AgentScope-owned shim.
            exec(  # pylint: disable=exec-used
                args[1],
                {"__name__": "__main__"},
            )
            return True

        helper_path = Path(args[0]).resolve()
        expected_path = Path(_default_glob_helper_path()).resolve()
        if helper_path == expected_path:
            sys.argv = [args[0], *args[1:]]
            glob_helper_main()
            return True
    finally:
        sys.argv = original_argv

    return False


def monitor_shutdown_stream(
    stream: TextIO,
    target: ShutdownTarget,
) -> None:
    """Request graceful server exit after the Electron shutdown command."""
    for line in stream:
        if line.strip() == DESKTOP_SHUTDOWN_COMMAND:
            target.should_exit = True
            return
    target.should_exit = True


def is_signed_download_request(request: Request) -> bool:
    """Return whether a GET request uses an existing signed URL flow."""
    if request.method != "GET" or "token" not in request.query_params:
        return False
    path = request.url.path
    return path == "/workspace/files" or bool(
        _DOCUMENT_DOWNLOAD_PATH.fullmatch(path),
    )


class DesktopAuthMiddleware(BaseHTTPMiddleware):
    """Require the per-launch bearer token for desktop HTTP requests."""

    def __init__(self, app: Any, auth_token: str) -> None:
        """Initialize the middleware with the in-memory launch token."""
        super().__init__(app)
        self._expected_header = f"Bearer {auth_token}"

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Authenticate requests while preserving signed downloads."""
        if request.method == "OPTIONS" or is_signed_download_request(request):
            return await call_next(request)

        authorization = request.headers.get("authorization", "")
        if not secrets.compare_digest(
            authorization,
            self._expected_header,
        ):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid desktop authorization token."},
            )
        return await call_next(request)


def create_desktop_app(data_dir: Path, auth_token: str) -> Any:
    """Create the authenticated desktop application with local storage."""
    if not auth_token:
        raise ValueError("Desktop authorization token must not be empty.")

    resolved_data_dir = data_dir.expanduser().resolve()
    resolved_data_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = resolved_data_dir / "workspaces"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    storage = AsyncSQLAlchemyStorage(
        build_sqlite_url(resolved_data_dir / "agentscope.db"),
        create_tables=False,
        auto_migrate=True,
    )
    knowledge_base_manager = CollectionPerKbManager(
        storage=storage,
        vector_store=QdrantStore(
            path=str(resolved_data_dir / "qdrant"),
        ),
    )
    middlewares = [
        Middleware(
            CORSMiddleware,
            allow_origins=[
                "null",
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ],
            allow_methods=["*"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-User-ID",
            ],
        ),
        Middleware(
            DesktopAuthMiddleware,
            auth_token=auth_token,
        ),
    ]
    app = create_app(
        storage=storage,
        message_bus=InMemoryMessageBus(),
        workspace_manager=LocalWorkspaceManager(
            basedir=str(workspace_dir),
        ),
        knowledge_base_manager=knowledge_base_manager,
        blob_store=LocalBlobStore(resolved_data_dir / "blobs"),
        enable_index_worker=True,
        extra_middlewares=middlewares,
    )

    async def desktop_user_id() -> str:
        """Return the fixed identity after middleware authentication."""
        return DESKTOP_USER_ID

    app.dependency_overrides[get_current_user_id] = desktop_user_id
    return app


def create_server_socket(port: int) -> socket.socket:
    """Bind the loopback server socket before reporting its port."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("127.0.0.1", port))
    server_socket.listen(2048)
    return server_socket


def parse_args() -> argparse.Namespace:
    """Parse desktop backend command-line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Loopback port. Zero lets the operating system select one.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory for the SQLite database and workspaces.",
    )
    return parser.parse_args()


def main() -> None:
    """Start the desktop backend on a pre-bound loopback socket."""
    if run_packaged_helper(sys.argv[1:]):
        return
    configure_packaged_tool_path()
    args = parse_args()
    configure_desktop_logging(args.data_dir)
    auth_token = os.environ.pop(AUTH_TOKEN_ENV, "")
    if not auth_token:
        raise RuntimeError(f"{AUTH_TOKEN_ENV} must be set by Electron.")

    app = create_desktop_app(args.data_dir, auth_token)
    server_socket = create_server_socket(args.port)
    selected_port = server_socket.getsockname()[1]
    print(f"{BACKEND_PORT_PREFIX}{selected_port}", flush=True)

    config = uvicorn.Config(app, log_level="info")
    server = uvicorn.Server(config)
    threading.Thread(
        target=monitor_shutdown_stream,
        args=(sys.stdin, server),
        name="desktop-shutdown-monitor",
        daemon=True,
    ).start()
    server.run(sockets=[server_socket])


if __name__ == "__main__":
    main()
