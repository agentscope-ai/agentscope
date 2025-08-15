# -*- coding: utf-8 -*-
"""The tool module unit tests"""

import os
import tempfile
from unittest import IsolatedAsyncioTestCase

import shortuuid

from agentscope.tool import (
    execute_python_code,
    execute_shell_command,
    view_text_file,
    write_text_file,
    insert_text_file,
)


class ToolTest(IsolatedAsyncioTestCase):
    """Test cases for the tool module."""

    def setUp(self) -> None:
        """Set up the test environment."""
        self.path_file = "./tmp.txt"
        if os.path.exists(self.path_file):
            os.remove(self.path_file)

    async def test_execute_python_code(self) -> None:
        """Test executing Python code."""

        # empty output
        res = await execute_python_code(code="a = 1 + 1")
        self.assertEqual(
            "<returncode>0</returncode>"
            "<stdout></stdout>"
            "<stderr></stderr>",
            res.content[0]["text"],
        )

        # with output
        res = await execute_python_code(code="print('Hello, World!')")
        self.assertEqual(
            "<returncode>0</returncode>"
            "<stdout>Hello, World!\n</stdout>"
            "<stderr></stderr>",
            res.content[0]["text"],
        )

        # with exception
        res = await execute_python_code(code="raise Exception('Test error')")
        self.assertTrue(
            res.content[0]["text"].startswith(
                "<returncode>1</returncode>"
                "<stdout></stdout>"
                "<stderr>Traceback (most recent call last):\n  File ",
            ),
        )
        self.assertTrue(
            res.content[0]["text"].endswith(
                '.py", line 1, in <module>\n'
                "    raise Exception('Test error')\n"
                "Exception: Test error\n"
                "</stderr>",
            ),
        )

        # with timeout
        code = """print("123")
import time
time.sleep(5)
print("456")"""

        res = await execute_python_code(code)
        self.assertEqual(
            "<returncode>0</returncode>"
            "<stdout>123\n456\n</stdout>"
            "<stderr></stderr>",
            res.content[0]["text"],
        )

        res = await execute_python_code(code, timeout=2)
        self.assertEqual(
            "<returncode>-1</returncode>"
            "<stdout>123\n</stdout>"
            "<stderr>TimeoutError: The code execution exceeded the "
            "timeout of 2 seconds.</stderr>",
            res.content[0]["text"],
        )

    async def test_execute_shell_command(self) -> None:
        """Test executing shell command."""
        # empty output
        res = await execute_shell_command(command="echo 'Hello, World!'")
        self.assertEqual(
            "<returncode>0</returncode>"
            "<stdout>Hello, World!\n</stdout>"
            "<stderr></stderr>",
            res.content[0]["text"],
        )

        # with exception
        res = await execute_shell_command(command="non_existent_command")
        assert "not found" in res.content[0]["text"]

        # with timeout
        res = await execute_shell_command(
            command='echo "123"; sleep 5; echo "456"',
        )
        self.assertEqual(
            "<returncode>0</returncode>"
            "<stdout>123\n456\n</stdout>"
            "<stderr></stderr>",
            res.content[0]["text"],
        )

        res = await execute_shell_command(
            command='echo "123"; sleep 5; echo "456"',
            timeout=2,
        )
        self.assertEqual(
            "<returncode>-1</returncode>"
            "<stdout>123\n</stdout>"
            "<stderr>TimeoutError: The command execution exceeded "
            "the timeout of 2 seconds.</stderr>",
            res.content[0]["text"],
        )

    async def test_view_text_file(self) -> None:
        """Test viewing text file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_file = os.path.join(temp_dir, f"tmp_{shortuuid.uuid()}.txt")
            with open(temp_file, "w", encoding="utf-8") as f:
                f.write("""1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n""")

            # View the whole file
            res = await view_text_file(file_path=temp_file)
            self.assertEqual(
                f"The content of {temp_file}:\n```\n1: 1\n2: 2\n3: 3\n"
                f"4: 4\n5: 5\n6: 6\n7: 7\n8: 8\n9: 9\n10: 10\n```",
                res.content[0]["text"],
            )

            # View a specific range
            res = await view_text_file(temp_file, ranges=[3, 5])
            self.assertEqual(
                f"The content of {temp_file} in [3, 5] lines:\n"
                f"```\n3: 3\n4: 4\n5: 5\n```",
                res.content[0]["text"],
            )

            # View a range that exceeds the file length
            res = await view_text_file(temp_file, ranges=[8, 13])
            self.assertEqual(
                f"The content of {temp_file} in [8, 13] lines:\n"
                f"```\n8: 8\n9: 9\n10: 10\n```",
                res.content[0]["text"],
            )

            # View a range that is invalid
            res = await view_text_file(temp_file, ranges=[11, 13])
            self.assertEqual(
                f"InvalidArgumentError: The range '[11, 13]' is out of "
                f"bounds for the file '{temp_file}', which has only 10 lines.",
                res.content[0]["text"],
            )

            # View invalid file path
            res = await view_text_file(file_path="non_existent_file.txt")
            self.assertEqual(
                "Error: The file non_existent_file.txt does not exist.",
                res.content[0]["text"],
            )

            # View a non-file path
            res = await view_text_file("/")
            self.assertEqual(
                "Error: The path / is not a file.",
                res.content[0]["text"],
            )

    async def test_write_text_file(self) -> None:
        """Test writing to text file."""
        # create and write a new file
        res = await write_text_file(
            self.path_file,
            "a\nb\nc\n",
            None,
        )
        self.assertEqual(
            "Create and write ./tmp.txt successfully.",
            res.content[0]["text"],
        )

        # replace content
        res = await write_text_file(
            self.path_file,
            "d\n",
            [2, 2],
        )
        self.assertEqual(
            "Write ./tmp.txt successfully. The new content snippet:\n"
            "```\n1: a\n2: d\n3: c\n```",
            res.content[0]["text"],
        )

    async def test_insert_text_file(self) -> None:
        """Test inserting text into a file."""
        with open(self.path_file, "w", encoding="utf-8") as f:
            f.write("\n".join([str(_) for _ in range(50)]))
        res = await insert_text_file(
            self.path_file,
            "d",
            line_number=1,
        )
        self.assertEqual(
            res.content[0]["text"],
            "Insert content into ./tmp.txt at line 1 successfully. "
            "The new content between lines 1-7 is:\n"
            "```\n"
            "1: d\n"
            "2: 0\n"
            "3: 1\n"
            "4: 2\n"
            "5: 3\n"
            "6: 4\n"
            "7: 5\n"
            "```",
        )

        res = await insert_text_file(
            self.path_file,
            "e",
            line_number=25,
        )
        self.assertEqual(
            res.content[0]["text"],
            "Insert content into ./tmp.txt at line 25 successfully. "
            "The new content between lines 20-31 is:\n"
            "```\n"
            "20: 18\n"
            "21: 19\n"
            "22: 20\n"
            "23: 21\n"
            "24: 22\n"
            "25: e\n"
            "26: 23\n"
            "27: 24\n"
            "28: 25\n"
            "29: 26\n"
            "30: 27\n"
            "31: 28\n"
            "```",
        )

        res = await insert_text_file(
            self.path_file,
            "\n".join(["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]),
            line_number=25,
        )
        self.assertEqual(
            res.content[0]["text"],
            "Insert content into ./tmp.txt at line 25 successfully. "
            "The new content between lines 20-40 is:\n"
            "```\n"
            "20: 18\n"
            "21: 19\n"
            "22: 20\n"
            "23: 21\n"
            "24: 22\n"
            "25: a\n"
            "26: b\n"
            "27: c\n"
            "28: d\n"
            "29: e\n"
            "30: f\n"
            "31: g\n"
            "32: h\n"
            "33: i\n"
            "34: j\n"
            "35: e\n"
            "36: 23\n"
            "37: 24\n"
            "38: 25\n"
            "39: 26\n"
            "40: 27\n"
            "```",
        )

        res = await insert_text_file(
            self.path_file,
            "The\nlast\nline",
            63,
        )
        self.assertEqual(
            res.content[0]["text"],
            "Insert content into ./tmp.txt at line 63 successfully. "
            "The new content between lines 58-65 is:\n"
            "```\n"
            "58: 45\n"
            "59: 46\n"
            "60: 47\n"
            "61: 48\n"
            "62: 49\n"
            "63: The\n"
            "64: last\n"
            "65: line```",
        )

        res = await insert_text_file(
            self.path_file,
            "end\nof\ntest",
            100,
        )
        self.assertEqual(
            res.content[0]["text"],
            "InvalidArgumentsError: The given line_number (100) is "
            "not in the valid range [1, 66].",
        )

    def tearDown(self) -> None:
        """Clean up after tests."""
        if os.path.exists(self.path_file):
            os.remove(self.path_file)
