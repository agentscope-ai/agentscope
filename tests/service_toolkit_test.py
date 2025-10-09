# -*- coding: utf-8 -*-
# pylint: disable=C0301
""" Unit test for service toolkit. """
import asyncio
import json
import os
import sys
import unittest
import threading
from typing import Literal

from pydantic import BaseModel

import agentscope
from agentscope.model import ModelResponse
from agentscope.parsers import MultiTaggedContentParser, TaggedContent
from agentscope.service import (
    bing_search,
    execute_python_code,
    retrieve_from_list,
    query_mysql,
)
from agentscope.service import ServiceToolkit
from agentscope.message import ToolUseBlock


class ServiceToolkitTest(unittest.TestCase):
    """
    Unit test for service toolkit.
    """

    def setUp(self) -> None:
        """Init for ExampleTest."""
        agentscope.init(disable_saving=True)

        self.maxDiff = None

        self.json_schema_bing_search1 = {
            "type": "function",
            "function": {
                "name": "bing_search",
                "description": (
                    "Search question in Bing Search API and "
                    "return the searching results"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "num_results": {
                            "type": "integer",
                            "description": (
                                "The number of search results to return."
                            ),
                            "default": 10,
                        },
                        "question": {
                            "type": "string",
                            "description": "The search query string.",
                        },
                    },
                    "required": [
                        "question",
                    ],
                },
            },
        }

        self.json_schema_bing_search2 = {
            "type": "function",
            "function": {
                "name": "bing_search",
                "description": (
                    "Search question in Bing Search API and "
                    "return the searching results"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The search query string.",
                        },
                    },
                    "required": ["question"],
                },
            },
        }

        self.json_schema_func = {
            "type": "function",
            "function": {
                "name": "func",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "c": {"default": "test"},
                        "d": {
                            "enum": [1, "abc", "d"],
                            "default": 1,
                        },
                        "b": {},
                        "a": {"type": "string"},
                    },
                    "required": ["a", "b"],
                },
            },
        }

        self.json_schema_execute_python_code = {
            "type": "function",
            "function": {
                "name": "execute_python_code",
                "description": (
                    "Execute the given python code in a temp file and capture "
                    "the return code, standard output and error. Note you "
                    "must `print` the output to get the result, and the "
                    "tmp file will be removed right after the execution."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": (
                                "The Python code to be " "executed."
                            ),
                        },
                    },
                    "required": ["code"],
                },
            },
        }

        self.json_schema_retrieve_from_list = {
            "type": "function",
            "function": {
                "name": "retrieve_from_list",
                "description": """Retrieve data in a list.

Memory retrieval with user-defined score function. The score function is
expected to take the `query` and one of the element in 'knowledge' (a
list). This function retrieves top-k elements in 'knowledge' with
HIGHEST scores. If the 'query' is a dict but has no embedding,
we use the embedding model to embed the query.""",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "description": "A message to be retrieved.",
                        },
                    },
                    "required": [
                        "query",
                    ],
                },
            },
        }

        self.json_schema_query_mysql = {
            "type": "function",
            "function": {
                "name": "query_mysql",
                "description": "Execute query within MySQL database.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "SQL query to execute.",
                        },
                    },
                    "required": [
                        "query",
                    ],
                },
            },
        }

        self.json_schema_summarization = {
            "type": "function",
            "function": {
                "name": "summarization",
                "description": (
                    "Summarize the input text.\n"
                    "\n"
                    "Summarization function (Notice: current version "
                    "of token limitation is\n"
                    "built with Open AI API)"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Text to be summarized by the "
                            "model.",
                        },
                    },
                    "required": [
                        "text",
                    ],
                },
            },
        }

    def test_bing_search(self) -> None:
        """Test bing_search."""
        # api_key is specified by developer, while question and num_results
        # are specified by model
        _, doc_dict = ServiceToolkit.get(bing_search, api_key="xxx")
        print(json.dumps(doc_dict, indent=4))
        self.assertDictEqual(
            doc_dict,
            self.json_schema_bing_search1,
        )

        # Set num_results by developer rather than model
        _, doc_dict = ServiceToolkit.get(
            bing_search,
            num_results=3,
            api_key="xxx",
        )

        self.assertDictEqual(
            doc_dict,
            self.json_schema_bing_search2,
        )

    def test_enum(self) -> None:
        """Test enum in service toolkit."""

        def func(  # type: ignore
            a: str,
            b,
            c="test",
            d: Literal[1, "abc", "d"] = 1,
        ) -> None:
            print(a, b, c, d)

        _, doc_dict = ServiceToolkit.get(func)

        self.assertDictEqual(
            doc_dict,
            self.json_schema_func,
        )

    def test_exec_python_code(self) -> None:
        """Test execute_python_code in service toolkit."""
        _, doc_dict = ServiceToolkit.get(
            execute_python_code,
            timeout=300,
            use_docker=True,
            maximum_memory_bytes=None,
        )

        self.assertDictEqual(
            doc_dict,
            self.json_schema_execute_python_code,
        )

    def test_retrieval(self) -> None:
        """Test retrieval in service toolkit."""
        _, doc_dict = ServiceToolkit.get(
            retrieve_from_list,
            knowledge=[1, 2, 3],
            score_func=lambda x, y: 1.0,
            top_k=10,
            embedding_model=10,
            preserve_order=True,
        )

        self.assertDictEqual(
            doc_dict,
            self.json_schema_retrieve_from_list,
        )

    def test_sql_query(self) -> None:
        """Test sql_query in service toolkit."""
        _, doc_dict = ServiceToolkit.get(
            query_mysql,
            database="test",
            host="localhost",
            user="root",
            password="xxx",
            port=3306,
            allow_change_data=False,
            maxcount_results=None,
        )

        self.assertDictEqual(
            doc_dict,
            self.json_schema_query_mysql,
        )

    def run_mcp_tool_test(self, server: str = "echo_mcp_server.py") -> None:
        """Core logic to test the mcp tool with ServiceToolkit."""
        if not sys.version_info >= (3, 10):
            self.skipTest(
                "`test_mcp_tool` is skipped for Python versions < 3.10",
            )

        service_toolkit = ServiceToolkit()
        server_path = os.path.abspath(
            os.path.join(
                os.path.abspath(os.path.dirname(__file__)),
                "custom",
                server,
            ),
        )
        service_toolkit.add_mcp_servers(
            server_configs={
                "mcpServers": {
                    server.split(".")[0]: {
                        "command": "python",
                        "args": [
                            server_path,
                        ],
                    },
                },
            },
        )
        self.assertEqual(
            service_toolkit.tools_instruction,
            """## Tool Functions:
The following tool functions are available in the format of
```
{index}. {function name}: {function description}
{argument1 name} ({argument type}): {argument description}
{argument2 name} ({argument type}): {argument description}
...
```

1. echo: Echo the input text
	text (string): Input text
""",  # noqa
        )

        res = service_toolkit.parse_and_call_func(
            ToolUseBlock(
                type="tool_use",
                id="xxx",
                name="echo",
                input={"text": "Hi"},
            ),
            tools_api_mode=True,
        )
        self.assertEqual(res.content[0]["output"][0]["text"], "Hi")

    def test_mcp_tool_main_thread(self) -> None:
        """Test the mcp tool in the main process."""
        if not sys.version_info >= (3, 10):
            self.skipTest(
                "`test_mcp_tool_main_thread` is skipped for Python versions "
                "< 3.10",
            )
        self.run_mcp_tool_test(server="echo_mcp_server.py")
        self.run_mcp_tool_test(server="as_mcp_server.py")

    def test_mcp_tool_child_thread(self) -> None:
        """Test the mcp tool in a child thread."""
        if not sys.version_info >= (3, 10):
            self.skipTest(
                "`test_mcp_tool_main_thread` is skipped for Python versions "
                "< 3.10",
            )
        # Use a threading event to signal test failure
        failure_event = threading.Event()

        def thread_target() -> None:
            try:
                self.run_mcp_tool_test(server="echo_mcp_server.py")
                self.run_mcp_tool_test(server="as_mcp_server.py")
            except Exception as e:
                # Set the failure event if an exception occurs
                failure_event.set()
                raise e

        test_thread = threading.Thread(target=thread_target)
        test_thread.start()
        test_thread.join()

        # Check if the failure event was set
        if failure_event.is_set():
            self.fail("The child thread test failed.")

    async def async_run_mcp_tool_test(self) -> None:
        """Run the async MCP tool test logic."""
        # Simulate asynchronous operations with asyncio.sleep
        await asyncio.sleep(0.1)
        self.run_mcp_tool_test(server="echo_mcp_server.py")
        self.run_mcp_tool_test(server="as_mcp_server.py")

    def test_mcp_tool_async(self) -> None:
        """Test the mcp tool in the main async context."""
        asyncio.run(self.async_run_mcp_tool_test())

    def test_service_toolkit(self) -> None:
        """Test the object of ServiceToolkit."""
        service_toolkit = ServiceToolkit()

        service_toolkit.add(bing_search, api_key="xxx", num_results=3)
        service_toolkit.add(
            execute_python_code,
            timeout=300,
            use_docker=True,
            maximum_memory_bytes=None,
        )

        self.assertEqual(
            service_toolkit.tools_instruction,
            """## Tool Functions:
The following tool functions are available in the format of
```
{index}. {function name}: {function description}
{argument1 name} ({argument type}): {argument description}
{argument2 name} ({argument type}): {argument description}
...
```

1. bing_search: Search question in Bing Search API and return the searching results
	question (string): The search query string.
2. execute_python_code: Execute the given python code in a temp file and capture the return code, standard output and error. Note you must `print` the output to get the result, and the tmp file will be removed right after the execution.
	code (string): The Python code to be executed.
""",  # noqa
        )
        self.assertDictEqual(
            service_toolkit.json_schemas,
            {
                "bing_search": self.json_schema_bing_search2,
                "execute_python_code": self.json_schema_execute_python_code,
            },
        )

    def test_multi_tagged_content(self) -> None:
        """Test multi tagged content"""

        parser = MultiTaggedContentParser(
            TaggedContent(
                "thought",
                "[THOUGHT]",
                "what you think",
                "[/THOUGHT]",
            ),
            TaggedContent("speak", "[SPEAK]", "what you speak", "[/SPEAK]"),
            TaggedContent(
                "function",
                "[FUNCTION]",
                '{"function name": "xxx", "args": {"arg name": "xxx"}}',
                "[/FUNCTION]",
                parse_json=True,
            ),
        )

        target_format_instruction = (
            "Respond with specific tags as outlined below, and the content "
            "between [FUNCTION] and [/FUNCTION] MUST be a JSON object:\n"
            "[THOUGHT]what you think[/THOUGHT]\n"
            "[SPEAK]what you speak[/SPEAK]\n"
            '[FUNCTION]{"function name": "xxx", "args": {"arg name": "xxx"}}'
            "[/FUNCTION]"
        )

        self.assertEqual(parser.format_instruction, target_format_instruction)

        response = ModelResponse(
            text=(
                "This is a test\n[THOUGHT]I think this is a good idea"
                "[/THOUGHT]\n[SPEAK]I am speaking now[/SPEAK]\n[FUNCTION]"
                '{"function name": "bing_search", "args": {"query": "news of '
                'today"}}[/FUNCTION]'
            ),
        )
        res = parser.parse(response)

        self.assertDictEqual(
            res.parsed,
            {
                "thought": "I think this is a good idea",
                "speak": "I am speaking now",
                "function": {
                    "function name": "bing_search",
                    "args": {"query": "news of today"},
                },
            },
        )

    def test_extend_model_schema(self) -> None:
        """Test extending model schema in service toolkit."""
        toolkit = ServiceToolkit()

        def func(a: str, b: int) -> str:
            """A simple test function."""
            return f"{a}-{b}"

        toolkit.add(func)

        class ExtendModel(BaseModel):
            """A test class to extend the function schema."""

            c: str = "test"
            d: Literal["a", "b", "c"]
            e: dict[str, int]

        self.assertDictEqual(
            toolkit.json_schemas["func"],
            {
                "type": "function",
                "function": {
                    "name": "func",
                    "parameters": {
                        "properties": {
                            "a": {
                                "type": "string",
                            },
                            "b": {
                                "type": "integer",
                            },
                        },
                        "required": ["a", "b"],
                        "type": "object",
                    },
                    "description": "A simple test function.",
                },
            },
        )

        toolkit.extend_function_schema(func_name="func", model=ExtendModel)

        self.assertDictEqual(
            toolkit.json_schemas["func"],
            {
                "type": "function",
                "function": {
                    "name": "func",
                    "parameters": {
                        "properties": {
                            "a": {
                                "type": "string",
                            },
                            "b": {
                                "type": "integer",
                            },
                            "c": {
                                "default": "test",
                                "type": "string",
                            },
                            "d": {
                                "enum": ["a", "b", "c"],
                                "type": "string",
                            },
                            "e": {
                                "additionalProperties": {"type": "integer"},
                                "type": "object",
                            },
                        },
                        "required": ["a", "b", "d", "e"],
                        "type": "object",
                    },
                    "description": "A simple test function.",
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
