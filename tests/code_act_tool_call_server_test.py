# -*- coding: utf-8 -*-
"""CodeActRunToolServe tests."""
import asyncio
import socket
from unittest.async_case import IsolatedAsyncioTestCase
import httpx

from agentscope.codeact.code_act_tool_call_server import CodeActToolCallServer
from agentscope.message import TextBlock
from agentscope.tool import ToolResponse
from agentscope.tool._toolkit import Toolkit


class TestCodeActRunToolServer(IsolatedAsyncioTestCase):
    """Test the CodeActRunToolServer."""

    @staticmethod
    def _is_port_in_use(port: int, timeout: int = 1) -> bool:
        """Check if a port is in use (occupied) or not (free)"""
        # AF_INET = IPv4, SOCK_STREAM = TCP
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)  # Prevent hanging if port is blocked
            in_use = s.connect_ex(("0.0.0.0", port)) == 0
            return in_use

    @staticmethod
    def _create_dummy_toolkit() -> Toolkit:
        def succeed_with_compelete_metadata(txt: str) -> ToolResponse:
            """ metadata is complete. succeeded. valid structured output"""
            return ToolResponse(
                content=[TextBlock(type="text", text=f"foo {txt}")],
                metadata={
                    'success': True,
                    'structured_output': {'key1':'some_value', 'key2': 123}
                }
            )

        def fail_with_compelete_metadata(value: int) -> ToolResponse:
            return ToolResponse(
                content=[TextBlock(type="text", text=str(2 * value))],
                metadata={
                    'success': False,
                    'structured_output': {'you_wont_see_me': 123}
                }
            )

        def succeed_with_empty_structured_output(value: float) -> ToolResponse:
            return ToolResponse(
                content=[TextBlock(type="text", text=str(3.0 * value))],
                metadata={
                    'success': True,
                    'structured_output': {}
                }
            )

        def succeed_without_structured_output(value: float) -> ToolResponse:
            return ToolResponse(
                content=[TextBlock(type="text", text=str(3.0 * value))],
                metadata={
                    'success': True
                }
            )

        def no_metadata_in_response() -> ToolResponse:
            return ToolResponse(
                content=[TextBlock(type="text", text="metadata is not set")]
            )

        def always_interrupted(txt: str) -> ToolResponse:
            return ToolResponse(
                content=[TextBlock(type="text", text="metadata is not set")],
                is_interrupted=True
            )

        tk = Toolkit()
        tk.register_tool_function(tool_func=succeed_with_compelete_metadata)
        tk.register_tool_function(tool_func=fail_with_compelete_metadata)
        tk.register_tool_function(tool_func=succeed_with_empty_structured_output)
        tk.register_tool_function(tool_func=succeed_without_structured_output)
        tk.register_tool_function(tool_func=no_metadata_in_response)
        tk.register_tool_function(tool_func=always_interrupted)
        print(f"toolkit json schemas:{tk.get_json_schemas()}")
        return tk

    @classmethod
    def setUpClass(cls) -> None:
        """Set up the test environment."""
        port = 0
        for p in range(1024, 49152):
            if not cls._is_port_in_use(p):
                port = p
                break

        cls._port = port
        toolkit = cls._create_dummy_toolkit()
        cls._code_act_server = CodeActToolCallServer(port=port, toolkit=toolkit)
        asyncio.run(cls._code_act_server.start())

    async def _calling_tool_on_server(
        self,
        tool_name: str,
        tool_args: dict = None,
    ) -> dict:
        """Test general evaluator."""
        url = f"http://localhost:{self._port}/call_tool"
        payload = {"tool_name": tool_name, "tool_args": tool_args or {}}
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload)
            print(f'tool name:|{tool_name}|, args:|{tool_args}|, response.json:|{response.json()}|')
            return response.json()

    async def test_happy_case(self) -> None:
        """Tool call succeeded with structured output"""
        rs = await self._calling_tool_on_server("succeed_with_compelete_metadata", {"txt": "Foo Bar"})
        self.assertEqual(rs, {'key1':'some_value', 'key2': 123})

    async def test_failed_tool_call(self) -> None:
        """Tool call finished with success flag set to False"""
        rs = await self._calling_tool_on_server("fail_with_compelete_metadata", {"value": 3})
        self.assertEqual(rs, {})

    async def test_succeeded_tool_call_with_empty_structured_output(self) -> None:
        """Tool call finished successfully but with empty structured output"""
        rs = await self._calling_tool_on_server("succeed_with_empty_structured_output", {"value": 3})
        self.assertEqual(rs, {})

    async def test_succeeded_tool_call_without_structured_output(self) -> None:
        """Tool call finished successfully but without structured output attribute"""
        rs = await self._calling_tool_on_server("succeed_without_structured_output", {"value": 3.3})
        self.assertEqual(rs, {})

    async def test_tool_call_without_metadata_in_response(self) -> None:
        """Tool call finished but without metadata attribute"""
        rs = await self._calling_tool_on_server("no_metadata_in_response")
        self.assertEqual(rs, {})

    async def test_interrupted_tool_call(self) -> None:
        """Tool call is interrupted"""
        rs = await self._calling_tool_on_server("always_interrupted", {"txt": "Foo Bar"})
        self.assertTrue('tool call is interrupted.' in rs['detail'])
