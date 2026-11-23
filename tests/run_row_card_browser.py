# -*- coding: utf-8 -*-
"""Run the built Web UI against an isolated chat service and fake systems."""
import asyncio
import os
import socket
from contextlib import ExitStack, closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import uvicorn
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from business_row_references_test import BusinessRowReferencesTest
from utils import MockModel

from agentscope.app import create_app
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    AsyncSQLAlchemyStorage,
    ChatModelConfig,
    SessionConfig,
)
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.business import RowScope
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatResponse
from agentscope.tool import FunctionTool

FRONTEND = Path(__file__).resolve().parents[1] / "examples/web_ui/frontend"


async def run(directory: Path, stack: ExitStack) -> None:
    """Keep HTTP, SSE, SQLite, tools and browser on the same test server."""
    storage = AsyncSQLAlchemyStorage(
        url=f"sqlite+aiosqlite:///{directory.as_posix()}/chat.db",
    )
    fixtures = {}

    async def factory(
        user: str,
        agent: str,
        session: str,
    ) -> list[FunctionTool]:
        fixture = fixtures[session]
        source = (
            fixture.purchase
            if session.startswith("purchase")
            else fixture.ticket
        )
        fixture.viewer = RowScope(agent, "fake-tenant", user, session)
        rows = fixture.host(source)
        read = rows.list_rows

        async def page(owner: RowScope, limit: int) -> dict:
            if len(fixture.rows) == 2 and fixture.calls:
                fixture.rows.insert(
                    0,
                    dict(fixture.rows[0], id=3, ticketKey="T3"),
                )
            return await read(owner, limit)

        rows.list_rows = page
        return rows.tools()

    async def model_for(
        _user: str,
        config: ChatModelConfig,
        _access: object,
    ) -> MockModel:
        model = MockModel(context_size=32000)
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id=uuid4().hex,
                            name=f"query_recent_{config.model}",
                            input='{"limit":2}',
                        ),
                    ],
                    is_last=True,
                ),
                ChatResponse(content=[TextBlock(text="查询完成")], is_last=True),
            ],
        )
        return model

    stack.enter_context(
        patch("agentscope.app._service._chat.get_model", model_for),
    )
    app = create_app(
        storage=storage,
        message_bus=InMemoryMessageBus(),
        workspace_manager=LocalWorkspaceManager(str(directory)),
        extra_agent_tools=factory,
        enable_channel_worker=False,
        enable_scheduler=False,
        enable_index_worker=False,
    )
    dist = FRONTEND / "dist"
    app.mount("/assets", StaticFiles(directory=dist / "assets"))

    @app.get("/chat/{agent}/{session}")
    async def frontend() -> FileResponse:
        return FileResponse(dist / "index.html")

    with closing(socket.socket()) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Mock chat service failed to start")
                await asyncio.sleep(0.02)
            await storage.upsert_agent(
                "reader",
                AgentRecord(
                    id="reader",
                    user_id="reader",
                    data=AgentData(name="Reader"),
                ),
            )
            for system in ("purchase_receipts", "tickets"):
                for device in ("desktop", "mobile"):
                    session = f"{system}-{device}"
                    fixture = BusinessRowReferencesTest()
                    stack.callback(fixture.doCleanups)
                    fixture.setUp()
                    fixture.rows = [
                        {
                            "id": n,
                            "ticketKey": f"T{n}",
                            "billNo": "同号",
                            "subject": "同一工单",
                            "details": [f"detail-{n}"],
                        }
                        for n in (1, 2)
                    ]
                    fixtures[session] = fixture
                    await storage.upsert_session(
                        "reader",
                        "reader",
                        SessionConfig(
                            workspace_id=session,
                            name=session,
                            chat_model_config=ChatModelConfig(
                                type="test",
                                credential_id="simulated",
                                model=system,
                                parameters={},
                            ),
                        ),
                        session_id=session,
                    )
            browser = await asyncio.create_subprocess_exec(
                "node",
                "--test",
                "tests/row-cards.test.mjs",
                cwd=FRONTEND,
                env={
                    **os.environ,
                    "ROW_CARD_TEST_URL": f"http://127.0.0.1:{port}",
                },
            )
            if await browser.wait():
                raise RuntimeError("Row card browser regressions failed")
            for session, fixture in fixtures.items():
                expected: list = [
                    "/desk/inbox",
                    "/desk/inbox",
                    {"key": "T2"},
                    {"key": "T2"},
                ]
                if session.startswith("purchase"):
                    expected = ["/api/purchase/order/page"] * 2 + [
                        "/api/purchase/order/2",
                    ] * 2
                if fixture.calls != expected:
                    raise AssertionError((session, fixture.calls, expected))
        finally:
            server.should_exit = True
            await task


if __name__ == "__main__":
    with TemporaryDirectory() as tmp, ExitStack() as cleanup:
        asyncio.run(run(Path(tmp), cleanup))
