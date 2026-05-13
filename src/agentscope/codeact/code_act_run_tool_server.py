# -*- coding: utf-8 -*-

import ast
import logging
import uvicorn
import asyncio
from threading import Thread
import uuid
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from ..message._message_block import ToolUseBlock
from ..tool._toolkit import Toolkit


logger = logging.getLogger(__name__)


class RunToolRequest(BaseModel):
    tool_name: str
    tool_args: dict


class CodeActRunToolServer:
    """An http server, accept run tool request, call the actual tool, and return the tool call result."""

    def __init__(self, port: int, toolkit: Toolkit):
        """"""
        if not port or port <= 0:
            raise ValueError(
                "CodeActRunToolServer missing a positive port argument"
            )
        if not toolkit:
            raise ValueError("CodeActRunToolServer missing toolkit argument")

        self._port = port
        self._toolkit = toolkit

    async def start(self):
        """Launch the server."""

        app = FastAPI(
            title="AgentScope CodeAct Run Tool Server",
            version="1.0",
            description="An http server, accept run tool request, call the actual tool, and return the tool call result.",
        )

        @app.post("/run_tool")
        async def run_tool(requst: RunToolRequest) -> JSONResponse:
            """Handle a run-tool request by calling the tool in toolkit"""

            try:
                request_id = uuid.uuid4().hex
                logger.info(
                    f"/run_tool, request_id:{request_id}, tool_name:{requst.tool_name}, tool_args:{requst.tool_args}"
                )

                tool_use_block = ToolUseBlock(
                    type="tool_use",
                    id=request_id,
                    name=requst.tool_name,
                    input=requst.tool_args or {},
                )

                # NOTE: For now assuming the tool only returns once, and returns a TextBlock
                # TODO: support generator style results
                result = await self._toolkit.call_tool_function(tool_use_block)

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
                        tool_call_result_type = type(tool_call_result).__name__
                        logger.info(
                            f"/run_tool, request_id:{request_id}, casted set to list, {tool_call_result}"
                        )

                    logger.info(
                        f"/run_tool, request_id:{request_id}, casted result_text |{result_text}| to |{tool_call_result}| of type {tool_call_result_type}"
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
                        f"/run_tool, request_id:{request_id}, return text type directly |{result_text}|",
                        exc_info=True,
                    )
                    return JSONResponse(
                        content={"result": result_text, "type": "str"},
                        status_code=200,
                    )
            except Exception as e:
                error_msg = f"/run_tool, failed to call tool, name:{requst.tool_name}, args:{requst.tool_args}, request_id:{request_id}, {str(e)}"
                logger.error(error_msg)
                raise HTTPException(status_code=500, detail=error_msg)

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
                    detail=f"CodeActRunToolServer is not functional, {str(e)}",
                )

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
        logger.info(f"CodeActRunToolServer is running on {host}:{self._port}")
