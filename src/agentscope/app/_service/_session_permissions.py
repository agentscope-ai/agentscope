# -*- coding: utf-8 -*-
"""Resolve session policy before chat or realtime execution."""
from ..storage import ScheduleOrigin, SessionRecord, SOPOrigin, StorageBase


async def refresh_session_permissions(
    storage: StorageBase,
    user_id: str,
    session: SessionRecord,
) -> None:
    """Resolve the original SOP inputs or the current schedule bounds."""
    if isinstance(session.origin, SOPOrigin):
        run = await storage.get_sop_run(user_id, session.origin.sop_run_id)
        if run is None:
            session.state.authorization_evidence_complete = False
        else:
            session.state.retain_authorization(run.state.inputs)
        return
    if not isinstance(session.origin, ScheduleOrigin):
        return
    schedule = await storage.get_schedule(user_id, session.origin.schedule_id)
    if schedule is not None:
        schedule.apply_to_session(session.state)
    else:
        session.state.schedule_authorization = None
        session.state.permission_context.interactive = False
