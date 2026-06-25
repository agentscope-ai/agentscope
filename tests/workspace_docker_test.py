# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for DockerWorkspace.

Mirrors the structure of ``workspace_local_test.py`` so that the two
backends are validated against the same surface contract:

* offload_context (text + DataBlock + dedup)
* offload_tool_result (string + multimodal blocks)
* skill seeding / listing
* lifecycle (initialize idempotent, close, workspace_id stability)

Two practical differences vs. the local tests:

1. Paths *returned* by DockerWorkspace are container-side
   (``/workspace/sessions/...``).  Host-side file checks therefore
   compose paths against ``self.temp_dir.name``, which is bind-mounted
   to ``CONTAINER_WORKDIR`` inside the container.

2. ``DataBlock`` URLs persisted by DockerWorkspace use the container
   path (``file:///workspace/data/<hash>.png``).  We assert the URL
   string against the container path and verify the file's *bytes* via
   the corresponding host-side path.

The whole module is skipped when no Docker daemon is reachable.
"""

import base64
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import uuid
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from urllib.parse import urlparse

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
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk
from agentscope.workspace import DockerWorkspace
from agentscope.workspace._docker._make_dockerfile import (
    CONTAINER_SESSIONS_DIR,
    CONTAINER_SKILLS_DIR,
)

# ── docker daemon detection ────────────────────────────────────────


def _docker_available() -> bool:
    """Return ``True`` iff the Docker daemon is reachable.

    Probes via the ``docker`` CLI rather than the aiodocker async client
    so the check is cheap and synchronous (runs at module import time).
    """
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


_DOCKER_OK = _docker_available()
_SKIP_REASON = "Docker daemon not available"


# ── helper tool for agent-integration test ────────────────────────


class _LongResultTool(ToolBase):
    """Mock tool that returns a long string + base64 DataBlock."""

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
        """Long text + base64 DataBlock so we exercise both offload paths."""
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


# ── offload tests ─────────────────────────────────────────────────


@unittest.skipUnless(_DOCKER_OK, _SKIP_REASON)
class TestDockerWorkspaceOffload(IsolatedAsyncioTestCase):
    """Test cases for DockerWorkspace offload functionality."""

    async def asyncSetUp(self) -> None:
        """Build a fresh workspace bound to a temp host dir.

        The workspace_id is randomised so test runs do not collide on
        the deterministic ``as_ws_<workspace_id>`` container name.
        """
        # pylint: disable=consider-using-with
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = DockerWorkspace(
            workspace_id=f"test-{uuid.uuid4().hex[:8]}",
            host_workdir=self.temp_dir.name,
        )
        await self.workspace.initialize()

    async def asyncTearDown(self) -> None:
        """Stop the container and clean up the temp host dir."""
        try:
            await self.workspace.close()
        finally:
            self.temp_dir.cleanup()

    async def test_offload_context_pure_text(self) -> None:
        """Text-only offload: returned path is container-side, lines match.

        Verifies:
        1. The returned path is the *container-side* JSONL path.
        2. The host-mirror file (under the bind-mounted workdir) contains
           one ``model_dump_json()`` line per input message.
        """
        session_id = "test_session_pure_text"
        msgs = [
            UserMsg(name="user", content="Hello, world!"),
            AssistantMsg(name="assistant", content="Hi there!"),
        ]

        file_path = await self.workspace.offload_context(session_id, msgs)

        # Returned path is container-side (/workspace/sessions/...).
        expected_container_path = (
            f"{CONTAINER_SESSIONS_DIR}/{session_id}/context.jsonl"
        )
        self.assertEqual(file_path, expected_container_path)

        # Host-side mirror of that path (bind mount).
        host_path = os.path.join(
            self.temp_dir.name,
            "sessions",
            session_id,
            "context.jsonl",
        )
        self.assertTrue(os.path.exists(host_path))

        async with aiofiles.open(host_path, "r") as f:
            content = await f.read()
        lines = content.strip().split("\n")
        self.assertEqual(len(lines), 2)
        self.assertListEqual(lines, [m.model_dump_json() for m in msgs])

    async def test_offload_context_multiple_calls(self) -> None:
        """Repeated calls append to the same file (proper JSONL).

        Verifies:
        1. Both calls return the same container-side path.
        2. The host-mirror file ends up with one line per message,
           preserving order.
        """
        session_id = "test_session_multiple"
        msgs1 = [
            UserMsg(name="user", content="First message"),
            AssistantMsg(name="assistant", content="First response"),
        ]
        msgs2 = [
            UserMsg(name="user", content="Second message"),
            AssistantMsg(name="assistant", content="Second response"),
        ]

        path1 = await self.workspace.offload_context(session_id, msgs1)
        path2 = await self.workspace.offload_context(session_id, msgs2)
        self.assertEqual(path1, path2)

        host_path = os.path.join(
            self.temp_dir.name,
            "sessions",
            session_id,
            "context.jsonl",
        )
        async with aiofiles.open(host_path, "r") as f:
            content = await f.read()
        lines = content.strip().split("\n")
        self.assertEqual(len(lines), 4)
        self.assertListEqual(
            lines,
            [m.model_dump_json() for m in msgs1 + msgs2],
        )
        for line in lines:
            self.assertIsNotNone(Msg.model_validate_json(line))

    async def test_offload_context_with_datablock(self) -> None:
        """DataBlock(Base64Source) is rewritten to URLSource on a
        separate file.

        Verifies:
        1. The decoded payload lands on disk under ``<workdir>/data/``
           (container-side ``/workspace/data/``).
        2. The offloaded JSONL line carries a ``URLSource`` whose URL is
           the *container* path (``file:///workspace/data/<hash>.<ext>``).
        3. Decoded bytes match the original.
        """
        session_id = "test_session_datablock"
        b64_data = base64.b64encode(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde",
        ).decode()
        data_block = DataBlock(
            source=Base64Source(data=b64_data, media_type="image/png"),
            name="test_image",
        )
        msgs = [
            UserMsg(
                name="user",
                content=[TextBlock(text="Check this image:"), data_block],
            ),
        ]

        file_path = await self.workspace.offload_context(session_id, msgs)
        host_path = os.path.join(
            self.temp_dir.name,
            "sessions",
            session_id,
            "context.jsonl",
        )
        self.assertTrue(os.path.exists(host_path))

        async with aiofiles.open(host_path, "r") as f:
            content = await f.read()
        loaded_msg = Msg.model_validate_json(content.strip())

        # The DataBlock's source should now be URLSource pointing to a
        # container-side file://...
        self.assertEqual(len(loaded_msg.content), 2)
        data_url = str(loaded_msg.content[1].source.url)
        self.assertTrue(data_url.startswith("file:///workspace/data/"))

        # Verify the bytes via the host-side mirror.
        container_path = urlparse(data_url).path  # /workspace/data/<hash>.png
        rel = os.path.relpath(container_path, "/workspace")
        host_data_path = os.path.join(self.temp_dir.name, rel)
        self.assertTrue(os.path.exists(host_data_path))
        async with aiofiles.open(host_data_path, "rb") as f:
            saved = await f.read()
        self.assertEqual(saved, base64.b64decode(b64_data))

        # Sanity-check the returned (container) JSONL path too.
        self.assertEqual(
            file_path,
            f"{CONTAINER_SESSIONS_DIR}/{session_id}/context.jsonl",
        )

    async def test_offload_data_block_deduplication(self) -> None:
        """Two identical DataBlocks share a single persisted file.

        Verifies:
        1. Both ``_offload_data_block`` calls return DataBlocks pointing
           at the same URL.
        2. Only one file ends up in ``<workdir>/data/``.
        """
        b64_data = base64.b64encode(b"test content").decode()
        block1 = DataBlock(
            source=Base64Source(data=b64_data, media_type="text/plain"),
            name="file1",
        )
        block2 = DataBlock(
            source=Base64Source(data=b64_data, media_type="text/plain"),
            name="file2",
        )

        result1 = await self.workspace._offload_data_block(block1)
        result2 = await self.workspace._offload_data_block(block2)
        self.assertEqual(str(result1.source.url), str(result2.source.url))

        # Check the host-mirror data dir has exactly one file.
        host_data_dir = os.path.join(self.temp_dir.name, "data")
        self.assertTrue(os.path.isdir(host_data_dir))
        files = os.listdir(host_data_dir)
        self.assertEqual(len(files), 1)

        # Hash key is sha256 of the *base64 string* (matches LocalWorkspace).
        expected_name = hashlib.sha256(b64_data.encode()).hexdigest() + ".txt"
        self.assertIn(expected_name, files)

    async def test_offload_data_block_url_source(self) -> None:
        """A DataBlock that already has a URLSource is returned unchanged.

        Verifies:
        1. ``_offload_data_block`` is a no-op for URL-sourced blocks.
        2. No file lands in ``<workdir>/data/``.
        """
        from pydantic import AnyUrl

        block = DataBlock(
            source=URLSource(
                url=AnyUrl("https://example.com/image.png"),
                media_type="image/png",
            ),
            name="remote_image",
        )

        result = await self.workspace._offload_data_block(block)
        self.assertDictEqual(result.model_dump(), block.model_dump())

        host_data_dir = os.path.join(self.temp_dir.name, "data")
        if os.path.isdir(host_data_dir):
            self.assertEqual(len(os.listdir(host_data_dir)), 0)

    async def test_offload_tool_result_string(self) -> None:
        """String-output tool result writes the raw string to disk.

        Verifies:
        1. The returned path is the container-side ``tool_result-<id>.txt``.
        2. The host-mirror file contains exactly the output string.
        """
        session_id = "test_session_tool_result"
        tool_result = ToolResultBlock(
            id="tool_123",
            name="test_tool",
            output="Tool execution successful!",
            state=ToolResultState.SUCCESS,
        )

        file_path = await self.workspace.offload_tool_result(
            session_id,
            tool_result,
        )
        self.assertEqual(
            file_path,
            f"{CONTAINER_SESSIONS_DIR}/{session_id}/"
            f"tool_result-{tool_result.id}.txt",
        )

        host_path = os.path.join(
            self.temp_dir.name,
            "sessions",
            session_id,
            f"tool_result-{tool_result.id}.txt",
        )
        self.assertTrue(os.path.exists(host_path))
        async with aiofiles.open(host_path, "r") as f:
            self.assertEqual(await f.read(), "Tool execution successful!")

    async def test_offload_tool_result_with_blocks(self) -> None:
        """Multimodal tool result: text concatenated, DataBlock placeholder.

        Verifies:
        1. The text portion is preserved verbatim.
        2. The DataBlock is rendered as
           ``<data url='…' name='…' media_type='…'/>`` with a
           container-side URL.
        3. The decoded payload is reachable via the host mirror.
        """
        session_id = "test_session_tool_result_blocks"
        b64_data = base64.b64encode(b"test file content").decode()
        data_block = DataBlock(
            source=Base64Source(data=b64_data, media_type="text/plain"),
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

        file_path = await self.workspace.offload_tool_result(
            session_id,
            tool_result,
        )
        host_path = os.path.join(
            self.temp_dir.name,
            "sessions",
            session_id,
            f"tool_result-{tool_result.id}.txt",
        )
        self.assertTrue(os.path.exists(host_path))
        async with aiofiles.open(host_path, "r") as f:
            content = await f.read()

        self.assertTrue(content.startswith("File created successfully: "))
        self.assertIn("<data url='file:///workspace/data/", content)
        self.assertIn("name='output.txt'", content)
        self.assertIn("media_type='text/plain'", content)
        self.assertTrue(content.endswith("/>"))

        # The data file is reachable via the host mirror.
        match = re.search(r"url='([^']+)'", content)
        self.assertIsNotNone(match)
        container_path = urlparse(match.group(1)).path
        rel = os.path.relpath(container_path, "/workspace")
        host_data_path = os.path.join(self.temp_dir.name, rel)
        self.assertTrue(os.path.exists(host_data_path))

        # And the returned path is container-side.
        self.assertEqual(
            file_path,
            f"{CONTAINER_SESSIONS_DIR}/{session_id}/"
            f"tool_result-{tool_result.id}.txt",
        )


# ── skill tests ────────────────────────────────────────────────────


@unittest.skipUnless(_DOCKER_OK, _SKIP_REASON)
class TestDockerWorkspaceSkills(IsolatedAsyncioTestCase):
    """Test cases for DockerWorkspace skill management."""

    async def asyncSetUp(self) -> None:
        """Build separate temp dirs for the workspace and the skill source."""
        # pylint: disable=consider-using-with
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_skills_dir = tempfile.TemporaryDirectory()
        self.workspace: DockerWorkspace | None = None

    async def asyncTearDown(self) -> None:
        """Stop the workspace if started, then drop both temp dirs."""
        try:
            if self.workspace is not None:
                await self.workspace.close()
        finally:
            self.temp_dir.cleanup()
            self.test_skills_dir.cleanup()

    def _create_test_skill(
        self,
        skill_name: str,
        description: str,
        additional_files: dict[str, str] | None = None,
    ) -> str:
        """Create a host-side skill directory containing SKILL.md.

        Args:
            skill_name: Name written to the SKILL.md front matter and
                used as the directory basename.
            description: ``description`` field for the front matter.
            additional_files: Optional ``{filename: content}`` map of
                supplementary files written alongside SKILL.md.

        Returns:
            Absolute path to the created directory.
        """
        skill_dir = os.path.join(self.test_skills_dir.name, skill_name)
        os.makedirs(skill_dir, exist_ok=True)
        skill_md = (
            f"---\nname: {skill_name}\ndescription: {description}\n---\n\n"
            f"# {skill_name}\n\n{description}\n"
        )
        with open(
            os.path.join(skill_dir, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(skill_md)
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
        """``skill_paths`` are copied into the container's ``skills/``.

        Verifies:
        1. Both seed skills appear under ``<workdir>/skills/`` (host
           mirror) — directory + SKILL.md + supplementary files.
        """
        skill1 = self._create_test_skill(
            "test_skill_1",
            "First test skill",
            {"tool.py": "def t():\n    pass\n"},
        )
        skill2 = self._create_test_skill(
            "test_skill_2",
            "Second test skill",
            {"helper.py": "def h():\n    return 42\n"},
        )

        self.workspace = DockerWorkspace(
            workspace_id=f"test-{uuid.uuid4().hex[:8]}",
            host_workdir=self.temp_dir.name,
            skill_paths=[skill1, skill2],
        )
        await self.workspace.initialize()

        skills_host = os.path.join(self.temp_dir.name, "skills")
        self.assertTrue(os.path.isdir(skills_host))
        for name, extra in (
            ("test_skill_1", "tool.py"),
            ("test_skill_2", "helper.py"),
        ):
            target = os.path.join(skills_host, name)
            self.assertTrue(os.path.isdir(target))
            self.assertTrue(
                os.path.isfile(os.path.join(target, "SKILL.md")),
            )
            self.assertTrue(os.path.isfile(os.path.join(target, extra)))

    async def test_list_skills(self) -> None:
        """``list_skills`` enumerates skills via in-container ``find``.

        Verifies:
        1. Every seeded skill is returned with the right name + dir.
        2. The dir field uses the *container-side* path.
        """
        skill1 = self._create_test_skill(
            "list_skill_1",
            "First skill for listing",
        )
        skill2 = self._create_test_skill(
            "list_skill_2",
            "Second skill for listing",
        )

        self.workspace = DockerWorkspace(
            workspace_id=f"test-{uuid.uuid4().hex[:8]}",
            host_workdir=self.temp_dir.name,
            skill_paths=[skill1, skill2],
        )
        await self.workspace.initialize()

        skills = await self.workspace.list_skills()
        self.assertEqual(len(skills), 2)
        skills_sorted = sorted(skills, key=lambda s: s.name)

        self.assertEqual(skills_sorted[0].name, "list_skill_1")
        self.assertEqual(
            skills_sorted[0].description,
            "First skill for listing",
        )
        self.assertEqual(
            skills_sorted[0].dir,
            f"{CONTAINER_SKILLS_DIR}/list_skill_1",
        )

        self.assertEqual(skills_sorted[1].name, "list_skill_2")
        self.assertEqual(
            skills_sorted[1].description,
            "Second skill for listing",
        )
        self.assertEqual(
            skills_sorted[1].dir,
            f"{CONTAINER_SKILLS_DIR}/list_skill_2",
        )

    async def test_list_skills_empty(self) -> None:
        """Empty workspace → empty skill list (no errors)."""
        self.workspace = DockerWorkspace(
            workspace_id=f"test-{uuid.uuid4().hex[:8]}",
            host_workdir=self.temp_dir.name,
        )
        await self.workspace.initialize()
        skills = await self.workspace.list_skills()
        self.assertListEqual(skills, [])

    async def test_add_skill_then_list(self) -> None:
        """``add_skill`` after init shows up in subsequent ``list_skills``."""
        self.workspace = DockerWorkspace(
            workspace_id=f"test-{uuid.uuid4().hex[:8]}",
            host_workdir=self.temp_dir.name,
        )
        await self.workspace.initialize()
        self.assertListEqual(await self.workspace.list_skills(), [])

        new_skill = self._create_test_skill(
            "added_skill",
            "Added at runtime",
        )
        await self.workspace.add_skill(new_skill)

        skills = await self.workspace.list_skills()
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, "added_skill")
        self.assertEqual(skills[0].description, "Added at runtime")

    async def test_add_skill_invalid_no_md(self) -> None:
        """``add_skill`` rejects directories without SKILL.md."""
        self.workspace = DockerWorkspace(
            workspace_id=f"test-{uuid.uuid4().hex[:8]}",
            host_workdir=self.temp_dir.name,
        )
        await self.workspace.initialize()

        invalid = os.path.join(self.test_skills_dir.name, "no_md")
        os.makedirs(invalid, exist_ok=True)
        with open(
            os.path.join(invalid, "tool.py"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("def t():\n    pass\n")

        with self.assertRaises(ValueError):
            await self.workspace.add_skill(invalid)


# ── lifecycle tests ───────────────────────────────────────────────


@unittest.skipUnless(_DOCKER_OK, _SKIP_REASON)
class TestDockerWorkspaceLifecycle(IsolatedAsyncioTestCase):
    """Test cases for DockerWorkspace lifecycle (init / close / restart)."""

    async def asyncSetUp(self) -> None:
        """Per-test temp workdir; workspace built lazily inside each test."""
        # pylint: disable=consider-using-with
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_id = f"test-{uuid.uuid4().hex[:8]}"

    async def asyncTearDown(self) -> None:
        """Best-effort cleanup of the temp workdir."""
        self.temp_dir.cleanup()

    async def test_initialize_idempotent(self) -> None:
        """Calling ``initialize`` on a live workspace is a no-op."""
        ws = DockerWorkspace(
            workspace_id=self.workspace_id,
            host_workdir=self.temp_dir.name,
        )
        try:
            await ws.initialize()
            self.assertTrue(ws.is_alive)
            container = ws._container

            await ws.initialize()  # second call — must be a no-op
            self.assertTrue(ws.is_alive)
            self.assertIs(ws._container, container)
        finally:
            await ws.close()

    async def test_close_marks_inactive(self) -> None:
        """``close`` flips ``is_alive`` and tears the container down."""
        ws = DockerWorkspace(
            workspace_id=self.workspace_id,
            host_workdir=self.temp_dir.name,
        )
        await ws.initialize()
        self.assertTrue(ws.is_alive)
        await ws.close()
        self.assertFalse(ws.is_alive)
        self.assertIsNone(ws._container)
        self.assertIsNone(ws._client)

    async def test_list_mcps_empty(self) -> None:
        """No MCPs registered → ``list_mcps`` returns an empty list."""
        ws = DockerWorkspace(
            workspace_id=self.workspace_id,
            host_workdir=self.temp_dir.name,
        )
        try:
            await ws.initialize()
            self.assertListEqual(await ws.list_mcps(), [])
        finally:
            await ws.close()

    async def test_list_tools_builtin(self) -> None:
        """returns the six builtin tools backed by DockerBackend."""
        from agentscope.tool._builtin import (
            Bash,
            Edit,
            Glob,
            Grep,
            Read,
            Write,
        )
        from agentscope.workspace._docker._docker_backend import DockerBackend

        ws = DockerWorkspace(
            workspace_id=self.workspace_id,
            host_workdir=self.temp_dir.name,
        )
        try:
            await ws.initialize()
            tools = await ws.list_tools()
            self.assertEqual(len(tools), 6)

            expected_types = {Bash, Edit, Glob, Grep, Read, Write}
            actual_types = {type(t) for t in tools}
            self.assertSetEqual(actual_types, expected_types)

            for tool in tools:
                self.assertIsInstance(tool._backend, DockerBackend)
        finally:
            await ws.close()

    async def test_workdir_persistence_across_restart(self) -> None:
        """Same ``workspace_id`` + same ``workdir`` → state survives close.

        Specifically: an offloaded session file written before ``close``
        is still readable from the host mirror after a fresh
        ``initialize`` (the new container re-bind-mounts the same host
        workdir).
        """
        session_id = "persisted_session"
        msg = UserMsg(name="user", content="durable line")

        ws1 = DockerWorkspace(
            workspace_id=self.workspace_id,
            host_workdir=self.temp_dir.name,
        )
        await ws1.initialize()
        await ws1.offload_context(session_id, [msg])
        await ws1.close()

        host_path = os.path.join(
            self.temp_dir.name,
            "sessions",
            session_id,
            "context.jsonl",
        )
        self.assertTrue(os.path.exists(host_path))

        ws2 = DockerWorkspace(
            workspace_id=self.workspace_id,
            host_workdir=self.temp_dir.name,
        )
        try:
            await ws2.initialize()
            # The container mounts the same host dir; the file should
            # still be visible via the host mirror.
            self.assertTrue(os.path.exists(host_path))
            async with aiofiles.open(host_path, "r") as f:
                content = await f.read()
            self.assertEqual(content.strip(), msg.model_dump_json())
        finally:
            await ws2.close()

    async def test_reset_clears_sessions_and_data(self) -> None:
        """``reset`` removes ``sessions/`` and ``data/`` (host-visible)."""
        ws = DockerWorkspace(
            workspace_id=self.workspace_id,
            host_workdir=self.temp_dir.name,
        )
        try:
            await ws.initialize()

            # Seed both directories via offload_*.
            session_id = "reset_session"
            await ws.offload_context(
                session_id,
                [UserMsg(name="user", content="hi")],
            )
            await ws._offload_data_block(
                DataBlock(
                    source=Base64Source(
                        data=base64.b64encode(b"x").decode(),
                        media_type="text/plain",
                    ),
                    name="x",
                ),
            )
            sessions_host = os.path.join(self.temp_dir.name, "sessions")
            data_host = os.path.join(self.temp_dir.name, "data")
            self.assertTrue(os.path.isdir(sessions_host))
            self.assertTrue(os.path.isdir(data_host))

            await ws.reset()
            self.assertFalse(os.path.exists(sessions_host))
            self.assertFalse(os.path.exists(data_host))
        finally:
            await ws.close()


if __name__ == "__main__":
    unittest.main()
