# -*- coding: utf-8 -*-
""""""
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from ._manager import BackgroundTaskManager, SessionManager

if TYPE_CHECKING:
    from fastapi import FastAPI
else:
    FastAPI = Any


@asynccontextmanager
async def lifespan(app: FastAPI) -> None:
    app.state.background_task_manager = BackgroundTaskManager()
    app.state.session_manager = SessionManager()

    yield

    app.state.background_task_manager.cancel()
    app.state.session_manager.cancel()
