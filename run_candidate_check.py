import asyncio, json, sys
sys.path.append('app/backend')
from app.db.session import engine, SessionLocal
from app.db import models
from app.schemas.relations import CanvasSubmitRequest
from app.services.relation_factory import run_relation_factory

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    async with SessionLocal() as session:
        req = CanvasSubmitRequest(content='测试GLM：小样本条件下的泛化', note_id='Z_demo', predicate='supports')
        candidate = await run_relation_factory(session, req)
        print('CANDIDATE:', json.dumps(candidate.model_dump(), ensure_ascii=False) if candidate else 'None')

asyncio.run(main())
