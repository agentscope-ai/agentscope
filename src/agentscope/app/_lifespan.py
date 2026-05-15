# -*- coding: utf-8 -*-
"""The lifespan of the agent service."""
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from ._manager import BackgroundTaskManager, SessionManager

if TYPE_CHECKING:
    from fastapi import FastAPI
else:
    FastAPI = Any


@asynccontextmanager
async def lifespan(app: FastAPI) -> None:
    async with app.state.storage:
        app.state.session_manager = SessionManager()
        app.state.background_task_manager = BackgroundTaskManager()

        yield

        app.state.session_manager.cancel()
        app.state.background_task_manager.cancel()
