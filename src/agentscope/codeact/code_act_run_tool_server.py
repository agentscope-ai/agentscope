# -*- coding: utf-8 -*-
"""HTTP server for running CodeAct tools."""

import ast
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


class RunToolRequest(BaseModel):
    """Request model for running a tool."""

    tool_name: str
    tool_args: dict


class CodeActRunToolServer:
    """An HTTP server that accepts run-tool requests, calls the tool, and returns the result."""

    def __init__(self, port: int, toolkit: Toolkit):
        if not port or port <= 0:
            raise ValueError(
                "CodeActRunToolServer missing a positive port argument"
            )
        if not toolkit:
            raise ValueError("CodeActRunToolServer missing toolkit argument")

        self._port = port
        self._toolkit = toolkit
        self.code_act_server_thread = None

    async def start(self):
        """Launch the server."""

        app = FastAPI(
            title="AgentScope CodeAct Run Tool Server",
            version="1.0",
            description=(
                "An HTTP server that accepts run-tool requests, "
                "calls the actual tool, and returns the result."
            ),
        )

        @app.post("/run_tool")
        async def run_tool(requst: RunToolRequest) -> JSONResponse:
            """Handle a run-tool request by calling the tool in toolkit."""

            try:
                request_id = uuid.uuid4().hex
                logger.info(
                    "/run_tool, request_id:%s, tool_name:%s, tool_args:%s",
                    request_id,
                    requst.tool_name,
                    requst.tool_args,
                )

                tool_use_block = ToolUseBlock(
                    type="tool_use",
                    id=request_id,
                    name=requst.tool_name,
                    input=requst.tool_args or {},
                )

                # NOTE: For now assuming the tool only returns once,
                # and returns a TextBlock
                # TODO: support generator style results
                result = await self._toolkit.call_tool_function(
                    tool_use_block
                )

                result_text = ""
                # ToolResponse object
                async for tool_response in result:
                    if (
                        tool_response.content
                        and tool_response.content[0]["text"]
                    ):
                        result_text = tool_response.content[0]["text"]
                        break

                try:
                    # TODO: use output schema if available
                    tool_call_result = ast.literal_eval(result_text)
                    tool_call_result_type = type(tool_call_result).__name__

                    if tool_call_result_type == "set":
                        # json cannot handle set object like {'a', 'b'}.
                        # Return a list to caller
                        tool_call_result = list(tool_call_result)
                        tool_call_result_type = type(
                            tool_call_result
                        ).__name__
                        logger.info(
                            "/run_tool, request_id:%s, "
                            "casted set to list, %s",
                            request_id,
                            tool_call_result,
                        )

                    logger.info(
                        "/run_tool, request_id:%s, "
                        "casted result_text |%s| to |%s| "
                        "of type %s",
                        request_id,
                        result_text,
                        tool_call_result,
                        tool_call_result_type,
                    )
                    return JSONResponse(
                        content={
                            "result": tool_call_result,
                            "type": tool_call_result_type,
                        },
                        status_code=200,
                    )
                except Exception:
                    logger.warning(
                        "/run_tool, request_id:%s, "
                        "return text type directly |%s|",
                        request_id,
                        result_text,
                        exc_info=True,
                    )
                    return JSONResponse(
                        content={"result": result_text, "type": "str"},
                        status_code=200,
                    )
            except Exception as e:
                error_msg = (
                    "/run_tool, failed to call tool, "
                    f"name:{requst.tool_name}, "
                    f"args:{requst.tool_args}, "
                    f"request_id:{request_id}, {str(e)}"
                )
                logger.error("%s", error_msg)
                raise HTTPException(
                    status_code=500, detail=error_msg
                ) from e

        @app.get("/heartbeat")
        async def heartbeat() -> JSONResponse:
            """Return a heartbeat."""
            try:
                if self._toolkit.get_json_schemas():
                    return JSONResponse(
                        content={"status": "ok"}, status_code=200
                    )

                raise ValueError("cannot find any active tool")
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"CodeActRunToolServer is not functional, "
                        f"{str(e)}"
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
            "CodeActRunToolServer is running on %s:%s",
            host,
            self._port,
        )
