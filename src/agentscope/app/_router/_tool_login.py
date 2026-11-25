"""Private form transport for service authentication."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..deps import get_current_user_id, get_storage
from ..storage import StorageBase
from ..tool_login import (
    LoginConflict,
    LoginEnded,
    LoginFailed,
    LoginRejected,
    LoginScope,
    LoginView,
    ToolLogin,
)

tool_login_router = APIRouter(
    prefix="/sessions/{session_id}/tool-login", tags=["tool-login"],
)


async def _owned_scope(
    session_id: str,
    agent_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> LoginScope:
    if await storage.get_session(user_id, agent_id, session_id) is None:
        raise HTTPException(404, "Session not found.")
    return user_id, agent_id, session_id


def _manager(request: Request) -> ToolLogin:
    manager = request.app.state.tool_login
    if manager is None:
        raise HTTPException(404, "Tool login is unavailable.")
    return manager


@tool_login_router.get("", response_model=LoginView | None)
async def get_tool_login(
    request: Request,
    response: Response,
    scope: LoginScope = Depends(_owned_scope),
) -> LoginView | None:
    """Get the current form after an event or page reload."""
    response.headers["Cache-Control"] = "no-store"
    manager = request.app.state.tool_login
    return await manager.pending_view(scope) if manager is not None else None


@tool_login_router.post("/{login_id}", status_code=204)
async def submit_tool_login(
    login_id: str,
    request: Request,
    scope: LoginScope = Depends(_owned_scope),
) -> Response:
    """Pass fields directly to the adapter without reflecting invalid input."""
    manager = _manager(request)
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Invalid login form.") from None
    if (
        not isinstance(body, dict)
        or type(body.get("revision")) is not int
        or not isinstance(body.get("values"), dict)
        or len(body["values"]) > 12
        or any(
            len(key) > 80 or not isinstance(value, str) or len(value) > 4096
            for key, value in body["values"].items()
        )
    ):
        raise HTTPException(400, "Invalid login form.")
    try:
        await manager.submit(scope, login_id, body["revision"], body["values"])
    except LoginRejected as exc:
        raise HTTPException(400, str(exc)) from None
    except LoginFailed as exc:
        raise HTTPException(422, str(exc)) from None
    except LoginConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except LoginEnded as exc:
        raise HTTPException(404, str(exc)) from None
    except Exception:
        # Adapter exceptions may contain upstream request or response data.
        raise HTTPException(502, "The service could not complete login.") from None
    return Response(status_code=204)


@tool_login_router.delete("/{login_id}", status_code=204)
async def cancel_tool_login(
    login_id: str,
    request: Request,
    scope: LoginScope = Depends(_owned_scope),
) -> Response:
    """Discard the pending form and end the waiting authentication."""
    try:
        await _manager(request).cancel(scope, login_id)
    except LoginEnded as exc:
        raise HTTPException(404, str(exc)) from None
    return Response(status_code=204)
