# -*- coding: utf-8 -*-
"""Tests for Phase 4: WorkspaceManager, WorkspaceSkillRepository, WorkspaceTaskRepository."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from agentscope.workspace import (
    WorkspaceManager,
    WorkspaceSkillRepository,
    WorkspaceTaskRepository,
    WorkspaceMode,
)
from agentscope.filesystem import LocalFilesystem, InMemoryStore, RemoteFilesystem
from agentscope.skill import Skill


CTX = {"user_id": "u1", "session_id": "s1"}


# ---------------------------------------------------------------------------
# WorkspaceMode
# ---------------------------------------------------------------------------

class TestWorkspaceMode:
    def test_isolated_value(self) -> None:
        assert WorkspaceMode.ISOLATED == "isolated"

    def test_shared_value(self) -> None:
        assert WorkspaceMode.SHARED == "shared"


# ---------------------------------------------------------------------------
# WorkspaceManager
# ---------------------------------------------------------------------------

class TestWorkspaceManager:
    @pytest.fixture
    def tmp_workspace(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.fixture
    def manager(self, tmp_workspace: Path) -> WorkspaceManager:
        return WorkspaceManager(str(tmp_workspace))

    @pytest.mark.asyncio
    async def test_read_agents_md_local_fallback(self, manager: WorkspaceManager, tmp_workspace: Path) -> None:
        (tmp_workspace / "AGENTS.md").write_text("hello agent")
        assert await manager.read_agents_md(CTX) == "hello agent"

    @pytest.mark.asyncio
    async def test_read_memory_md(self, manager: WorkspaceManager, tmp_workspace: Path) -> None:
        (tmp_workspace / "MEMORY.md").write_text("memory content")
        assert await manager.read_memory_md(CTX) == "memory content"

    @pytest.mark.asyncio
    async def test_read_knowledge_md(self, manager: WorkspaceManager, tmp_workspace: Path) -> None:
        knowledge = tmp_workspace / "knowledge"
        knowledge.mkdir()
        (knowledge / "KNOWLEDGE.md").write_text("knowledge base")
        assert await manager.read_knowledge_md(CTX) == "knowledge base"

    @pytest.mark.asyncio
    async def test_read_managed_workspace_file_utf8(self, manager: WorkspaceManager, tmp_workspace: Path) -> None:
        (tmp_workspace / "sub").mkdir()
        (tmp_workspace / "sub" / "file.txt").write_text("data")
        assert await manager.read_managed_workspace_file_utf8(CTX, "sub/file.txt") == "data"

    @pytest.mark.asyncio
    async def test_read_managed_workspace_file_blocks_traversal(self, manager: WorkspaceManager) -> None:
        assert await manager.read_managed_workspace_file_utf8(CTX, "../outside.txt") == ""

    @pytest.mark.asyncio
    async def test_write_and_read_utf8(self, manager: WorkspaceManager) -> None:
        await manager.write_utf8_workspace_relative(CTX, "a/b.txt", "hello")
        assert await manager.read_managed_workspace_file_utf8(CTX, "a/b.txt") == "hello"

    @pytest.mark.asyncio
    async def test_append_utf8(self, manager: WorkspaceManager) -> None:
        await manager.append_utf8_workspace_relative(CTX, "log.txt", "line1\n")
        await manager.append_utf8_workspace_relative(CTX, "log.txt", "line2\n")
        assert await manager.read_managed_workspace_file_utf8(CTX, "log.txt") == "line1\nline2\n"

    @pytest.mark.asyncio
    async def test_list_memory_file_paths(self, manager: WorkspaceManager, tmp_workspace: Path) -> None:
        (tmp_workspace / "memory").mkdir()
        (tmp_workspace / "memory" / "2024-01-01.md").write_text("x")
        (tmp_workspace / "MEMORY.md").write_text("root")
        paths = await manager.list_memory_file_paths(CTX)
        assert "MEMORY.md" in paths
        assert "memory/2024-01-01.md" in paths

    @pytest.mark.asyncio
    async def test_list_knowledge_files(self, manager: WorkspaceManager, tmp_workspace: Path) -> None:
        knowledge = tmp_workspace / "knowledge"
        knowledge.mkdir()
        (knowledge / "a.md").write_text("a")
        files = await manager.list_knowledge_files(CTX)
        assert any("a.md" in str(f) for f in files)

    @pytest.mark.asyncio
    async def test_session_index(self, manager: WorkspaceManager) -> None:
        await manager.update_session_index(CTX, "agent1", "sess1", "summary one")
        await manager.update_session_index(CTX, "agent1", "sess2", "summary two")
        content = await manager.read_managed_workspace_file_utf8(
            CTX, "agents/agent1/sessions/sessions.json",
        )
        data = json.loads(content)
        assert "sess1" in data["sessions"]
        assert data["sessions"]["sess1"]["summary"] == "summary one"

    @pytest.mark.asyncio
    async def test_task_record_crud(self, manager: WorkspaceManager) -> None:
        rec = {"taskId": "t1", "status": "PENDING"}
        await manager.write_task_record(CTX, "agent1", "sess1", rec)
        fetched = await manager.read_task_record(CTX, "agent1", "sess1", "t1")
        assert fetched is not None
        assert fetched["taskId"] == "t1"
        assert fetched["status"] == "PENDING"

        all_recs = await manager.list_task_records(CTX, "agent1", "sess1")
        assert len(all_recs) == 1

    @pytest.mark.asyncio
    async def test_sweep_marker(self, manager: WorkspaceManager) -> None:
        assert await manager.read_sweep_marker(CTX, "agent1") is None
        await manager.write_sweep_marker(CTX, "agent1")
        assert await manager.read_sweep_marker(CTX, "agent1") is not None

    @pytest.mark.asyncio
    async def test_move_skill(self, manager: WorkspaceManager, tmp_workspace: Path) -> None:
        # move_skill requires a filesystem backend
        fs = LocalFilesystem(str(tmp_workspace))
        mgr = WorkspaceManager(str(tmp_workspace), filesystem=fs)
        await fs.write(CTX, "skills/old/SKILL.md", "---\nname: old\ndescription: d\n---\n")
        ok = await mgr.move_skill(CTX, "skills/old", "skills/new")
        assert ok is True
        assert (tmp_workspace / "skills" / "new" / "SKILL.md").exists()

    @pytest.mark.asyncio
    async def test_two_layer_read_fs_overrides_local(self, tmp_workspace: Path) -> None:
        (tmp_workspace / "AGENTS.md").write_text("local")
        fs = RemoteFilesystem(InMemoryStore())
        await fs.write(CTX, "AGENTS.md", "remote")
        mgr = WorkspaceManager(str(tmp_workspace), filesystem=fs)
        assert await mgr.read_agents_md(CTX) == "remote"

    @pytest.mark.asyncio
    async def test_two_layer_read_local_fallback(self, tmp_workspace: Path) -> None:
        (tmp_workspace / "AGENTS.md").write_text("local only")
        mgr = WorkspaceManager(str(tmp_workspace), filesystem=RemoteFilesystem(InMemoryStore()))
        assert await mgr.read_agents_md(CTX) == "local only"


# ---------------------------------------------------------------------------
# WorkspaceSkillRepository
# ---------------------------------------------------------------------------

class TestWorkspaceSkillRepository:
    @pytest.fixture
    def tmp_workspace(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.fixture
    def repo(self, tmp_workspace: Path) -> WorkspaceSkillRepository:
        fs = LocalFilesystem(str(tmp_workspace))
        return WorkspaceSkillRepository(
            fs,
            "skills",
            context_supplier=lambda: CTX,
            writable=True,
        )

    @pytest.mark.asyncio
    async def test_get_all_skills_empty(self, repo: WorkspaceSkillRepository) -> None:
        assert await repo.get_all_skills() == []

    @pytest.mark.asyncio
    async def test_save_and_get_skill(self, repo: WorkspaceSkillRepository, tmp_workspace: Path) -> None:
        skill = Skill(
            name="test_skill",
            description="A test skill",
            dir="skills/test_skill",
            markdown="body here",
            updated_at=0.0,
            metadata={"tags": ["test"]},
        )
        ok = await repo.save([skill])
        assert ok is True

        fetched = await repo.get_skill("test_skill")
        assert fetched is not None
        assert fetched.name == "test_skill"
        assert fetched.description == "A test skill"

    @pytest.mark.asyncio
    async def test_skill_exists(self, repo: WorkspaceSkillRepository) -> None:
        skill = Skill(
            name="existing",
            description="d",
            dir="skills/existing",
            markdown="m",
            updated_at=0.0,
        )
        await repo.save([skill])
        assert await repo.skill_exists("existing") is True
        assert await repo.skill_exists("missing") is False

    @pytest.mark.asyncio
    async def test_delete_archives_skill(self, repo: WorkspaceSkillRepository, tmp_workspace: Path) -> None:
        skill = Skill(
            name="to_delete",
            description="d",
            dir="skills/to_delete",
            markdown="m",
            updated_at=0.0,
        )
        await repo.save([skill])
        assert await repo.skill_exists("to_delete") is True
        ok = await repo.delete("to_delete")
        assert ok is True
        assert await repo.skill_exists("to_delete") is False
        # archived under .archive/
        archive_dir = tmp_workspace / "skills" / ".archive"
        assert any("to_delete" in d.name for d in archive_dir.iterdir())

    @pytest.mark.asyncio
    async def test_read_write_delete_skill_file(self, repo: WorkspaceSkillRepository, tmp_workspace: Path) -> None:
        skill = Skill(
            name="file_test",
            description="d",
            dir="skills/file_test",
            markdown="m",
            updated_at=0.0,
        )
        await repo.save([skill])
        ok = await repo.write_skill_file("file_test", "script.py", "print(1)")
        assert ok is True
        content = await repo.read_skill_file("file_test", "script.py")
        assert content == "print(1)"
        ok = await repo.delete_skill_file("file_test", "script.py")
        assert ok is True
        assert await repo.read_skill_file("file_test", "script.py") is None

    @pytest.mark.asyncio
    async def test_resources_for(self, repo: WorkspaceSkillRepository, tmp_workspace: Path) -> None:
        skill = Skill(
            name="res_skill",
            description="d",
            dir="skills/res_skill",
            markdown="m",
            updated_at=0.0,
            resources={"ref.md": "reference content"},
        )
        await repo.save([skill])
        resources = await repo.resources_for("res_skill")
        assert "ref.md" in resources
        assert resources["ref.md"] == "reference content"


# ---------------------------------------------------------------------------
# WorkspaceTaskRepository
# ---------------------------------------------------------------------------

class TestWorkspaceTaskRepository:
    @pytest.fixture
    def tmp_workspace(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.fixture
    def repo(self, tmp_workspace: Path) -> WorkspaceTaskRepository:
        wm = WorkspaceManager(str(tmp_workspace))
        return WorkspaceTaskRepository(wm, "parent_agent")

    @pytest.mark.asyncio
    async def test_put_and_get_task(self, repo: WorkspaceTaskRepository) -> None:
        async def dummy() -> str:
            await asyncio.sleep(0.01)
            return "done"

        task = await repo.put_task(CTX, "t1", "sess1", dummy())
        assert isinstance(task, asyncio.Task)
        result = await task
        assert result == "done"

        record = await repo.get_task(CTX, "sess1", "t1")
        assert record is not None
        assert record["status"] == "COMPLETED"
        assert record["result"] == "done"

    @pytest.mark.asyncio
    async def test_cancel_task(self, repo: WorkspaceTaskRepository) -> None:
        async def slow() -> str:
            await asyncio.sleep(10)
            return "never"

        task = await repo.put_task(CTX, "t2", "sess1", slow())
        await asyncio.sleep(0.01)  # let it start
        ok = await repo.cancel_task(CTX, "sess1", "t2")
        assert ok is True

        with pytest.raises(asyncio.CancelledError):
            await task

        record = await repo.get_task(CTX, "sess1", "t2")
        assert record is not None
        assert record["status"] == "CANCELLED"
        assert record["cancelRequested"] is True

    @pytest.mark.asyncio
    async def test_failed_task(self, repo: WorkspaceTaskRepository) -> None:
        async def bad() -> str:
            raise ValueError("oops")

        task = await repo.put_task(CTX, "t3", "sess1", bad())
        with pytest.raises(ValueError):
            await task

        record = await repo.get_task(CTX, "sess1", "t3")
        assert record is not None
        assert record["status"] == "FAILED"
        assert "oops" in record["errorMessage"]

    @pytest.mark.asyncio
    async def test_list_tasks(self, repo: WorkspaceTaskRepository) -> None:
        async def dummy() -> str:
            return "x"

        await repo.put_task(CTX, "t4", "sess2", dummy())
        await repo.put_task(CTX, "t5", "sess2", dummy())
        # wait for completion
        await asyncio.sleep(0.05)
        tasks = await repo.list_tasks(CTX, "sess2")
        assert len(tasks) == 2
