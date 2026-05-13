# -*- coding: utf-8 -*-
"""The model router."""


from fastapi import APIRouter

from .._schema import ListModelResponse, ListModelRequest

model_router = APIRouter(
    prefix="/model",
    tags=["model"],
    responses={404: {"description": "Not found"}},
)


@model_router.get(
    "/",
    response_model=ListModelResponse,
    summary="List all candidate models under the given credential type",
)
async def list_models(
    body: ListModelRequest,
) -> ListModelResponse:
    """Return all candidate models under the given credential type.


    Args:
        body (ListModelRequest): The request body.

    Returns:
        `ListModelResponse`: The response body.
    """

    return ListModelResponse()
