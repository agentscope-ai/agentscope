# -*- coding: utf-8 -*-
"""HTTP server for running CodeAct tools."""
import asyncio
import logging
import uuid
from threading import Thread

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..message._message_block import ToolUseBlock
from ..tool._toolkit import Toolkit


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ToolCallRequest(BaseModel):
    """Request model for calling a tool."""

    tool_name: str
    tool_args: dict


class CodeActToolCallServer:
    """An HTTP server that accepts tool call requests, calls the tool,
    and returns the result."""

    def __init__(self, port: int, toolkit: Toolkit):
        if not port or port <= 0:
            raise ValueError(
                "CodeActToolCallServer missing a positive port argument",
            )
        if not toolkit:
            raise ValueError("CodeActToolCallServer missing toolkit argument")

        self._port = port
        self._toolkit = toolkit
        self.code_act_server_thread = None

    async def start(self) -> None:
        """Launch the server."""

        app = FastAPI(
            title="AgentScope CodeAct Call Tool Server",
            version="1.0",
            description=(
                "An HTTP server that accepts tool-call requests, "
                "calls the actual tool, and returns the result."
            ),
        )

        @app.post("/call_tool")
        async def call_tool(requst: ToolCallRequest) -> JSONResponse:
            """Handle a tool call request by calling the tool in toolkit."""

            try:
                request_id = uuid.uuid4().hex
                logger.info(
                    "tool call, request_id:%s, tool_name:%s, tool_args:%s",
                    request_id,
                    requst.tool_name,
                    requst.tool_args,
                )
                # result: async_generator
                result = await self._toolkit.call_tool_function(
                    ToolUseBlock(
                        type="tool_use",
                        id=request_id,
                        name=requst.tool_name,
                        input=requst.tool_args or {},
                    ),
                )

                # Refer to the tool call result handling logic of ReActAgent
                # ToolResponse object
                async for tool_response in result:
                    if tool_response.is_interrupted:
                        raise ValueError("tool call is interrupted.")

                    # Return message if generate_response is successful
                    if tool_response.metadata and tool_response.metadata.get(
                        "success",
                        False,
                    ):
                        # Only return the structured output
                        structured_output = tool_response.metadata.get(
                            "structured_output",
                            {},
                        )
                        return JSONResponse(content=structured_output)

                return JSONResponse(content={})
            except Exception as e:
                error_msg = (
                    "tool call failed, "
                    f"name:{requst.tool_name}, "
                    f"args:{requst.tool_args}, "
                    f"request_id:{request_id}, {str(e)}"
                )
                logger.error("%s", error_msg)
                raise HTTPException(
                    status_code=500,
                    detail=error_msg,
                ) from e

        @app.get("/heartbeat")
        async def heartbeat() -> JSONResponse:
            """Return a heartbeat."""
            try:
                if self._toolkit.get_json_schemas():
                    return JSONResponse(
                        content={"status": "ok"},
                        status_code=200,
                    )

                raise ValueError("cannot find any active tool")
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"CodeActToolCallServer is not functional, {str(e)}"
                    ),
                ) from e

        host = "0.0.0.0"

        self.code_act_server_thread = Thread(
            target=uvicorn.run,
            args=[app],
            kwargs={"host": host, "port": self._port, "access_log": True},
            daemon=True,
        )
        self.code_act_server_thread.start()

        # wait for the server to be fully started
        await asyncio.sleep(3)
        logger.info(
            "CodeActToolCallServer is running on %s:%s",
            host,
            self._port,
        )
