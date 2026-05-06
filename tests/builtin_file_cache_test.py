# -*- coding: utf-8 -*-
"""File cache test case for Read/Write/Edit tools."""
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.state import AgentState
from agentscope.tool import Read, Write, Edit


class FileCacheTest(IsolatedAsyncioTestCase):
    """Test file cache functionality for Read/Write/Edit tools."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.read_tool = Read()
        self.write_tool = Write()
        self.edit_tool = Edit()
        self.state = AgentState()

        # Create a temporary directory
        self.temp_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.temp_dir, "test.txt")

    async def asyncTearDown(self) -> None:
        """Clean up temporary files."""
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    async def test_edit_without_read(self) -> None:
        """Test Edit fails when file not read first."""
        # Create a file
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Hello World\n")

        # Try to edit without reading first
        chunk = await self.edit_tool(
            file_path=self.test_file,
            old_string="Hello",
            new_string="Hi",
            _agent_state=self.state,
        )

        # Should fail with error
        self.assertEqual(chunk.state, "error")
        self.assertIn("must first read", chunk.content[0].text)

    async def test_write_without_read(self) -> None:
        """Test Write fails when existing file not read first."""
        # Create a file
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Existing content\n")

        # Try to write without reading first
        chunk = await self.write_tool(
            file_path=self.test_file,
            content="New content\n",
            _agent_state=self.state,
        )

        # Should fail with error
        self.assertEqual(chunk.state, "error")
        self.assertIn("has not been read yet", chunk.content[0].text)

    async def test_write_new_file_without_read(self) -> None:
        """Test Write succeeds for new file without reading."""
        new_file = os.path.join(self.temp_dir, "new_file.txt")

        # Write to a new file (doesn't exist yet)
        chunk = await self.write_tool(
            file_path=new_file,
            content="New file content\n",
            _agent_state=self.state,
        )

        # Should succeed
        self.assertEqual(chunk.state, "running")
        self.assertTrue(os.path.exists(new_file))

        with open(new_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "New file content\n")

    async def test_edit_after_read(self) -> None:
        """Test Edit succeeds after reading file."""
        # Create a file
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Hello World\n")

        # Read the file first
        read_chunk = await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )
        self.assertEqual(read_chunk.state, "running")

        # Now edit should succeed
        edit_chunk = await self.edit_tool(
            file_path=self.test_file,
            old_string="Hello",
            new_string="Hi",
            _agent_state=self.state,
        )

        self.assertEqual(edit_chunk.state, "running")

        # Verify file was edited
        with open(self.test_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "Hi World\n")

    async def test_write_after_read(self) -> None:
        """Test Write succeeds after reading file."""
        # Create a file
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Old content\n")

        # Read the file first
        read_chunk = await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )
        self.assertEqual(read_chunk.state, "running")

        # Now write should succeed
        write_chunk = await self.write_tool(
            file_path=self.test_file,
            content="New content\n",
            _agent_state=self.state,
        )

        self.assertEqual(write_chunk.state, "running")

        # Verify file was overwritten
        with open(self.test_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "New content\n")

    async def test_cache_invalidation_after_file_deletion(self) -> None:
        """Test cache handles file deletion gracefully."""
        # Create a file
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Test content\n")

        # Read the file to cache it
        read_chunk = await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )
        self.assertEqual(read_chunk.state, "running")

        # Verify cache exists
        self.assertEqual(len(self.state.tool_context.read_file_cache), 1)
        self.assertEqual(
            self.state.tool_context.read_file_cache[0].file_path,
            self.test_file,
        )

        # Delete the file
        os.unlink(self.test_file)

        # Try to edit - should fail with "File not found" error
        edit_chunk = await self.edit_tool(
            file_path=self.test_file,
            old_string="Test",
            new_string="New",
            _agent_state=self.state,
        )

        # Should fail with "File not found" error
        self.assertEqual(edit_chunk.state, "error")
        self.assertIn("not found", edit_chunk.content[0].text.lower())

        # Try to read again - should also fail
        read_chunk2 = await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )
        self.assertEqual(read_chunk2.state, "error")
        self.assertIn("does not exist", read_chunk2.content[0].text)

    async def test_cache_invalidation_after_file_modification(self) -> None:
        """Test cache detects file modification."""
        # Create a file
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Original content\n")

        # Read the file to cache it
        read_chunk = await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )
        self.assertEqual(read_chunk.state, "running")

        # Modify the file externally
        import time

        time.sleep(0.1)  # Ensure mtime changes
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Modified content\n")

        # Try to edit - should fail because cache is stale
        edit_chunk = await self.edit_tool(
            file_path=self.test_file,
            old_string="Original",
            new_string="New",
            _agent_state=self.state,
        )

        # Should fail with error
        self.assertEqual(edit_chunk.state, "error")
        self.assertIn("must first read", edit_chunk.content[0].text)

    async def test_cache_lru_eviction(self) -> None:
        """Test LRU cache eviction when max_cache_files is exceeded."""
        # Set a small cache limit
        self.state.tool_context.max_cache_files = 3

        # Create and read 4 files
        files = []
        for i in range(4):
            file_path = os.path.join(self.temp_dir, f"file{i}.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"Content {i}\n")
            files.append(file_path)

            # Read each file
            await self.read_tool(
                file_path=file_path,
                _agent_state=self.state,
            )

        # Cache should only have 3 files (oldest evicted)
        self.assertEqual(len(self.state.tool_context.read_file_cache), 3)

        # The first file should have been evicted
        cached_paths = [
            entry.file_path
            for entry in self.state.tool_context.read_file_cache
        ]
        self.assertNotIn(files[0], cached_paths)
        self.assertIn(files[1], cached_paths)
        self.assertIn(files[2], cached_paths)
        self.assertIn(files[3], cached_paths)

    async def test_cache_without_state(self) -> None:
        """Test tools work without state (fallback mode)."""
        # Create a file
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Hello World\n")

        # Edit without state should work (fallback to reading from disk)
        edit_chunk = await self.edit_tool(
            file_path=self.test_file,
            old_string="Hello",
            new_string="Hi",
            _agent_state=None,
        )

        self.assertEqual(edit_chunk.state, "running")

        # Verify file was edited
        with open(self.test_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "Hi World\n")

    async def test_multiple_reads_update_cache(self) -> None:
        """Test reading same file multiple times updates cache."""
        # Create a file
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Content v1\n")

        # Read the file
        await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )
        self.assertEqual(len(self.state.tool_context.read_file_cache), 1)

        # Read again - should update cache, not add duplicate
        await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )
        self.assertEqual(len(self.state.tool_context.read_file_cache), 1)

    async def test_read_cache_lines_contain_newlines(self) -> None:
        """Test that cached lines from Read retain trailing newlines.

        readlines() includes the newline character in each line. The cache
        must store them as-is so that "".join(lines) reconstructs the exact
        original file content.
        """
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("line1\nline2\nline3\n")

        await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )

        cache = await self.state.tool_context.get_cache(self.test_file)
        self.assertIsNotNone(cache)
        # Each line from readlines() ends with \n
        self.assertEqual(cache.lines, ["line1\n", "line2\n", "line3\n"])
        # "".join reconstructs the exact original content
        self.assertEqual("".join(cache.lines), "line1\nline2\nline3\n")

    async def test_edit_multiline_match_from_cache(self) -> None:
        """Test Edit correctly matches multi-line old_string from
        cached content.

        Regression test for the bug where "\n".join(cache.lines) doubled the
        newlines (e.g. "line1\n\nline2\n" instead of "line1\nline2\n"),
        making multi-line old_string matching fail.
        """
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("line1\nline2\nline3\n")

        await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )

        # This multi-line old_string must match exactly in the reconstructed
        # content; with the bug it would not be found.
        chunk = await self.edit_tool(
            file_path=self.test_file,
            old_string="line1\nline2",
            new_string="replaced",
            _agent_state=self.state,
        )

        self.assertEqual(chunk.state, "running")

        with open(self.test_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "replaced\nline3\n")

    async def test_edit_single_line_match_from_cache(self) -> None:
        """Test Edit correctly matches single-line old_string from cache."""
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("Hello World\nThis is a test\n")

        await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )

        chunk = await self.edit_tool(
            file_path=self.test_file,
            old_string="Hello World",
            new_string="Hello Python",
            _agent_state=self.state,
        )

        self.assertEqual(chunk.state, "running")

        with open(self.test_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "Hello Python\nThis is a test\n")

    async def test_write_invalidates_cache(self) -> None:
        """Test that Write updates the cache so Edit can use it afterwards."""
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("original content\n")

        # Read to populate cache
        await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )

        # Overwrite with Write (mtime changes, old cache becomes stale)
        write_chunk = await self.write_tool(
            file_path=self.test_file,
            content="new content\n",
            _agent_state=self.state,
        )
        self.assertEqual(write_chunk.state, "running")

        # The old cache entry is now stale; Edit should require a new Read
        edit_chunk = await self.edit_tool(
            file_path=self.test_file,
            old_string="new content",
            new_string="updated content",
            _agent_state=self.state,
        )
        self.assertEqual(edit_chunk.state, "error")
        self.assertIn("must first read", edit_chunk.content[0].text)

    async def test_write_cache_stale_then_reread(self) -> None:
        """Test workflow: Read -> Write -> Read -> Edit works correctly."""
        with open(self.test_file, "w", encoding="utf-8") as f:
            f.write("original\n")

        # First read
        await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )

        # Overwrite
        import time

        time.sleep(0.01)  # ensure mtime changes
        await self.write_tool(
            file_path=self.test_file,
            content="rewritten\n",
            _agent_state=self.state,
        )

        # Re-read to refresh cache
        await self.read_tool(
            file_path=self.test_file,
            _agent_state=self.state,
        )

        # Now Edit should succeed against the new content
        edit_chunk = await self.edit_tool(
            file_path=self.test_file,
            old_string="rewritten",
            new_string="final",
            _agent_state=self.state,
        )
        self.assertEqual(edit_chunk.state, "running")

        with open(self.test_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "final\n")
