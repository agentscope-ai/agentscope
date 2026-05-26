# -*- coding: utf-8 -*-
"""CodeActToolCallServer and remote_tool_call tests."""
import asyncio
import socket
from unittest.async_case import IsolatedAsyncioTestCase
import time
import os

from agentscope.codeact.code_act_tool_call_server import CodeActToolCallServer
from agentscope.codeact.code_act_client import remote_tool_call
from agentscope.message import TextBlock
from agentscope.tool import ToolResponse
from agentscope.tool._toolkit import Toolkit


def _is_port_in_use(port: int, timeout: int = 1) -> bool:
    """Check if a port is in use (occupied) or not (free)"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        in_use = s.connect_ex(("0.0.0.0", port)) == 0
        return in_use


def _create_dummy_toolkit() -> Toolkit:
    def succeed_with_compelete_metadata(txt: str) -> ToolResponse:
        """metadata is complete. succeeded. valid structured output"""
        return ToolResponse(
            content=[TextBlock(type="text", text=f"foo {txt}")],
            metadata={
                "success": True,
                "structured_output": {"key1": "some_value", "key2": 123},
            },
        )

    def fail_with_compelete_metadata(value: int) -> ToolResponse:
        return ToolResponse(
            content=[TextBlock(type="text", text=str(2 * value))],
            metadata={
                "success": False,
                "structured_output": {"you_wont_see_me": 123},
            },
        )

    def succeed_with_empty_structured_output(value: float) -> ToolResponse:
        return ToolResponse(
            content=[TextBlock(type="text", text=str(3.0 * value))],
            metadata={
                "success": True,
                "structured_output": {},
            },
        )

    def succeed_without_structured_output(value: float) -> ToolResponse:
        return ToolResponse(
            content=[TextBlock(type="text", text=str(3.0 * value))],
            metadata={
                "success": True,
            },
        )

    def no_metadata_in_response() -> ToolResponse:
        return ToolResponse(
            content=[TextBlock(type="text", text="metadata is not set")],
        )

    def always_interrupted(txt: str) -> ToolResponse:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"metadata is not set. {txt} is unused.",
                ),
            ],
            is_interrupted=True,
        )

    tk = Toolkit()
    tk.register_tool_function(tool_func=succeed_with_compelete_metadata)
    tk.register_tool_function(tool_func=fail_with_compelete_metadata)
    tk.register_tool_function(
        tool_func=succeed_with_empty_structured_output,
    )
    tk.register_tool_function(tool_func=succeed_without_structured_output)
    tk.register_tool_function(tool_func=no_metadata_in_response)
    tk.register_tool_function(tool_func=always_interrupted)
    return tk


def _find_free_port() -> int:
    for p in range(1024, 49152):
        if not _is_port_in_use(p):
            return p
    raise RuntimeError("No free port found")


class TestCodeActToolCallServer(IsolatedAsyncioTestCase):
    """Test the CodeActToolCallServer via direct httpx calls."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._port = _find_free_port()
        toolkit = _create_dummy_toolkit()
        cls._code_act_server = CodeActToolCallServer(
            port=cls._port,
            toolkit=toolkit,
        )
        asyncio.run(cls._code_act_server.start())

    async def _calling_tool_on_server(
        self,
        tool_name: str,
        tool_args: dict = None,
    ) -> dict:
        """Call the server endpoint directly via httpx."""
        import httpx

        url = f"http://localhost:{self._port}/call_tool"
        payload = {"tool_name": tool_name, "tool_args": tool_args or {}}
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload)
            return response.json()

    async def test_happy_case(self) -> None:
        """Tool call succeeded with structured output"""
        rs = await self._calling_tool_on_server(
            "succeed_with_compelete_metadata",
            {"txt": "Foo Bar"},
        )
        self.assertEqual(rs, {"key1": "some_value", "key2": 123})

    async def test_failed_tool_call(self) -> None:
        """Tool call finished with success flag set to False"""
        rs = await self._calling_tool_on_server(
            "fail_with_compelete_metadata",
            {"value": 3},
        )
        self.assertEqual(rs, {})

    async def test_succeeded_tool_call_with_empty_structured_output(
        self,
    ) -> None:
        """Tool call finished successfully but with empty structured output"""
        rs = await self._calling_tool_on_server(
            "succeed_with_empty_structured_output",
            {"value": 3},
        )
        self.assertEqual(rs, {})

    async def test_succeeded_tool_call_without_structured_output(self) -> None:
        """Tool call finished successfully but no structured output"""
        rs = await self._calling_tool_on_server(
            "succeed_without_structured_output",
            {"value": 3.3},
        )
        self.assertEqual(rs, {})

    async def test_tool_call_without_metadata_in_response(self) -> None:
        """Tool call finished but without metadata attribute"""
        rs = await self._calling_tool_on_server("no_metadata_in_response")
        self.assertEqual(rs, {})

    async def test_interrupted_tool_call(self) -> None:
        """Tool call is interrupted"""
        rs = await self._calling_tool_on_server(
            "always_interrupted",
            {"txt": "Foo Bar"},
        )
        self.assertTrue("tool call is interrupted." in rs["detail"])


class TestRemoteToolCall(IsolatedAsyncioTestCase):
    """Test the remote_tool_call function."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._port = _find_free_port()
        toolkit = _create_dummy_toolkit()
        cls._code_act_server = CodeActToolCallServer(
            port=cls._port,
            toolkit=toolkit,
        )
        os.environ["CODE_ACT_TOOL_CALL_SERVER_PORT"] = str(cls._port)
        asyncio.run(cls._code_act_server.start())

    async def test_happy_case(self) -> None:
        """Successful tool call returns structured output"""
        rs = await remote_tool_call(
            tool_name="succeed_with_compelete_metadata",
            tool_args={"txt": "hello"},
        )
        self.assertEqual(rs, {"key1": "some_value", "key2": 123})

    async def test_failed_tool_call(self) -> None:
        """Tool with success=False returns empty dict"""
        rs = await remote_tool_call(
            tool_name="fail_with_compelete_metadata",
            tool_args={"value": 3},
        )
        self.assertEqual(rs, {})

    async def test_empty_structured_output(self) -> None:
        """Tool succeeds but structured_output is empty"""
        rs = await remote_tool_call(
            tool_name="succeed_with_empty_structured_output",
            tool_args={"value": 3},
        )
        self.assertEqual(rs, {})

    async def test_no_structured_output_key(self) -> None:
        """Tool succeeds but metadata has no structured_output key"""
        rs = await remote_tool_call(
            tool_name="succeed_without_structured_output",
            tool_args={"value": 3.3},
        )
        self.assertEqual(rs, {})

    async def test_no_metadata(self) -> None:
        """Tool response has no metadata at all"""
        rs = await remote_tool_call(
            tool_name="no_metadata_in_response",
        )
        self.assertEqual(rs, {})

    async def test_interrupted(self) -> None:
        """Interrupted tool call returns error detail"""
        with self.assertRaises(Exception):
            await remote_tool_call(
                tool_name="always_interrupted",
                tool_args={"txt": "test"},
            )

    async def test_nonexistent_tool(self) -> None:
        """Calling a nonexistent tool returns empty dict (tool not found
        yields empty response from the server)"""
        rs = await remote_tool_call(
            tool_name="does_not_exist",
            tool_args={},
        )
        self.assertEqual(rs, {})

    async def test_default_tool_args(self) -> None:
        """tool_args defaults to empty dict when not provided"""
        rs = await remote_tool_call(
            tool_name="no_metadata_in_response",
        )
        self.assertEqual(rs, {})

    async def test_custom_timeout(self) -> None:
        """Custom timeout parameter is accepted without error"""
        rs = await remote_tool_call(
            tool_name="succeed_with_compelete_metadata",
            tool_args={"txt": "timeout test"},
            timeout=120,
        )
        self.assertEqual(rs, {"key1": "some_value", "key2": 123})

    async def test_connection_refused(self) -> None:
        """Connecting to a non-listening port raises an error"""
        os.environ["CODE_ACT_TOOL_CALL_SERVER_PORT"] = "1"
        try:
            with self.assertRaises(Exception):
                await remote_tool_call(
                    tool_name="succeed_with_compelete_metadata",
                    tool_args={"txt": "fail"},
                    timeout=2,
                )
        finally:
            os.environ["CODE_ACT_TOOL_CALL_SERVER_PORT"] = str(self._port)


def _create_temperature_toolkit() -> Toolkit:
    import random

    def get_fahrenheit_temperature(city: str) -> ToolResponse:
        """Return a fahrenheit temperature reading."""
        value = random.randint(-4, 122)
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Obtain the temperature in fahrenheit for {city}",
                ),
            ],
            metadata={
                "success": True,
                "structured_output": {"fahrenheit": value},
            },
        )

    def convert_fahrenheit_to_celsius(fahrenheit: int) -> ToolResponse:
        """Convert a fahrenheit reading to celsius reading."""
        c = int((fahrenheit - 32) * 5 / 9)
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="Convert fahrenheit reading to celsius reading",
                ),
            ],
            metadata={
                "success": True,
                "structured_output": {"celsius": c},
            },
        )

    tk = Toolkit()
    tk.register_tool_function(tool_func=get_fahrenheit_temperature)
    tk.register_tool_function(tool_func=convert_fahrenheit_to_celsius)
    return tk


class TestCodeActExecution(IsolatedAsyncioTestCase):
    """Test the code act problem solving process."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._port = _find_free_port()
        toolkit = _create_temperature_toolkit()
        cls._code_act_server = CodeActToolCallServer(
            port=cls._port,
            toolkit=toolkit,
        )
        asyncio.run(cls._code_act_server.start())

    async def test_code_act_code_execution(self) -> None:
        """Test the code that uses tool call results to solve user problem."""
        import pathlib
        import subprocess

        # /path/to/agentscope/tests/this_file.py
        test_file_path = pathlib.Path(__file__).resolve()
        agentscope_folder = test_file_path.parent.parent
        remote_tool_call_file_path = pathlib.Path(
            agentscope_folder,
            "src",
            "agentscope",
            "codeact",
            "code_act_client.py",
        )

        txt = ""
        with open(remote_tool_call_file_path, "r", encoding="utf-8") as fh:
            txt = fh.read()

        txt += """
#############################
# The following code is supposed to be generated by CodeActAgent.
# The code only uses tools of the agent to solve user problem.
#
# User: Tell me the temperature of Hangzhou city in Celsius degree.
async def solve_problem():
    temperature_result = await remote_tool_call(
        tool_name="get_fahrenheit_temperature",
        tool_args={"city": "hangzhou"},
        timeout=120,
        )

    print(f'temperature_result:{temperature_result}')

    temperature_in_fahrenheit = temperature_result.get('fahrenheit')
    print(f'temperature_in_fahrenheit:{temperature_in_fahrenheit}')

    conversion_result = await remote_tool_call(
        tool_name="convert_fahrenheit_to_celsius",
        tool_args={"fahrenheit": temperature_in_fahrenheit},
        timeout=120,
        )
    print(f'conversion_result:{conversion_result}')

    temperature_in_selsius = conversion_result.get('celsius')
    print(f'temperature_in_selsius:{temperature_in_selsius}')

    print(f'The temperature of Hangzhou is {temperature_in_selsius} C')

import asyncio
asyncio.run(solve_problem())
"""

        os.environ["CODE_ACT_TOOL_CALL_SERVER_PORT"] = str(self._port)

        py_file = pathlib.Path(
            test_file_path.parent,
            f"code_act_script_{time.time_ns()}.py",
        )

        try:
            with open(py_file, "w", encoding="utf-8") as wfh:
                wfh.write(txt)

            r = subprocess.run(
                ["python", str(py_file)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            stdout_str = r.stdout.decode("utf-8") if r.stdout else ""
            print(f"{str(py_file)}, captured result:|{stdout_str}|")
            self.assertTrue("The temperature of Hangzhou is " in stdout_str)
        finally:
            py_file.unlink(missing_ok=True)
