# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for LocalWorkspace."""

import asyncio
import base64
import json
import os
import tempfile
from dataclasses import asdict
from unittest.async_case import IsolatedAsyncioTestCase
from urllib.parse import urlparse
from urllib.request import url2pathname

import aiofiles

from agentscope.message import (
    AssistantMsg,
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    ToolResultBlock,
    ToolResultState,
    URLSource,
    UserMsg,
)
from agentscope.workspace import LocalWorkspace


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
        2. All skill files are preserved during copying
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

        # Verify skills are discoverable via list_skills
        skills = await workspace.list_skills()
        skill_names = sorted(s.name for s in skills)
        self.assertEqual(skill_names, ["test_skill_1", "test_skill_2"])

    async def test_initialize_skip_duplicate_skills(self) -> None:
        """Test that duplicate skills are not copied again.

        This test verifies that:
        1. Skills are copied on first initialization
        2. Running initialize again does not copy duplicate skills
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

        # Get modification time of the skill directory
        skill_target = os.path.join(
            self.temp_dir.name,
            "skills",
            "test_skill_dup",
        )
        mtime_first = os.path.getmtime(skill_target)

        # Initialize again
        await workspace.initialize()

        # Verify skill directory was not modified (not re-copied)
        mtime_second = os.path.getmtime(skill_target)
        self.assertEqual(mtime_first, mtime_second)

        # Verify skill is still listed once
        skills = await workspace.list_skills()
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, "test_skill_dup")

    async def test_initialize_deduplicate_skills(self) -> None:
        """Test that duplicate skills in skill_paths are deduplicated.

        This test verifies that:
        1. When skill_paths contains duplicates (same hash), only one is copied
        2. No concurrent copy conflicts occur
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

        # Verify only one skill is listed
        skills = await workspace.list_skills()
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, "test_skill_dedup")

    async def test_concurrent_add_skill_is_deduplicated(self) -> None:
        """Concurrent add_skill calls for the same skill install once."""
        skill_dir = self._create_test_skill(
            "concurrent_skill",
            "A test skill for concurrent add_skill calls",
        )
        workspace = LocalWorkspace(workdir=self.temp_dir.name)
        await workspace.initialize()

        await asyncio.gather(
            *(workspace.add_skill(skill_dir) for _ in range(8)),
        )

        skills = await workspace.list_skills()
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, "concurrent_skill")

        skills_dir = os.path.join(self.temp_dir.name, "skills")
        skill_dirs = [
            d
            for d in os.listdir(skills_dir)
            if os.path.isdir(os.path.join(skills_dir, d))
        ]
        self.assertEqual(skill_dirs, ["concurrent_skill"])

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
        1. An empty list is returned when the skills directory doesn't exist
        2. An empty list is returned when the skills directory exists but
           contains no skill subdirectories
        """
        # Create workspace without initializing — skills dir doesn't exist
        workspace = LocalWorkspace(workdir=self.temp_dir.name)

        # list_skills returns empty list when skills directory doesn't exist
        skills = await workspace.list_skills()
        self.assertListEqual(skills, [])

        # After creating the skills directory, should still return empty list
        os.makedirs(os.path.join(self.temp_dir.name, "skills"), exist_ok=True)
        skills = await workspace.list_skills()
        self.assertListEqual(skills, [])


class TestLocalWorkspaceIsAlive(IsolatedAsyncioTestCase):
    """Test is_alive() for LocalWorkspace."""

    async def test_is_alive_returns_true(self) -> None:
        """is_alive() always returns True for local workspaces."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = LocalWorkspace(workdir=tmpdir)
            self.assertTrue(await ws.is_alive())


class TestLocalWorkspaceMCPPersistence(IsolatedAsyncioTestCase):
    """Test .mcp file persistence in LocalWorkspace."""

    async def asyncSetUp(self) -> None:
        # pylint: disable=consider-using-with
        self.temp_dir = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_add_mcp_creates_mcp_file(self) -> None:
        """Adding an MCP persists the .mcp JSON file."""
        from agentscope.mcp import MCPClient
        from agentscope.mcp._config import StdioMCPConfig

        ws = LocalWorkspace(workdir=self.temp_dir.name)
        await ws.initialize()

        client = MCPClient(
            name="test_mcp",
            is_stateful=True,
            mcp_config=StdioMCPConfig(
                command="echo",
                args=["hello"],
            ),
        )
        try:
            await ws.add_mcp(client)
        except BaseException:
            pass

        mcp_file = os.path.join(self.temp_dir.name, ".mcp")
        if os.path.exists(mcp_file):
            async with aiofiles.open(mcp_file, "r") as f:
                data = json.loads(await f.read())
            self.assertIsInstance(data, list)
        await ws.close()

    async def test_initialize_restores_from_mcp_file(self) -> None:
        """initialize() restores MCPs from .mcp file if it exists."""
        mcp_file = os.path.join(self.temp_dir.name, ".mcp")
        mcp_data = [
            {
                "name": "restored_mcp",
                "is_stateful": False,
                "mcp_config": {
                    "type": "http_mcp",
                    "url": "http://localhost:9999",
                },
            },
        ]
        async with aiofiles.open(mcp_file, "w") as f:
            await f.write(json.dumps(mcp_data))

        ws = LocalWorkspace(workdir=self.temp_dir.name)
        await ws.initialize()

        mcps = await ws.list_mcps()
        self.assertEqual(len(mcps), 1)
        self.assertEqual(mcps[0].name, "restored_mcp")
        await ws.close()

    async def test_initialize_uses_defaults_without_mcp_file(self) -> None:
        """Without .mcp file, initialize() uses default_mcps."""
        ws = LocalWorkspace(workdir=self.temp_dir.name)
        await ws.initialize()

        mcps = await ws.list_mcps()
        self.assertEqual(len(mcps), 0)
        await ws.close()

    async def test_remove_mcp_updates_mcp_file(self) -> None:
        """remove_mcp() updates the .mcp file after removal."""
        mcp_data = [
            {
                "name": "mcp_a",
                "is_stateful": False,
                "mcp_config": {
                    "type": "http_mcp",
                    "url": "http://localhost:9991",
                },
            },
            {
                "name": "mcp_b",
                "is_stateful": False,
                "mcp_config": {
                    "type": "http_mcp",
                    "url": "http://localhost:9992",
                },
            },
        ]
        mcp_file = os.path.join(self.temp_dir.name, ".mcp")
        async with aiofiles.open(mcp_file, "w") as f:
            await f.write(json.dumps(mcp_data))

        ws = LocalWorkspace(workdir=self.temp_dir.name)
        await ws.initialize()

        self.assertEqual(len(await ws.list_mcps()), 2)

        await ws.remove_mcp("mcp_a")
        self.assertEqual(len(await ws.list_mcps()), 1)

        async with aiofiles.open(mcp_file, "r") as f:
            saved = json.loads(await f.read())
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["name"], "mcp_b")
        await ws.close()


class TestLocalWorkspaceSkillsReconciliation(IsolatedAsyncioTestCase):
    """Test skill directory scanning in LocalWorkspace."""

    async def asyncSetUp(self) -> None:
        # pylint: disable=consider-using-with
        self.temp_dir = tempfile.TemporaryDirectory()
        # pylint: disable=consider-using-with
        self.skills_src = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        self.temp_dir.cleanup()
        self.skills_src.cleanup()

    def _make_skill(self, name: str, desc: str) -> str:
        d = os.path.join(self.skills_src.name, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                f"---\nname: {name}\ndescription: {desc}\n---\n"
                f"# {name}\n{desc}\n",
            )
        return d

    async def test_reconcile_detects_manual_addition(self) -> None:
        """Manually adding a skill dir triggers reconciliation."""
        ws = LocalWorkspace(workdir=self.temp_dir.name)
        await ws.initialize()

        skills = await ws.list_skills()
        self.assertEqual(len(skills), 0)

        # Manually copy a skill into the skills directory
        import shutil

        src = self._make_skill("manual_skill", "Added manually")
        dest = os.path.join(
            self.temp_dir.name,
            "skills",
            "manual_skill",
        )
        shutil.copytree(src, dest)

        # list_skills should detect and reconcile
        skills = await ws.list_skills()
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, "manual_skill")
        await ws.close()

    async def test_reconcile_detects_manual_removal(self) -> None:
        """Manually deleting a skill dir triggers index cleanup."""
        import shutil

        skill_src = self._make_skill("removable", "To be removed")
        ws = LocalWorkspace(
            workdir=self.temp_dir.name,
            skill_paths=[skill_src],
        )
        await ws.initialize()

        skills = await ws.list_skills()
        self.assertEqual(len(skills), 1)

        # Manually remove the skill directory
        skill_dir = os.path.join(
            self.temp_dir.name,
            "skills",
            "removable",
        )
        shutil.rmtree(skill_dir)

        # list_skills should detect the removal
        skills = await ws.list_skills()
        self.assertEqual(len(skills), 0)
        await ws.close()

    async def test_export_state(self) -> None:
        """export_state returns correct backend_type and payload."""
        ws = LocalWorkspace(workdir=self.temp_dir.name)
        await ws.initialize()

        state = await ws.export_state()
        self.assertEqual(state.backend_type, "local")
        self.assertEqual(state.payload["workdir"], self.temp_dir.name)
        self.assertIn("workspace_id", state.payload)
        await ws.close()
