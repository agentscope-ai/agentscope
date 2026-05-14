# -*- coding: utf-8 -*-
"""CodeActRunToolServe tests."""
import asyncio
import socket
from unittest.async_case import IsolatedAsyncioTestCase
import httpx

from agentscope.codeact.code_act_run_tool_server import CodeActRunToolServer
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
        def str_func(txt: str) -> ToolResponse:
            return ToolResponse(
                content=[TextBlock(type="text", text=f"foo {txt}")],
            )

        def int_func(value: int) -> ToolResponse:
            return ToolResponse(
                content=[TextBlock(type="text", text=str(2 * value))],
            )

        def float_func(value: float) -> ToolResponse:
            return ToolResponse(
                content=[TextBlock(type="text", text=str(3.0 * value))],
            )

        def list_func() -> ToolResponse:
            return ToolResponse(
                content=[TextBlock(type="text", text="[1,2,3]")],
            )

        def dict_func() -> ToolResponse:
            return ToolResponse(
                content=[TextBlock(type="text", text="{'a':1}")],
            )

        def set_func() -> ToolResponse:
            return ToolResponse(
                content=[TextBlock(type="text", text='{"a", "b"}')],
            )

        def none_func() -> ToolResponse:
            return ToolResponse(content=[TextBlock(type="text", text="None")])

        tk = Toolkit()
        tk.register_tool_function(tool_func=str_func)
        tk.register_tool_function(tool_func=int_func)
        tk.register_tool_function(tool_func=float_func)
        tk.register_tool_function(tool_func=list_func)
        tk.register_tool_function(tool_func=dict_func)
        tk.register_tool_function(tool_func=set_func)
        tk.register_tool_function(tool_func=none_func)
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
        cls._code_act_server = CodeActRunToolServer(port=port, toolkit=toolkit)
        asyncio.run(cls._code_act_server.start())

    async def _calling_tool_on_server(
        self,
        tool_name: str,
        tool_args: dict = None,
    ) -> dict:
        """Test general evaluator."""
        url = f"http://localhost:{self._port}/run_tool"
        payload = {"tool_name": tool_name, "tool_args": tool_args or {}}
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload)
            return response.json()

    async def test_str_function(self) -> None:
        """Call str function"""
        rs = await self._calling_tool_on_server("str_func", {"txt": "Foo Bar"})
        self.assertEqual(rs["result"], "foo Foo Bar")
        self.assertEqual(rs["type"], "str")

    async def test_int_function(self) -> None:
        """Call int function"""
        rs = await self._calling_tool_on_server("int_func", {"value": 3})
        self.assertEqual(rs["result"], 6)
        self.assertEqual(rs["type"], "int")

    async def test_float_function(self) -> None:
        """Call float function"""
        rs = await self._calling_tool_on_server("float_func", {"value": 3})
        self.assertEqual(rs["result"], 9.0)
        self.assertEqual(rs["type"], "float")

    async def test_list_function(self) -> None:
        """Call list function"""
        rs = await self._calling_tool_on_server("list_func")
        self.assertEqual(rs["result"], [1, 2, 3])
        self.assertEqual(rs["type"], "list")

    async def test_dict_function(self) -> None:
        """Call dict function"""
        rs = await self._calling_tool_on_server("dict_func")
        self.assertEqual(rs["result"], {"a": 1})
        self.assertEqual(rs["type"], "dict")

    async def test_set_function(self) -> None:
        """Call set function"""
        rs = await self._calling_tool_on_server("set_func")
        self.assertTrue(
            rs["result"] == ["a", "b"] or rs["result"] == ["b", "a"],
        )
        self.assertEqual(rs["type"], "list")

    async def test_none_function(self) -> None:
        """Call none function"""
        rs = await self._calling_tool_on_server("none_func")
        self.assertEqual(rs["result"], None)
        self.assertEqual(rs["type"], "NoneType")
