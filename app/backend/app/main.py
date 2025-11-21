import asyncio
from contextlib import suppress

from fastapi import FastAPI

from .routers import canvas, relations, review, audit, metrics, config, notes
from .services.relations import finalize_pending_loop


def create_app() -> FastAPI:
    app = FastAPI(title="Relation-Zettel Service", version="0.1.0")

    app.include_router(canvas.router)
    app.include_router(relations.router)
    app.include_router(review.router)
    app.include_router(audit.router)
    app.include_router(metrics.router)
    app.include_router(config.router)
    app.include_router(notes.router)

    @app.on_event("startup")
    async def _start_relations_finalizer():
        app.state.relations_finalizer = asyncio.create_task(finalize_pending_loop())

    @app.on_event("shutdown")
    async def _stop_relations_finalizer():
        task = getattr(app.state, "relations_finalizer", None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    return app


app = create_app()
