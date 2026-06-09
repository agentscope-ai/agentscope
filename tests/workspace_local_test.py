# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for LocalWorkspace."""
import os
import json
import base64
import hashlib
import tempfile
from pathlib import Path
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from dataclasses import asdict
from urllib.parse import urlparse
from urllib.request import url2pathname

import aiofiles
from utils import AnyString, MockModel
from agentscope.agent import Agent, ContextConfig
from agentscope.model import ChatResponse, StructuredResponse
from agentscope.state import AgentState
from agentscope.tool import Toolkit, ToolBase, ToolChunk
from agentscope.permission import PermissionDecision, PermissionBehavior
from agentscope.workspace import LocalWorkspace
from agentscope.mcp import MCPClient, StdioMCPConfig
from agentscope.message import (
    Msg,
    UserMsg,
    AssistantMsg,
    DataBlock,
    Base64Source,
    URLSource,
    TextBlock,
    ToolResultBlock,
    ToolResultState,
    ToolCallBlock,
)


class _LongResultTool(ToolBase):
    """A mock tool that returns a long string result for offload testing."""

    name: str = "long_result_tool"
    description: str = "A tool that returns a long string."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> PermissionDecision:
        """Always allow."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="Mock tool always allows",
            message="Mock tool always allows",
        )

    async def __call__(self, **_kwargs: Any) -> ToolChunk:
        """Return a long string result followed by a base64 data block, so we
        can also verify base64 data offloading."""
        return ToolChunk(
            content=[
                TextBlock(text="0" * 30000),
                DataBlock(
                    name="fake_image.png",
                    source=Base64Source(
                        data="AAECAwQF",
                        media_type="image/png",
                    ),
                ),
            ],
            state=ToolResultState.SUCCESS,
        )


class TestLocalWorkspaceOffload(IsolatedAsyncioTestCase):
    """Test cases for LocalWorkspace offload functionality."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        # pylint: disable=consider-using-with
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = LocalWorkspace(workdir=self.temp_dir.name)

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.temp_dir.cleanup()

    async def test_offload_context_pure_text(self) -> None:
        """Test offloading messages with pure text content.

        This test verifies that:
        1. Messages with string content are correctly offloaded
        2. The offloaded file is created at the expected path
        3. The file contains valid JSONL with all message fields preserved
        """
        session_id = "test_session_pure_text"
        msgs = [
            UserMsg(name="user", content="Hello, world!"),
            AssistantMsg(name="assistant", content="Hi there!"),
        ]

        # Offload the messages
        file_path = await self.workspace.offload_context(session_id, msgs)

        # Verify the file was created at the expected path
        expected_path = os.path.join(
            self.temp_dir.name,
            "sessions",
            session_id,
            "context.jsonl",
        )
        self.assertEqual(file_path, expected_path)
        self.assertTrue(os.path.exists(file_path))

        # Read and verify the offloaded messages
        async with aiofiles.open(file_path, "r") as f:
            content = await f.read()

        lines = content.strip().split("\n")
        self.assertEqual(len(lines), 2)

        # Compare with expected JSON strings
        expected_lines = [msg.model_dump_json() for msg in msgs]
        self.assertListEqual(lines, expected_lines)

    async def test_offload_context_multiple_calls(self) -> None:
        """Test multiple calls to offload_context for the same session.

        This test verifies that:
        1. Multiple calls to offload_context append correctly to the file
        2. Each message is on its own line (proper JSONL format)
        3. No lines are concatenated together
        """
        session_id = "test_session_multiple"

        # First batch of messages
        msgs1 = [
            UserMsg(name="user", content="First message"),
            AssistantMsg(name="assistant", content="First response"),
        ]

        # Second batch of messages
        msgs2 = [
            UserMsg(name="user", content="Second message"),
            AssistantMsg(name="assistant", content="Second response"),
        ]

        # Offload first batch
        file_path = await self.workspace.offload_context(session_id, msgs1)

        # Offload second batch
        file_path2 = await self.workspace.offload_context(session_id, msgs2)

        # Verify both calls return the same path
        self.assertEqual(file_path, file_path2)

        # Read and verify the offloaded messages
        async with aiofiles.open(file_path, "r") as f:
            content = await f.read()

        lines = content.strip().split("\n")
        self.assertEqual(len(lines), 4)

        # Compare with expected JSON strings
        expected_lines = [msg.model_dump_json() for msg in msgs1 + msgs2]
        self.assertListEqual(lines, expected_lines)

        # Verify each line is valid JSON
        for line in lines:
            msg = Msg.model_validate_json(line)
            self.assertIsNotNone(msg)

    async def test_offload_context_with_datablock(self) -> None:
        """Test offloading messages with DataBlock content.

        This test verifies that:
        1. Messages with DataBlock (Base64Source) are correctly offloaded
        2. DataBlock data is persisted to separate files
        3. DataBlock source is converted from Base64Source to URLSource
        4. The offloaded message file contains the updated DataBlock
        """
        session_id = "test_session_datablock"

        # Create a test image data (1x1 red pixel PNG)
        test_data = base64.b64encode(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde",
        ).decode()

        data_block = DataBlock(
            source=Base64Source(data=test_data, media_type="image/png"),
            name="test_image",
        )

        msgs = [
            UserMsg(
                name="user",
                content=[TextBlock(text="Check this image:"), data_block],
            ),
        ]

        # Offload the messages
        file_path = await self.workspace.offload_context(session_id, msgs)

        # Verify the message file was created
        self.assertTrue(os.path.exists(file_path))

        # Read and verify the offloaded message
        async with aiofiles.open(file_path, "r") as f:
            content = await f.read()

        loaded_msg = Msg.model_validate_json(content.strip())

        # Verify the data file was created and extract the URL
        self.assertIsInstance(loaded_msg.content, list)
        self.assertEqual(len(loaded_msg.content), 2)
        data_url = str(loaded_msg.content[1].source.url)
        self.assertTrue(data_url.startswith("file://"))
        # Convert file URL to local path (works on both Windows and Unix)
        data_file_path = url2pathname(urlparse(data_url).path)
        self.assertTrue(os.path.exists(data_file_path))

        # Verify the data file contains the correct content
        async with aiofiles.open(data_file_path, "rb") as f:
            saved_data = await f.read()
        self.assertEqual(saved_data, base64.b64decode(test_data))

        # Build expected message with URLSource for comparison
        # Use the actual IDs from loaded message to avoid UUID mismatch
        expected_msg = UserMsg(
            name="user",
            content=[
                TextBlock(
                    text="Check this image:",
                    id=loaded_msg.content[0].id,
                ),
                DataBlock(
                    id=loaded_msg.content[1].id,
                    source=loaded_msg.content[1].source,
                    name="test_image",
                ),
            ],
            id=loaded_msg.id,
            created_at=loaded_msg.created_at,
        )
        self.assertEqual(
            loaded_msg.model_dump_json(),
            expected_msg.model_dump_json(),
        )

    async def test_offload_data_block_deduplication(self) -> None:
        """Test that duplicate DataBlocks are deduplicated.

        This test verifies that:
        1. Multiple DataBlocks with the same content share the same file
        2. Only one file is created for duplicate data
        3. Both DataBlocks point to the same file path
        """
        # Create two DataBlocks with identical data
        test_data = base64.b64encode(b"test content").decode()

        data_block1 = DataBlock(
            source=Base64Source(data=test_data, media_type="text/plain"),
            name="file1",
        )
        data_block2 = DataBlock(
            source=Base64Source(data=test_data, media_type="text/plain"),
            name="file2",
        )

        # Offload both data blocks
        result1 = await self.workspace._offload_data_block(data_block1)
        result2 = await self.workspace._offload_data_block(data_block2)

        # Verify both point to the same file by comparing source URLs
        self.assertEqual(str(result1.source.url), str(result2.source.url))

        # Verify the file exists
        data_url = str(result1.source.url)
        # Convert file URL to local path (works on both Windows and Unix)
        data_file_path = url2pathname(urlparse(data_url).path)
        self.assertTrue(os.path.exists(data_file_path))

        # Verify only one file was created in the data directory
        data_dir = os.path.join(self.temp_dir.name, "data")
        files = os.listdir(data_dir)
        self.assertEqual(len(files), 1)

    async def test_offload_data_block_url_source(self) -> None:
        """Test offloading DataBlock with URLSource.

        This test verifies that:
        1. DataBlock with URLSource is returned as-is
        2. No file is created for URLSource DataBlocks
        """
        from pydantic import AnyUrl

        data_block = DataBlock(
            source=URLSource(
                url=AnyUrl("https://example.com/image.png"),
                media_type="image/png",
            ),
            name="remote_image",
        )

        # Offload the data block
        result = await self.workspace._offload_data_block(data_block)

        # Verify the data block is returned as-is by comparing full objects
        self.assertDictEqual(result.model_dump(), data_block.model_dump())

        # Verify no file was created in the data directory
        data_dir = os.path.join(self.temp_dir.name, "data")
        if os.path.exists(data_dir):
            files = os.listdir(data_dir)
            self.assertEqual(len(files), 0)

    async def test_offload_tool_result_string(self) -> None:
        """Test offloading tool result with string output.

        This test verifies that:
        1. Tool result with string output is correctly offloaded
        2. The offloaded file is created at the expected path
        3. The file contains the correct string content
        """
        session_id = "test_session_tool_result"
        tool_result = ToolResultBlock(
            id="tool_123",
            name="test_tool",
            output="Tool execution successful!",
            state=ToolResultState.SUCCESS,
        )

        # Offload the tool result
        file_path = await self.workspace.offload_tool_result(
            session_id,
            tool_result,
        )

        # Verify the file was created at the expected path
        expected_path = os.path.join(
            self.temp_dir.name,
            "sessions",
            session_id,
            f"tool_result-{tool_result.id}.txt",
        )
        self.assertEqual(file_path, expected_path)
        self.assertTrue(os.path.exists(file_path))

        # Read and verify the content
        async with aiofiles.open(file_path, "r") as f:
            content = await f.read()

        expected_content = "Tool execution successful!"
        self.assertEqual(content, expected_content)

    async def test_offload_tool_result_with_blocks(self) -> None:
        """Test offloading tool result with TextBlock and DataBlock output.

        This test verifies that:
        1. Tool result with list of blocks is correctly offloaded
        2. TextBlock content is extracted and written to file
        3. DataBlock is offloaded and referenced in the output file
        4. The output file contains the correct format
        """
        session_id = "test_session_tool_result_blocks"

        # Create test data
        test_data = base64.b64encode(b"test file content").decode()
        data_block = DataBlock(
            source=Base64Source(data=test_data, media_type="text/plain"),
            name="output.txt",
        )

        tool_result = ToolResultBlock(
            id="tool_456",
            name="file_tool",
            output=[
                TextBlock(text="File created successfully: "),
                data_block,
            ],
            state=ToolResultState.SUCCESS,
        )

        # Offload the tool result
        file_path = await self.workspace.offload_tool_result(
            session_id,
            tool_result,
        )

        # Verify the file was created
        self.assertTrue(os.path.exists(file_path))

        # Read and verify the content
        async with aiofiles.open(file_path, "r") as f:
            content = await f.read()

        # Verify the content structure (URL format varies by platform)
        self.assertTrue(content.startswith("File created successfully: "))
        self.assertIn("<data url='file://", content)
        self.assertIn("name='output.txt'", content)
        self.assertIn("media_type='text/plain'", content)
        self.assertTrue(content.endswith("/>"))

        # Extract and verify the data file exists
        # Parse the URL from the content
        import re

        url_match = re.search(r"url='([^']+)'", content)
        self.assertIsNotNone(url_match)
        data_url = url_match.group(1)
        # Convert file URL to local path (works on both Windows and Unix)
        data_file_path = url2pathname(urlparse(data_url).path)
        self.assertTrue(os.path.exists(data_file_path))


class TestLocalWorkspaceSkills(IsolatedAsyncioTestCase):
    """Test cases for LocalWorkspace skill management functionality."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        # pylint: disable=consider-using-with
        self.temp_dir = tempfile.TemporaryDirectory()
        # pylint: disable=consider-using-with
        self.test_skills_dir = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.temp_dir.cleanup()
        self.test_skills_dir.cleanup()

    def _create_test_skill(
        self,
        skill_name: str,
        description: str,
        additional_files: dict[str, str] | None = None,
    ) -> str:
        """Create a test skill directory with SKILL.md.

        Args:
            skill_name (`str`):
                The name of the skill.
            description (`str`):
                The description of the skill.
            additional_files (`dict[str, str] | None`, optional):
                Additional files to create in the skill directory.
                Keys are file names, values are file contents.

        Returns:
            `str`:
                The path to the created skill directory.
        """
        skill_dir = os.path.join(self.test_skills_dir.name, skill_name)
        os.makedirs(skill_dir, exist_ok=True)

        # Create SKILL.md with frontmatter
        skill_md_content = f"""---
name: {skill_name}
description: {description}
---

# {skill_name}

{description}
"""
        with open(
            os.path.join(skill_dir, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(skill_md_content)

        # Create additional files if provided
        if additional_files:
            for filename, content in additional_files.items():
                with open(
                    os.path.join(skill_dir, filename),
                    "w",
                    encoding="utf-8",
                ) as f:
                    f.write(content)

        return skill_dir

    async def test_initialize_copy_skills(self) -> None:
        """Test copying skills to workspace.

        This test verifies that:
        1. Skills are correctly copied from source paths to workspace
        2. The .skills file is created with correct hash mappings
        3. All skill files are preserved during copying
        """
        # Create test skills
        skill1_dir = self._create_test_skill(
            "test_skill_1",
            "A test skill for testing",
            {"tool.py": "def test_tool():\n    pass\n"},
        )
        skill2_dir = self._create_test_skill(
            "test_skill_2",
            "Another test skill",
            {"helper.py": "def helper():\n    return 42\n"},
        )

        # Create workspace with skill paths
        workspace = LocalWorkspace(
            workdir=self.temp_dir.name,
            skill_paths=[skill1_dir, skill2_dir],
        )

        # Initialize the workspace
        await workspace.initialize()

        # Verify skills were copied
        skills_dir = os.path.join(self.temp_dir.name, "skills")
        self.assertTrue(os.path.exists(skills_dir))

        # Verify skill directories exist
        skill1_target = os.path.join(skills_dir, "test_skill_1")
        skill2_target = os.path.join(skills_dir, "test_skill_2")
        self.assertTrue(os.path.exists(skill1_target))
        self.assertTrue(os.path.exists(skill2_target))

        # Verify SKILL.md files exist
        self.assertTrue(
            os.path.exists(os.path.join(skill1_target, "SKILL.md")),
        )
        self.assertTrue(
            os.path.exists(os.path.join(skill2_target, "SKILL.md")),
        )

        # Verify additional files were copied
        self.assertTrue(os.path.exists(os.path.join(skill1_target, "tool.py")))
        self.assertTrue(
            os.path.exists(os.path.join(skill2_target, "helper.py")),
        )

        # Verify .skills file was created with correct new structure
        skills_hash_file = os.path.join(skills_dir, ".skills")
        self.assertTrue(os.path.exists(skills_hash_file))

        async with aiofiles.open(skills_hash_file, "r") as f:
            skills_data = json.loads(await f.read())

        # Verify top-level structure
        self.assertIn("skills_dir_mtime", skills_data)
        self.assertIn("skills", skills_data)

        skills_index = skills_data["skills"]
        self.assertEqual(len(skills_index), 2)

        # Verify each entry has the correct structure
        self.assertIn("test_skill_1", skills_index)
        self.assertIn("test_skill_2", skills_index)
        self.assertDictEqual(
            {k: v["skill_name"] for k, v in skills_index.items()},
            {"test_skill_1": "test_skill_1", "test_skill_2": "test_skill_2"},
        )

    async def test_initialize_skip_duplicate_skills(self) -> None:
        """Test that duplicate skills are not copied again.

        This test verifies that:
        1. Skills are copied on first initialization
        2. Running initialize again does not copy duplicate skills
        3. The .skills file is not modified on second initialization
        """
        # Create test skill
        skill_dir = self._create_test_skill(
            "test_skill_dup",
            "A test skill for duplication testing",
        )

        # Create workspace and initialize
        workspace = LocalWorkspace(
            workdir=self.temp_dir.name,
            skill_paths=[skill_dir],
        )
        await workspace.initialize()

        # Get the .skills file content after first initialization
        skills_hash_file = os.path.join(
            self.temp_dir.name,
            "skills",
            ".skills",
        )
        async with aiofiles.open(skills_hash_file, "r") as f:
            hash_data_first = await f.read()

        # Get modification time of the skill directory
        skill_target = os.path.join(
            self.temp_dir.name,
            "skills",
            "test_skill_dup",
        )
        mtime_first = os.path.getmtime(skill_target)

        # Initialize again
        await workspace.initialize()

        # Verify .skills file is unchanged
        async with aiofiles.open(skills_hash_file, "r") as f:
            hash_data_second = await f.read()
        self.assertEqual(hash_data_first, hash_data_second)

        # Verify skill directory was not modified
        mtime_second = os.path.getmtime(skill_target)
        self.assertEqual(mtime_first, mtime_second)

    async def test_initialize_deduplicate_skills(self) -> None:
        """Test that duplicate skills in skill_paths are deduplicated.

        This test verifies that:
        1. When skill_paths contains duplicates (same hash), only one is copied
        2. No concurrent copy conflicts occur
        3. The .skills file contains only one entry for the duplicated skill
        """
        # Create a test skill
        skill_dir = self._create_test_skill(
            "test_skill_dedup",
            "A test skill for deduplication testing",
        )

        # Create workspace with the same skill path listed multiple times
        workspace = LocalWorkspace(
            workdir=self.temp_dir.name,
            skill_paths=[skill_dir, skill_dir, skill_dir],  # Same path 3 times
        )

        # Initialize the workspace
        await workspace.initialize()

        # Verify only one skill was copied
        skills_dir = os.path.join(self.temp_dir.name, "skills")
        skill_target = os.path.join(skills_dir, "test_skill_dedup")
        self.assertTrue(os.path.exists(skill_target))

        # Verify .skills file contains only one entry
        skills_hash_file = os.path.join(skills_dir, ".skills")
        self.assertTrue(os.path.exists(skills_hash_file))

        async with aiofiles.open(skills_hash_file, "r") as f:
            skills_data = json.loads(await f.read())

        # Should have exactly one entry in the skills index
        skills_index = skills_data["skills"]
        self.assertEqual(len(skills_index), 1)
        self.assertIn("test_skill_dedup", skills_index)
        self.assertEqual(
            skills_index["test_skill_dedup"]["skill_name"],
            "test_skill_dedup",
        )

    async def test_initialize_invalid_skill(self) -> None:
        """Test handling of invalid skills.

        This test verifies that:
        1. Skills without SKILL.md are not copied
        2. Skills with invalid frontmatter are not copied
        3. Valid skills are still copied correctly
        """
        # Create a valid skill
        valid_skill_dir = self._create_test_skill(
            "valid_skill",
            "A valid test skill",
        )

        # Create an invalid skill without SKILL.md
        invalid_skill_no_md = os.path.join(
            self.test_skills_dir.name,
            "invalid_no_md",
        )
        os.makedirs(invalid_skill_no_md, exist_ok=True)
        with open(
            os.path.join(invalid_skill_no_md, "tool.py"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("def tool():\n    pass\n")

        # Create an invalid skill with malformed frontmatter
        invalid_skill_bad_fm = os.path.join(
            self.test_skills_dir.name,
            "invalid_bad_fm",
        )
        os.makedirs(invalid_skill_bad_fm, exist_ok=True)
        with open(
            os.path.join(invalid_skill_bad_fm, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(
                "---\nname: missing_description\n---\n\nNo description field!",
            )

        # Create workspace with all skill paths
        workspace = LocalWorkspace(
            workdir=self.temp_dir.name,
            skill_paths=[
                valid_skill_dir,
                invalid_skill_no_md,
                invalid_skill_bad_fm,
            ],
        )

        # Initialize the workspace
        await workspace.initialize()

        # Verify only the valid skill was copied
        skills_dir = os.path.join(self.temp_dir.name, "skills")
        self.assertTrue(os.path.exists(skills_dir))

        # Verify valid skill exists
        valid_target = os.path.join(skills_dir, "valid_skill")
        self.assertTrue(os.path.exists(valid_target))

        # Verify invalid skills do not exist
        invalid_target_no_md = os.path.join(skills_dir, "invalid_no_md")
        invalid_target_bad_fm = os.path.join(skills_dir, "invalid_bad_fm")
        self.assertFalse(os.path.exists(invalid_target_no_md))
        self.assertFalse(os.path.exists(invalid_target_bad_fm))

    async def test_list_skills(self) -> None:
        """Test listing skills from workspace.

        This test verifies that:
        1. All skills in the workspace are correctly listed
        2. Each skill has the correct name, description, and directory
        3. The returned list matches the expected skills
        """
        # Create test skills
        skill1_dir = self._create_test_skill(
            "list_skill_1",
            "First skill for listing",
        )
        skill2_dir = self._create_test_skill(
            "list_skill_2",
            "Second skill for listing",
        )

        # Create workspace and initialize
        workspace = LocalWorkspace(
            workdir=self.temp_dir.name,
            skill_paths=[skill1_dir, skill2_dir],
        )
        await workspace.initialize()

        # List skills
        skills = await workspace.list_skills()

        # Verify the number of skills
        self.assertEqual(len(skills), 2)

        # Sort skills by name for consistent comparison
        skills_sorted = sorted(skills, key=lambda s: s.name)

        # Build expected skills for comparison
        expected_skills = [
            {
                "name": "list_skill_1",
                "description": "First skill for listing",
                "dir": skills_sorted[0].dir,  # Use actual dir path
                "markdown": skills_sorted[0].markdown,  # Use actual markdown
                "updated_at": skills_sorted[
                    0
                ].updated_at,  # Use actual timestamp
            },
            {
                "name": "list_skill_2",
                "description": "Second skill for listing",
                "dir": skills_sorted[1].dir,  # Use actual dir path
                "markdown": skills_sorted[1].markdown,  # Use actual markdown
                "updated_at": skills_sorted[
                    1
                ].updated_at,  # Use actual timestamp
            },
        ]

        # Compare full skill objects using dataclasses.asdict
        actual_skills = [asdict(skill) for skill in skills_sorted]
        self.assertListEqual(actual_skills, expected_skills)

    async def test_list_skills_empty(self) -> None:
        """Test listing skills when no skills exist.

        This test verifies that:
        1. An empty list is returned when no skills are in the workspace
        2. No errors are raised when the skills directory doesn't exist
        """
        # Create workspace without initializing
        workspace = LocalWorkspace(workdir=self.temp_dir.name)

        # List skills (should return empty list)
        skills = await workspace.list_skills()

        # Verify empty list is returned
        self.assertListEqual(skills, [])


class TestLocalWorkspaceWithAgent(IsolatedAsyncioTestCase):
    """Test the local workspace class offloading with the agent."""

    async def test_offload_tool_result(self) -> None:
        """Test integration with the agent when offloading tool result.

        This test verifies that:
        1. A long tool result is split into a reserved part (kept in context)
           and an offloaded part (written to disk).
        2. The reserved tool result block in the context is truncated and
           contains a system reminder pointing to the offload file.
        3. The offloaded file contains the truncated remainder.
        4. A second reply with a fresh tool call produces a new offload file.
        """
        with tempfile.TemporaryDirectory() as workdir:
            session_id = "test_session"
            model = MockModel(stream=False)
            agent = Agent(
                name="Friday",
                system_prompt="You're a helpful assistant named Friday.",
                model=model,
                toolkit=Toolkit(
                    tools=[_LongResultTool()],
                ),
                context_config=ContextConfig(
                    tool_result_limit=50,
                ),
                offloader=LocalWorkspace(
                    workdir=workdir,
                ),
                state=AgentState(session_id=session_id),
            )

            model.set_responses(
                mock_responses=[
                    [
                        ChatResponse(
                            content=[
                                ToolCallBlock(
                                    id="1",
                                    name="long_result_tool",
                                    input="{}",
                                ),
                            ],
                            is_last=True,
                        ),
                    ],
                    [
                        ChatResponse(
                            content=[
                                TextBlock(text="End_1."),
                            ],
                            is_last=True,
                        ),
                    ],
                ],
            )

            await agent.reply()

            # === Assert offload file content ===
            offload_path_1 = os.path.join(
                workdir,
                "sessions",
                session_id,
                "tool_result-1.txt",
            )
            self.assertTrue(os.path.exists(offload_path_1))
            async with aiofiles.open(offload_path_1, "r") as f:
                offload_content = await f.read()

            # The base64 payload is hashed with sha256 and persisted under
            # `{workdir}/data/{hash}.{ext}` with the decoded bytes.
            b64_data = "AAECAwQF"
            data_hash = hashlib.sha256(b64_data.encode()).hexdigest()
            data_file_path = os.path.join(
                workdir,
                "data",
                f"{data_hash}.png",
            )
            self.assertTrue(os.path.exists(data_file_path))
            async with aiofiles.open(data_file_path, "rb") as f:
                self.assertEqual(await f.read(), base64.b64decode(b64_data))

            # The full text is "0" * 30000 followed by a base64 DataBlock;
            # tool_result_limit=50 reserves ~200 chars of text in context, the
            # remaining 29800 chars + the DataBlock placeholder are offloaded.
            data_url = Path(data_file_path).as_uri()
            expected_offload_content = (
                "0" * 29800 + f"<data url='{data_url}' name='fake_image.png' "
                f"media_type='image/png'/>"
            )
            self.assertEqual(offload_content, expected_offload_content)

            # === Assert context content ===
            reminder_1 = (
                "\n<<<TRUNCATED>>>\n<system-reminder>The remaining content "
                "has been omitted for limited context. You can refer to the "
                f"file in '{offload_path_1}' for the truncated content if "
                "needed.</system-reminder>"
            )
            expected_first_msg = {
                "id": AnyString(),
                "name": "Friday",
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_call",
                        "id": "1",
                        "name": "long_result_tool",
                        "input": "{}",
                        "state": "finished",
                        "suggested_rules": [],
                    },
                    {
                        "type": "tool_result",
                        "id": "1",
                        "name": "long_result_tool",
                        "output": [
                            {
                                "type": "text",
                                "text": "0" * 200 + reminder_1,
                                "id": AnyString(),
                            },
                        ],
                        "state": "success",
                    },
                    {
                        "type": "text",
                        "text": "End_1.",
                        "id": AnyString(),
                    },
                ],
                "metadata": {},
                "created_at": AnyString(),
                "finished_at": None,
                "usage": None,
            }
            self.assertListEqual(
                [_.model_dump() for _ in agent.state.context],
                [expected_first_msg],
            )

    async def test_offload_context(self) -> None:
        """Test integration with the agent when offloading context.

        This test triggers context compression twice in the same session and
        verifies that:
        1. The offload file ``context.jsonl`` is appended to (not
           overwritten) across the two compressions.
        2. When the compressed context contains a base64-encoded
           ``DataBlock``, the binary payload is persisted to a separate
           data file and the offloaded JSON line references that file via a
           ``URLSource`` instead of embedding the base64 inline.
        3. ``agent.state.summary`` is rewritten on every compression and
           ends with a system-reminder pointing to the offload file.
        4. ``agent.state.context`` only retains the latest assistant reply.

        Note: compression triggers based on ``model.context_size`` together
        with the default ``ContextConfig.trigger_ratio`` (0.8) — we set
        ``context_size=100`` here so the threshold is just 80 tokens, and a
        ~500-byte user message (~125 tokens) is enough to trigger
        compression on each reply. The default ``ContextConfig`` is used.
        """
        with tempfile.TemporaryDirectory() as workdir:
            session_id = "test_session_ctx"
            model = MockModel(stream=False, context_size=100)
            agent = Agent(
                name="Friday",
                system_prompt="You're Friday.",
                model=model,
                toolkit=Toolkit(),
                offloader=LocalWorkspace(workdir=workdir),
                state=AgentState(session_id=session_id),
            )

            # The mock structured response is reused across both compression
            # calls (same summary fields each time).
            model.set_structured_response(
                StructuredResponse(
                    content={
                        "task_overview": "TASK",
                        "current_state": "STATE",
                        "important_discoveries": "DISCOVERIES",
                        "next_steps": "NEXT",
                        "context_to_preserve": "PRESERVE",
                    },
                ),
            )

            # Each reply yields a single final-text response (no tool calls).
            model.set_responses(
                mock_responses=[
                    ChatResponse(
                        content=[TextBlock(text="End_1.")],
                        is_last=True,
                    ),
                    ChatResponse(
                        content=[TextBlock(text="End_2.")],
                        is_last=True,
                    ),
                ],
            )

            offload_path = os.path.join(
                workdir,
                "sessions",
                session_id,
                "context.jsonl",
            )

            # ===== First reply =====
            # Build user_msg_a with **fixed** random fields (msg id, the
            # content-block ids, timestamps) so the offloaded JSONL is a
            # fully deterministic string we can assert against literally.
            #
            # Putting the DataBlock FIRST (before the long TextBlock) makes
            # the boundary split include both blocks on the compress side,
            # so the very first compression offloads a multimodal message —
            # the DataBlock is rewritten to a URLSource alongside the
            # original TextBlock, exercising the multimodal offload path.
            b64_data = "AAECAwQF"
            user_msg_a = UserMsg(
                name="user",
                content=[
                    DataBlock(
                        id="data_block_a",
                        name="fake_image_a.png",
                        source=Base64Source(
                            data=b64_data,
                            media_type="image/png",
                        ),
                    ),
                    TextBlock(id="text_block_a", text="A" * 500),
                ],
                id="msg_a",
                created_at="2026-01-01T00:00:00",
                finished_at="2026-01-01T00:00:00",
            )
            await agent.reply(user_msg_a)

            self.assertTrue(os.path.exists(offload_path))
            async with aiofiles.open(offload_path, "r") as f:
                content_after_first = await f.read()

            # The DataBlock is persisted to ``{workdir}/data/`` as soon as
            # it is included in an offloaded line — this happens during the
            # first compression because both blocks land on the compress
            # side of the boundary split.
            data_hash = hashlib.sha256(b64_data.encode()).hexdigest()
            data_file_path = os.path.join(
                workdir,
                "data",
                f"{data_hash}.png",
            )
            self.assertTrue(os.path.exists(data_file_path))
            async with aiofiles.open(data_file_path, "rb") as f:
                self.assertEqual(await f.read(), base64.b64decode(b64_data))

            # The single offloaded line carries user_msg_a with the
            # DataBlock's source rewritten from ``Base64Source`` to
            # ``URLSource`` (pointing at the persisted data file) while the
            # TextBlock is preserved as-is. The expected JSONL is written
            # literally so a developer can read off exactly what gets
            # persisted; only the temp-dir-dependent file URL is
            # interpolated via ``data_url``.
            data_url = Path(data_file_path).as_uri()
            expected_user_msg_a_offloaded_json = (
                '{"name":"user","content":['
                '{"type":"data","id":"data_block_a","source":'
                '{"type":"url","url":"' + data_url + '",'
                '"media_type":"image/png"},"name":"fake_image_a.png"},'
                '{"type":"text","text":"' + "A" * 500 + '",'
                '"id":"text_block_a"}'
                '],"role":"user","id":"msg_a","metadata":{},'
                '"created_at":"2026-01-01T00:00:00",'
                '"finished_at":"2026-01-01T00:00:00","usage":null}'
            )
            self.assertEqual(
                content_after_first,
                expected_user_msg_a_offloaded_json + "\n",
            )

            # ``state.context`` after the first compression is empty
            # (msgs_to_reserve is empty since both content blocks of
            # user_msg_a went to the compress side). After reasoning,
            # ``state.context[0]`` is the assistant's "End_1." reply. The
            # assistant fields (msg id, text-block id, timestamps) are
            # generated by the agent — we capture them here and substitute
            # them into the expected string.
            assistant_1 = agent.state.context[0]

            # ===== Second reply =====
            user_msg_b = UserMsg(
                name="user",
                content=[
                    TextBlock(id="text_block_b", text="B" * 500),
                ],
                id="msg_b",
                created_at="2026-01-02T00:00:00",
                finished_at="2026-01-02T00:00:00",
            )
            await agent.reply(user_msg_b)

            async with aiofiles.open(offload_path, "r") as f:
                content_after_second = await f.read()

            # The second compression offloads ``assistant_1`` and
            # ``user_msg_b``. The file is appended to (mode="a"), so it
            # now contains 3 lines: the multimodal user_msg_a from the
            # first compression, plus assistant_1 and user_msg_b from the
            # second.
            expected_assistant_1_json = (
                '{"name":"Friday","content":['
                '{"type":"text","text":"End_1.","id":"'
                + assistant_1.content[0].id
                + '"}'
                '],"role":"assistant","id":"' + assistant_1.id + '",'
                '"metadata":{},"created_at":"' + assistant_1.created_at + '",'
                '"finished_at":null,"usage":null}'
            )
            expected_user_msg_b_json = (
                '{"name":"user","content":['
                '{"type":"text","text":"' + "B" * 500 + '",'
                '"id":"text_block_b"}'
                '],"role":"user","id":"msg_b","metadata":{},'
                '"created_at":"2026-01-02T00:00:00",'
                '"finished_at":"2026-01-02T00:00:00","usage":null}'
            )
            self.assertEqual(
                content_after_second,
                expected_user_msg_a_offloaded_json
                + "\n"
                + expected_assistant_1_json
                + "\n"
                + expected_user_msg_b_json
                + "\n",
            )

            # ``state.summary`` is rewritten on every compression, so the
            # final value is just one rendering of the summary template plus
            # one offload pointer (both compressions wrote to the same file).
            expected_summary = (
                "<system-info>Here is a summary of your previous work\n"
                "# Task Overview\n"
                "TASK\n\n"
                "# Current State\n"
                "STATE\n\n"
                "# Important Discoveries\n"
                "DISCOVERIES\n\n"
                "# Next Steps\n"
                "NEXT\n\n"
                "# Context to Preserve\n"
                "PRESERVE</system-info>\n"
                f"<system-reminder>The compressed context is offloaded "
                f"to '{offload_path}', you can refer to it when needed."
                f"</system-reminder>"
            )
            self.assertEqual(agent.state.summary, expected_summary)

            # ``state.context`` only retains the latest assistant text;
            # everything else has been offloaded.
            expected_second_assistant = {
                "id": AnyString(),
                "name": "Friday",
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "End_2.",
                        "id": AnyString(),
                    },
                ],
                "metadata": {},
                "created_at": AnyString(),
                "finished_at": None,
                "usage": None,
            }
            self.assertListEqual(
                [_.model_dump() for _ in agent.state.context],
                [expected_second_assistant],
            )


class TestLocalWorkspaceMCPInit(IsolatedAsyncioTestCase):
    """Test MCP loading error handling in LocalWorkspace.initialize().

    Covers:
    - Invalid entries in persisted .mcp are skipped (not crashing)
    - Stateful MCP connection failures are skipped (not crashing)
    - Valid MCPs still load despite invalid neighbours
    """

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        # pylint: disable=consider-using-with
        self.temp_dir = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.temp_dir.cleanup()

    async def _write_mcp_file(self, entries: list[dict]) -> str:
        """Write a list of MCP config dicts to ``<workdir>/.mcp``.

        Args:
            entries: List of raw MCP config dicts.

        Returns:
            The path to the written file.
        """
        mcp_file = os.path.join(self.temp_dir.name, ".mcp")
        async with aiofiles.open(mcp_file, "w", encoding="utf-8") as f:
            await f.write(json.dumps(entries, indent=2, ensure_ascii=False))
        return mcp_file

    @staticmethod
    def _make_http_mcp(name: str) -> dict:
        """Return a valid stateless HTTP MCP entry."""
        return {
            "name": name,
            "is_stateful": False,
            "mcp_config": {
                "type": "http_mcp",
                "url": "http://localhost:19999/nonexistent",
            },
            "enable_tools": None,
            "disable_tools": None,
            "execution_timeout": None,
        }

    @staticmethod
    def _make_bad_stdio_mcp(name: str) -> dict:
        """Return an invalid STDIO MCP entry (is_stateful=False)."""
        return {
            "name": name,
            "is_stateful": False,
            "mcp_config": {
                "type": "stdio_mcp",
                "command": "nonexistent_cmd",
            },
            "enable_tools": None,
            "disable_tools": None,
            "execution_timeout": None,
        }

    # -----------------------------------------------------------------
    #  persisted .mcp
    # -----------------------------------------------------------------

    async def test_initialize_skips_bad_entry_keeps_good(self) -> None:
        """A persisted .mcp with one bad entry should skip it and still
        load the valid entry."""
        await self._write_mcp_file(
            [
                self._make_bad_stdio_mcp("bad_one"),
                self._make_http_mcp("good_one"),
            ],
        )

        ws = LocalWorkspace(workdir=self.temp_dir.name)
        await ws.initialize()

        mcps = await ws.list_mcps()
        names = [m.name for m in mcps]
        self.assertIn("good_one", names)
        self.assertNotIn("bad_one", names)

    # -----------------------------------------------------------------
    #  default_mcps + connect failure
    # -----------------------------------------------------------------

    async def test_initialize_connect_failure_removes_mcp(self) -> (None):
        """A stateful MCP whose connect() raises should not crash
        initialize() and should be removed from the MCP list."""
        ws = LocalWorkspace(
            workdir=self.temp_dir.name,
            default_mcps=[
                MCPClient(
                    name="will_fail_connect",
                    is_stateful=True,
                    mcp_config=StdioMCPConfig(
                        command="nonexistent_command_xyz",
                    ),
                ),
            ],
        )
        await ws.initialize()
        self.assertTrue(ws.is_alive)
        names = [m.name for m in await ws.list_mcps()]
        self.assertNotIn("will_fail_connect", names)
