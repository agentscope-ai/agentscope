# -*- coding: utf-8 -*-
"""Models router — model listing + active model switching.

GET  /models         — list all available models
POST /models/active   — switch the active model

This router exposes the :class:`ProviderManager` to the frontend.
The built-in AgentScope ``create_app`` already has a model router
(``/models``) backed by credentials; this router extends that with
the QwenPaw-style runtime switching.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

models_router = APIRouter(prefix="/models", tags=["models"])


class SetActiveModelRequest(BaseModel):
    """Request body for switching the active model."""

    provider_id: str = Field(description="Provider id to activate")
    model_name: str = Field(default="", description="Model name override")


@models_router.get("", summary="List all available models")
async def list_models(request: Request) -> list[dict]:
    """Return all registered providers/models."""
    pm = getattr(request.app.state, "provider_manager", None)
    if pm is None:
        return []
    return pm.list_models()


@models_router.post("/active", summary="Switch the active model")
async def set_active_model(
    body: SetActiveModelRequest,
    request: Request,
) -> dict:
    """Set the active provider+model for subsequent agent runs."""
    pm = getattr(request.app.state, "provider_manager", None)
    if pm is None:
        raise HTTPException(
            status_code=503,
            detail="ProviderManager not initialized",
        )
    ok = pm.set_active(body.provider_id, body.model_name)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Provider '{body.provider_id}' not found",
        )
    return {"ok": True, "provider_id": body.provider_id}


__all__ = ["models_router"]
