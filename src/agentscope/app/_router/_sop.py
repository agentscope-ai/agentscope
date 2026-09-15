# -*- coding: utf-8 -*-
"""SOP router — procedures, their runs, and the verdicts people file.

A run advances on its own as far as it can and then stops for someone:
a person to judge a step, or a tool call to be approved in one of its
sessions. Only the first is answered here; the second goes through
``POST /chat`` like any other tool call, because a step's session is an
ordinary session. Neither needs an endpoint to restart the run —
answering is what restarts it.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..deps import (
    get_current_user_id,
    get_sop_service,
    get_storage,
)
from ._schema import (
    CreateSOPRequest,
    CreateSOPResponse,
    ListSOPRunsResponse,
    ListSOPsResponse,
    SOPSchemaResponse,
    StartSOPRunRequest,
    SubmitVerdictRequest,
    UpdateSOPRequest,
)
from .._service import SOPService
from ..storage import (
    SOPData,
    SOPRecord,
    SOPRunRecord,
    StorageBase,
)
from ...sop import SOPPhase

sop_router = APIRouter(
    prefix="/sop",
    tags=["sop"],
    responses={404: {"description": "Not found"}},
)


async def _require_sop(
    storage: StorageBase,
    user_id: str,
    sop_id: str,
) -> SOPRecord:
    """Fetch a procedure the caller owns, or answer 404."""
    record = await storage.get_sop(user_id, sop_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP {sop_id!r} not found.",
        )
    return record


async def _require_run(
    storage: StorageBase,
    user_id: str,
    sop_run_id: str,
) -> SOPRunRecord:
    """Fetch a run the caller owns, or answer 404."""
    record = await storage.get_sop_run(user_id, sop_run_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP run {sop_run_id!r} not found.",
        )
    return record


@sop_router.get(
    "/schema",
    response_model=SOPSchemaResponse,
    summary="The schema a procedure's editor is built from",
)
async def get_sop_schema() -> SOPSchemaResponse:
    """Return :class:`SOPData`'s JSON Schema.

    Left as pydantic emits it, ``$defs`` and all. The agent editor's
    schema is flattened because some model providers choke on ``$ref``,
    but nothing here is shown to a model — and a verifier is a tagged
    union whose ``discriminator.mapping`` points into ``$defs``, so
    inlining the variants would leave that mapping pointing at
    definitions no longer there.

    Returns:
        `SOPSchemaResponse`:
            The schema.
    """
    return SOPSchemaResponse(schema=SOPData.model_json_schema())


@sop_router.get(
    "/",
    response_model=ListSOPsResponse,
    summary="List procedures",
)
async def list_sops(
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> ListSOPsResponse:
    """Return the caller's procedures.

    Args:
        user_id (`str`):
            Injected authenticated user id.
        storage (`StorageBase`):
            Injected storage backend.

    Returns:
        `ListSOPsResponse`:
            Every procedure the caller owns.
    """
    records = await storage.list_sops(user_id)
    return ListSOPsResponse(sops=records, total=len(records))


@sop_router.post(
    "/",
    response_model=CreateSOPResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a procedure",
)
async def create_sop(
    body: CreateSOPRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> CreateSOPResponse:
    """Store a new procedure.

    Args:
        body (`CreateSOPRequest`):
            The procedure to store.
        user_id (`str`):
            Injected authenticated user id.
        storage (`StorageBase`):
            Injected storage backend.

    Returns:
        `CreateSOPResponse`:
            The server-assigned identifier.

    Raises:
        `HTTPException`:
            422 if a step names a conversation the procedure never
            configured — a run of it could not open that session.
    """
    _reject_unconfigured_sessions(body.data)
    record = SOPRecord(user_id=user_id, data=body.data)
    await storage.upsert_sop(user_id, record)
    return CreateSOPResponse(sop_id=record.id)


@sop_router.get(
    "/runs",
    response_model=ListSOPRunsResponse,
    summary="List runs",
)
async def list_sop_runs(
    sop_id: str
    | None = Query(
        default=None,
        description="Only runs of this procedure.",
    ),
    phase: SOPPhase
    | None = Query(
        default=None,
        description="Only runs standing here — ``awaiting`` is the one "
        "worth asking for.",
    ),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> ListSOPRunsResponse:
    """Return the caller's runs, newest first.

    Args:
        sop_id (`str | None`):
            Narrow to one procedure.
        phase (`SOPPhase | None`):
            Narrow to one phase.
        user_id (`str`):
            Injected authenticated user id.
        storage (`StorageBase`):
            Injected storage backend.

    Returns:
        `ListSOPRunsResponse`:
            The matching runs.
    """
    records = await storage.list_sop_runs(user_id, sop_id, phase)
    return ListSOPRunsResponse(runs=records, total=len(records))


@sop_router.get(
    "/runs/{sop_run_id}",
    response_model=SOPRunRecord,
    summary="Read one run",
)
async def get_sop_run(
    sop_run_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> SOPRunRecord:
    """Return one run and where it stands.

    Args:
        sop_run_id (`str`):
            The run to read.
        user_id (`str`):
            Injected authenticated user id.
        storage (`StorageBase`):
            Injected storage backend.

    Returns:
        `SOPRunRecord`:
            The run. Where it stands is its state's ``phase``.
    """
    return await _require_run(storage, user_id, sop_run_id)


@sop_router.delete(
    "/runs/{sop_run_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a run",
)
async def delete_sop_run(
    sop_run_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> None:
    """Delete one run and the conversations it opened.

    The conversations go too: the run minted every one of them, and left
    behind they are sessions nobody opened, still wakeable by a
    background tool finishing long after the run is gone.

    Args:
        sop_run_id (`str`):
            The run to delete.
        user_id (`str`):
            Injected authenticated user id.
        storage (`StorageBase`):
            Injected storage backend.
    """
    if not await storage.delete_sop_run(user_id, sop_run_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP run {sop_run_id!r} not found.",
        )


@sop_router.post(
    "/runs/{sop_run_id}/verdict",
    response_model=SOPRunRecord,
    summary="File a person's verdict on a step",
)
async def submit_verdict(
    sop_run_id: str,
    body: SubmitVerdictRequest,
    user_id: str = Depends(get_current_user_id),
    service: SOPService = Depends(get_sop_service),
) -> SOPRunRecord:
    """Answer a step that was waiting on a person.

    Filed through the same tool an agent reviewer calls, so a verdict is
    one thing however it was reached. Answers as soon as the verdict is
    recorded: carrying the run on from it can take the rest of the
    procedure, which is no more this request's business than a chat
    turn's events are ``POST /chat``'s.

    Args:
        sop_run_id (`str`):
            The run being judged.
        body (`SubmitVerdictRequest`):
            Which step, and what was decided.
        user_id (`str`):
            Injected authenticated user id.
        service (`SOPService`):
            Injected SOP service.

    Returns:
        `SOPRunRecord`:
            The run with the verdict recorded on it.

    Raises:
        `HTTPException`:
            404 if there is no such run, and 409 if that step is not one
            a person was asked to judge, or is not waiting to be.
    """
    try:
        updated = await service.record_verdict(
            user_id,
            sop_run_id,
            body.step_index,
            body.passed,
            body.message,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc).strip("'"),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    service.advance_later(user_id, sop_run_id)
    return updated


@sop_router.get(
    "/{sop_id}",
    response_model=SOPRecord,
    summary="Read one procedure",
)
async def get_sop(
    sop_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> SOPRecord:
    """Return one procedure.

    Args:
        sop_id (`str`):
            The procedure to read.
        user_id (`str`):
            Injected authenticated user id.
        storage (`StorageBase`):
            Injected storage backend.

    Returns:
        `SOPRecord`:
            The procedure.
    """
    return await _require_sop(storage, user_id, sop_id)


@sop_router.patch(
    "/{sop_id}",
    response_model=SOPRecord,
    summary="Replace a procedure's contents",
)
async def update_sop(
    sop_id: str,
    body: UpdateSOPRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> SOPRecord:
    """Rewrite a procedure, leaving its runs as they were.

    A run carries its own copy of the procedure, so editing one here
    never changes what a run already in flight is doing.

    Args:
        sop_id (`str`):
            The procedure to update.
        body (`UpdateSOPRequest`):
            The procedure as it should now read.
        user_id (`str`):
            Injected authenticated user id.
        storage (`StorageBase`):
            Injected storage backend.

    Returns:
        `SOPRecord`:
            The procedure as stored.

    Raises:
        `HTTPException`:
            422 if a step names a conversation the procedure never
            configured.
    """
    record = await _require_sop(storage, user_id, sop_id)
    _reject_unconfigured_sessions(body.data)
    record.data = body.data
    return await storage.upsert_sop(user_id, record)


@sop_router.delete(
    "/{sop_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a procedure and its runs",
)
async def delete_sop(
    sop_id: str,
    user_id: str = Depends(get_current_user_id),
    service: SOPService = Depends(get_sop_service),
) -> None:
    """Delete a procedure and every run of it.

    The runs go too because a run is only readable through the
    definition it copied, and their sessions with them.

    Args:
        sop_id (`str`):
            The procedure to delete.
        user_id (`str`):
            Injected authenticated user id.
        service (`SOPService`):
            Injected SOP service.
    """
    if not await service.delete_sop(user_id, sop_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP {sop_id!r} not found.",
        )


@sop_router.post(
    "/{sop_id}/runs",
    response_model=SOPRunRecord,
    status_code=status.HTTP_201_CREATED,
    summary="Start a run",
)
async def start_sop_run(
    sop_id: str,
    body: StartSOPRunRequest,
    user_id: str = Depends(get_current_user_id),
    service: SOPService = Depends(get_sop_service),
) -> SOPRunRecord:
    """Open a run of a procedure and set it going.

    Returns as soon as the run exists — its conversations are opened
    first, so the client can watch them immediately, and the run itself
    proceeds in the background. Poll ``GET /sop/runs/{id}`` for where it
    got to.

    Args:
        sop_id (`str`):
            The procedure to run.
        body (`StartSOPRunRequest`):
            What the run is started with.
        user_id (`str`):
            Injected authenticated user id.
        service (`SOPService`):
            Injected SOP service.

    Returns:
        `SOPRunRecord`:
            The opened run, before it has got anywhere.
    """
    try:
        run = await service.create_run(user_id, sop_id, body.inputs)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP {sop_id!r} not found.",
        ) from exc
    service.advance_later(user_id, run.id)
    return run


def _reject_unconfigured_sessions(data: SOPData) -> None:
    """Refuse a procedure whose steps name conversations it never set up.

    Checked here rather than on the model because it is the one thing a
    stored procedure can say that makes a run of it impossible, and the
    editor should hear about it while it is still an edit.
    """
    named = {step.executor.session_key for step in data.steps}
    for step in data.steps:
        verifier = getattr(step.verifier, "agent", None)
        if verifier is not None:
            named.add(verifier.session_key)
    missing = sorted(named - set(data.session_settings))
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"These conversations are used but never configured: "
                f"{', '.join(missing)}."
            ),
        )
