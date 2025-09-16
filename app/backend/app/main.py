from fastapi import FastAPI

from .routers import canvas, relations, review, audit, metrics, config, notes


def create_app() -> FastAPI:
    app = FastAPI(title="Relation-Zettel Service", version="0.1.0")

    app.include_router(canvas.router)
    app.include_router(relations.router)
    app.include_router(review.router)
    app.include_router(audit.router)
    app.include_router(metrics.router)
    app.include_router(config.router)
    app.include_router(notes.router)

    return app


app = create_app()
