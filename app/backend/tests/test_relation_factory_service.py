import json
import os
from unittest.mock import AsyncMock

import pytest

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_relation_zettel.db"

from app.config import get_settings
from app.db import models
from app.db.session import SessionLocal, engine
from app.schemas.relations import CanvasSubmitRequest
from app.services.fts import ensure_fts, rebuild_fts
from app.services import provider as provider_module
from app.services.provider import CallStats
from app.services.relation_factory import _fts_best_match, run_relation_factory


@pytest.fixture(autouse=True)
async def prepare_database():
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.drop_all)
        await conn.run_sync(models.Base.metadata.create_all)
    await ensure_fts(engine)
    yield


@pytest.fixture
async def session():
    async with SessionLocal() as db:
        yield db


async def _seed_notes(db, subject_id: str = "note-subject", object_id: str = "note-object"):
    subject_note = models.Note(
        id=subject_id,
        title="Subject",
        content="Alpha beta gamma",
        tags="",
    )
    object_note = models.Note(
        id=object_id,
        title="Object",
        content="Magic keyword content for matching",
        tags="",
    )
    db.add_all([subject_note, object_note])
    await db.commit()
    await rebuild_fts(engine)
    return subject_note.id, object_note.id


@pytest.mark.asyncio
async def test_fts_best_match_returns_hit(session):
    subject_id, other_id = await _seed_notes(session)

    hit = await _fts_best_match(session, subject_id, "magic keyword")

    assert hit is not None
    note_id, snippet = hit
    assert note_id == other_id
    assert isinstance(snippet, str)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_name, helper_name",
    [
        ("dashscope", "_call_dashscope"),
        ("anthropic", "_call_anthropic"),
        ("gemini", "_call_gemini"),
        ("ollama", "_call_ollama"),
    ],
)
async def test_run_relation_factory_uses_non_openai_providers(
    session, monkeypatch, provider_name, helper_name
):
    subject_id, other_id = await _seed_notes(session)

    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "claim": f"{provider_name} claim",
                            "reason": "Stubbed reasoning",
                            "evidence": [
                                {
                                    "note": subject_id,
                                    "span": "L1-L2",
                                    "quote": "Alpha beta gamma",
                                },
                                {
                                    "note": other_id,
                                    "span": "L3-L4",
                                    "quote": "Magic keyword content for matching",
                                },
                            ],
                        }
                    )
                }
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }
    stats = CallStats(
        model="stub-model",
        prompt_tokens=12,
        completion_tokens=8,
        total_tokens=20,
        cost_cents=0,
    )

    helper_mock = AsyncMock(return_value=(payload, stats))
    monkeypatch.setattr(provider_module, helper_name, helper_mock)

    monkeypatch.setenv("LLM_PROVIDER", provider_name)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    get_settings.cache_clear()

    request = CanvasSubmitRequest(
        content="Magic keyword ties the notes together",
        note_id=subject_id,
        predicate="supports",
    )

    try:
        candidate = await run_relation_factory(session, request)
    finally:
        get_settings.cache_clear()

    assert candidate is not None
    assert candidate.claim == f"{provider_name} claim"
    assert candidate.evidence[1].note == other_id
    assert helper_mock.await_count == 1
