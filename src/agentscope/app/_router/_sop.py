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
    SOPRunResponse,
    SOPSchemaResponse,
    StartSOPRunRequest,
    SubmitVerdictRequest,
    UpdateSOPRequest,
)
from .._service import SOPService
from .._tool import SubmitVerdict
from ..storage import (
    HumanVerifier,
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
    response_model=SOPRunResponse,
    summary="Read one run",
)
async def get_sop_run(
    sop_run_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> SOPRunResponse:
    """Return one run and where it stands.

    Args:
        sop_run_id (`str`):
            The run to read.
        user_id (`str`):
            Injected authenticated user id.
        storage (`StorageBase`):
            Injected storage backend.

    Returns:
        `SOPRunResponse`:
            The run record and its derived phase.
    """
    record = await _require_run(storage, user_id, sop_run_id)
    return SOPRunResponse(run=record, phase=record.state.phase)


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
    """Delete one run.

    Its conversations are left alone: they are ordinary sessions and may
    hold work someone still wants to read.

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
    response_model=SOPRunResponse,
    summary="File a person's verdict on a step",
)
async def submit_verdict(
    sop_run_id: str,
    body: SubmitVerdictRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    service: SOPService = Depends(get_sop_service),
) -> SOPRunResponse:
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
        storage (`StorageBase`):
            Injected storage backend.
        service (`SOPService`):
            Injected SOP service.

    Returns:
        `SOPRunResponse`:
            The run with the verdict recorded on it.

    Raises:
        `HTTPException`:
            404 if there is no such run, and 409 if that step is not one
            a person was asked to judge, or is not waiting to be.
    """
    record = await _require_run(storage, user_id, sop_run_id)
    if body.step_index >= len(record.definition.steps):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Step {body.step_index} is not part of this run.",
        )
    step = record.definition.steps[body.step_index]
    if not isinstance(step.verifier, HumanVerifier):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Step {body.step_index} is not judged by a person.",
        )
    if record.state.steps[body.step_index].phase is not SOPPhase.AWAITING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Step {body.step_index} is not waiting to be judged.",
        )

    await SubmitVerdict(
        storage=storage,
        user_id=user_id,
        sop_run_id=sop_run_id,
        step_index=body.step_index,
        verifier=user_id,
    )(passed=body.passed, message=body.message)

    updated = await _require_run(storage, user_id, sop_run_id)
    service.advance_later(user_id, sop_run_id)
    return SOPRunResponse(run=updated, phase=updated.state.phase)


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
    storage: StorageBase = Depends(get_storage),
) -> None:
    """Delete a procedure and every run of it.

    The runs go too because a run is only readable through the
    definition it copied.

    Args:
        sop_id (`str`):
            The procedure to delete.
        user_id (`str`):
            Injected authenticated user id.
        storage (`StorageBase`):
            Injected storage backend.
    """
    if not await storage.delete_sop(user_id, sop_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"SOP {sop_id!r} not found.",
        )


@sop_router.post(
    "/{sop_id}/runs",
    response_model=SOPRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a run",
)
async def start_sop_run(
    sop_id: str,
    body: StartSOPRunRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    service: SOPService = Depends(get_sop_service),
) -> SOPRunResponse:
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
        storage (`StorageBase`):
            Injected storage backend.
        service (`SOPService`):
            Injected SOP service.

    Returns:
        `SOPRunResponse`:
            The opened run, before it has got anywhere.
    """
    record = await _require_sop(storage, user_id, sop_id)
    run = await service.create_run(user_id, record, body.inputs)
    service.advance_later(user_id, run.id)
    return SOPRunResponse(run=run, phase=run.state.phase)


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
