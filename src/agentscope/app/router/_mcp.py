# -*- coding: utf-8 -*-
"""MCP router for managing MCP service configurations."""
from fastapi import APIRouter, HTTPException

from .._schema import (
    MCPCreateRequest,
    MCPUpdateRequest,
    MCPResponse,
    MCPListResponse,
)


mcp_router = APIRouter(
    prefix="/mcp",
    tags=["mcp"],
    responses={404: {"description": "Not found"}},
)


@mcp_router.get(
    "/",
    response_model=MCPListResponse,
    summary="List all MCP configurations",
)
async def list_mcps() -> MCPListResponse:
    """Return a list of all stored MCP service configurations.

    Returns:
        `MCPListResponse`:
            The list of MCP configurations and the total count.
    """
    # TODO: query storage to retrieve all MCP records
    mcps: list[MCPResponse] = []
    return MCPListResponse(mcps=mcps, total=len(mcps))


@mcp_router.post(
    "/",
    response_model=MCPResponse,
    status_code=201,
    summary="Create a new MCP configuration",
)
async def create_mcp(body: MCPCreateRequest) -> MCPResponse:
    """Create and persist a new MCP service configuration.

    Args:
        body (`MCPCreateRequest`):
            The MCP configuration to create.

    Returns:
        `MCPResponse`:
            The created MCP configuration with server-generated metadata.

    Raises:
        `HTTPException`:
            409 if an MCP with the same name already exists.
        `HTTPException`:
            422 if the configuration is invalid.
    """
    try:
        body.validate_config()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # TODO: extract user_id from Depends(get_current_user)
    # TODO: check for name conflicts; raise HTTPException(status_code=409) if duplicate
    # TODO: persist the MCP configuration to storage with creator_id
    # TODO: return the created record
    raise HTTPException(status_code=501, detail="Not implemented")


@mcp_router.get(
    "/{mcp_name}",
    response_model=MCPResponse,
    summary="Get an MCP configuration",
)
async def get_mcp(mcp_name: str) -> MCPResponse:
    """Retrieve a single MCP service configuration by name.

    Args:
        mcp_name (`str`):
            The unique name of the MCP configuration to retrieve.

    Returns:
        `MCPResponse`:
            The full MCP record.

    Raises:
        `HTTPException`:
            404 if the MCP configuration does not exist.
    """
    # TODO: extract user_id from Depends(get_current_user)
    # TODO: check permission via Depends(get_permission_resolver)
    # TODO: load the MCP record from storage; raise HTTPException(status_code=404) if not found
    raise HTTPException(status_code=501, detail="Not implemented")


@mcp_router.delete(
    "/{mcp_name}",
    status_code=204,
    summary="Delete an MCP configuration",
)
async def delete_mcp(mcp_name: str) -> None:
    """Permanently delete an MCP service configuration.

    Args:
        mcp_name (`str`):
            The unique name of the MCP configuration to delete.

    Raises:
        `HTTPException`:
            404 if the MCP configuration does not exist.
        `HTTPException`:
            403 if the user does not have permission to delete.
    """
    # TODO: extract user_id from Depends(get_current_user)
    # TODO: verify the MCP record exists; raise HTTPException(status_code=404) if not found
    # TODO: check ownership or permission; raise HTTPException(status_code=403) if forbidden
    # TODO: delete the MCP record from storage


@mcp_router.patch(
    "/{mcp_name}",
    response_model=MCPResponse,
    summary="Update an MCP configuration",
)
async def update_mcp(
    mcp_name: str,
    body: MCPUpdateRequest,
) -> MCPResponse:
    """Partially update an existing MCP service configuration.

    Only the fields present in the request body are updated; all other fields
    keep their current values.

    Args:
        mcp_name (`str`):
            The unique name of the MCP configuration to update.
        body (`MCPUpdateRequest`):
            Fields to update.

    Returns:
        `MCPResponse`:
            The full MCP record after the update.

    Raises:
        `HTTPException`:
            404 if the MCP configuration does not exist.
        `HTTPException`:
            403 if the user does not have permission to update.
        `HTTPException`:
            422 if the updated configuration is invalid.
    """
    # TODO: extract user_id from Depends(get_current_user)
    # TODO: load existing MCP record; raise HTTPException(status_code=404) if not found
    # TODO: check ownership or permission; raise HTTPException(status_code=403) if forbidden
    # TODO: apply partial updates (merge body fields into existing record)
    # TODO: call validate_config() on the merged config; raise HTTPException(status_code=422) on error
    # TODO: update updated_at timestamp
    # TODO: persist the updated record to storage and return it

    # Placeholder – replaced once storage is wired in
    raise HTTPException(status_code=501, detail="Not implemented")
