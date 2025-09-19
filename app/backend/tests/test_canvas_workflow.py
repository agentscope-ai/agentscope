import os
import asyncio

import pytest
from httpx import AsyncClient

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_relation_zettel.db"

from app.main import app  # noqa: E402


@pytest.mark.asyncio
async def test_canvas_workflow(tmp_path):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/canvas/submit", json={"content": "测试输入"})
        assert response.status_code == 200
        data = response.json()
        assert data["candidate"]["id"].startswith("Rel_")

        relation_id = data["candidate"]["id"]

        verify = await client.post("/relations/decide", json={"ops": [{"id": relation_id, "action": "verify"}]})
        assert verify.status_code == 200
        undo_deadline = verify.json()["undo_expires_at"]
        assert undo_deadline is not None

        undo = await client.post("/relations/decide", json={"ops": [{"id": relation_id, "action": "undo"}]})
        assert undo.status_code == 200

        swap = await client.post(
            "/relations/swap",
            json={"id": relation_id, "subject": data["candidate"]["subject"], "predicate": "supports"},
        )
        assert swap.status_code == 200

        audit = await client.get(f"/audit/{relation_id}")
        assert audit.status_code == 200


@pytest.mark.asyncio
async def test_undo_after_deadline_expires(tmp_path):
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.post("/canvas/submit", json={"content": "测试输入"})
        assert response.status_code == 200
        relation_id = response.json()["candidate"]["id"]

        verify = await client.post("/relations/decide", json={"ops": [{"id": relation_id, "action": "verify"}]})
        assert verify.status_code == 200

        await asyncio.sleep(3.5)

        undo = await client.post("/relations/decide", json={"ops": [{"id": relation_id, "action": "undo"}]})
        assert undo.status_code == 200
        body = undo.json()
        assert relation_id in body["failed"]
        assert body["ok"] == 0
