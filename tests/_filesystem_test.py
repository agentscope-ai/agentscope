# -*- coding: utf-8 -*-
"""Tests for the filesystem abstraction layer (Phase 3)."""
from __future__ import annotations

import pytest
import asyncio
from pathlib import Path

from agentscope.filesystem import (
    LocalFilesystem,
    LocalFsMode,
    RemoteFilesystem,
    CompositeFilesystem,
    OverlayFilesystem,
    InMemoryStore,
    NamespaceFactory,
    WorkspaceIndex,
)


CTX = {"user_id": "u1", "session_id": "s1"}


# ---------------------------------------------------------------------------
# LocalFilesystem
# ---------------------------------------------------------------------------

class TestLocalFilesystem:
    @pytest.fixture
    def tmp_root(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.fixture
    def sandboxed(self, tmp_root: Path) -> LocalFilesystem:
        return LocalFilesystem(str(tmp_root), mode=LocalFsMode.SANDBOXED)

    @pytest.mark.asyncio
    async def test_write_and_read(self, sandboxed: LocalFilesystem) -> None:
        await sandboxed.write(CTX, "hello.txt", "world")
        result = await sandboxed.read(CTX, "hello.txt")
        assert result.content == "world"
        assert result.total_lines == 1

    @pytest.mark.asyncio
    async def test_ls(self, sandboxed: LocalFilesystem) -> None:
        await sandboxed.write(CTX, "a.txt", "A")
        await sandboxed.write(CTX, "b/c.txt", "C")
        ls = await sandboxed.ls(CTX, "/")
        names = {e.name for e in ls.entries}
        assert "a.txt" in names
        assert "b" in names

    @pytest.mark.asyncio
    async def test_exists_and_delete(self, sandboxed: LocalFilesystem) -> None:
        await sandboxed.write(CTX, "x.txt", "X")
        assert await sandboxed.exists(CTX, "x.txt")
        await sandboxed.delete(CTX, "x.txt")
        assert not await sandboxed.exists(CTX, "x.txt")

    @pytest.mark.asyncio
    async def test_move(self, sandboxed: LocalFilesystem) -> None:
        await sandboxed.write(CTX, "old.txt", "data")
        await sandboxed.move(CTX, "old.txt", "new.txt")
        assert not await sandboxed.exists(CTX, "old.txt")
        assert (await sandboxed.read(CTX, "new.txt")).content == "data"

    @pytest.mark.asyncio
    async def test_edit_single(self, sandboxed: LocalFilesystem) -> None:
        await sandboxed.write(CTX, "f.txt", "hello world")
        edit = await sandboxed.edit(CTX, "f.txt", "world", "universe")
        assert edit.replacements == 1
        assert (await sandboxed.read(CTX, "f.txt")).content == "hello universe"

    @pytest.mark.asyncio
    async def test_edit_all(self, sandboxed: LocalFilesystem) -> None:
        await sandboxed.write(CTX, "f.txt", "a a a")
        edit = await sandboxed.edit(CTX, "f.txt", "a", "b", replace_all=True)
        assert edit.replacements == 3
        assert (await sandboxed.read(CTX, "f.txt")).content == "b b b"

    @pytest.mark.asyncio
    async def test_grep(self, sandboxed: LocalFilesystem) -> None:
        await sandboxed.write(CTX, "src.py", "import os\nprint(1)\n")
        result = await sandboxed.grep(CTX, "print", "/")
        assert len(result.matches) == 1
        assert "print(1)" in result.matches[0].line_text

    @pytest.mark.asyncio
    async def test_glob(self, sandboxed: LocalFilesystem) -> None:
        await sandboxed.write(CTX, "a.txt", "")
        await sandboxed.write(CTX, "b.txt", "")
        result = await sandboxed.glob(CTX, "*.txt", "/")
        assert len(result.paths) == 2

    @pytest.mark.asyncio
    async def test_sandboxed_escapes_blocked(self, tmp_root: Path) -> None:
        fs = LocalFilesystem(str(tmp_root), mode=LocalFsMode.SANDBOXED)
        with pytest.raises(PermissionError):
            await fs.read(CTX, "../outside.txt")

    @pytest.mark.asyncio
    async def test_unrestricted_absolute(self, tmp_root: Path) -> None:
        fs = LocalFilesystem(str(tmp_root), mode=LocalFsMode.UNRESTRICTED)
        outside = tmp_root.parent / "outside.txt"
        outside.write_text("ok")
        result = await fs.read(CTX, str(outside))
        assert result.content == "ok"

    @pytest.mark.asyncio
    async def test_read_offset_limit(self, sandboxed: LocalFilesystem) -> None:
        content = "\n".join(f"line {i}" for i in range(10))
        await sandboxed.write(CTX, "ten.txt", content)
        result = await sandboxed.read(CTX, "ten.txt", offset=3, limit=3)
        assert result.content == "line 3\nline 4\nline 5"
        assert result.offset == 3


# ---------------------------------------------------------------------------
# RemoteFilesystem
# ---------------------------------------------------------------------------

class TestRemoteFilesystem:
    @pytest.fixture
    def remote(self) -> RemoteFilesystem:
        return RemoteFilesystem(InMemoryStore())

    @pytest.mark.asyncio
    async def test_write_and_read(self, remote: RemoteFilesystem) -> None:
        await remote.write(CTX, "a.txt", "hello")
        result = await remote.read(CTX, "a.txt")
        assert result.content == "hello"

    @pytest.mark.asyncio
    async def test_exists_and_delete(self, remote: RemoteFilesystem) -> None:
        await remote.write(CTX, "b.txt", "B")
        assert await remote.exists(CTX, "b.txt")
        await remote.delete(CTX, "b.txt")
        assert not await remote.exists(CTX, "b.txt")

    @pytest.mark.asyncio
    async def test_edit_cas(self, remote: RemoteFilesystem) -> None:
        await remote.write(CTX, "c.txt", "foo bar baz")
        edit = await remote.edit(CTX, "c.txt", "bar", "qux")
        assert edit.replacements == 1
        assert (await remote.read(CTX, "c.txt")).content == "foo qux baz"

    @pytest.mark.asyncio
    async def edit_retries_on_conflict(self, remote: RemoteFilesystem) -> None:
        await remote.write(CTX, "d.txt", "x")
        # Manually simulate a concurrent edit by mocking store.get to return
        # different versions on first two calls.
        calls = [0]
        orig_get = remote._store.get

        async def fake_get(key):
            calls[0] += 1
            if calls[0] <= 2:
                # Return stale version
                from agentscope.filesystem._base_store import StoreValue
                return StoreValue(data=b"x", version=calls[0])
            return await orig_get(key)

        remote._store.get = fake_get  # type: ignore[method-assign]
        edit = await remote.edit(CTX, "d.txt", "x", "y")
        assert edit.replacements == 1
        assert (await remote.read(CTX, "d.txt")).content == "y"

    @pytest.mark.asyncio
    async def test_move(self, remote: RemoteFilesystem) -> None:
        await remote.write(CTX, "src.txt", "data")
        await remote.move(CTX, "src.txt", "dst.txt")
        assert not await remote.exists(CTX, "src.txt")
        assert (await remote.read(CTX, "dst.txt")).content == "data"

    @pytest.mark.asyncio
    async def test_ls_and_glob(self, remote: RemoteFilesystem) -> None:
        await remote.write(CTX, "dir/a.txt", "A")
        await remote.write(CTX, "dir/b.py", "B")
        ls = await remote.ls(CTX, "dir")
        names = {e.name for e in ls.entries}
        assert "a.txt" in names
        assert "b.py" in names

        glob = await remote.glob(CTX, "*.txt", "dir")
        assert glob.paths == ["dir/a.txt"]

    @pytest.mark.asyncio
    async def test_grep(self, remote: RemoteFilesystem) -> None:
        await remote.write(CTX, "x.py", "import os\nprint(1)\n")
        result = await remote.grep(CTX, "print", "/")
        assert len(result.matches) == 1
        assert "print(1)" in result.matches[0].line_text


# ---------------------------------------------------------------------------
# CompositeFilesystem
# ---------------------------------------------------------------------------

class TestCompositeFilesystem:
    @pytest.fixture
    def tmp_root(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.fixture
    def composite(self, tmp_root: Path) -> CompositeFilesystem:
        default = LocalFilesystem(str(tmp_root / "default"))
        tmp = LocalFilesystem(str(tmp_root / "tmp"))
        proj = LocalFilesystem(str(tmp_root / "projects"))
        comp = CompositeFilesystem(default=default)
        comp.mount("/tmp", tmp)
        comp.mount("/projects", proj)
        return comp

    @pytest.mark.asyncio
    async def test_routing(self, composite: CompositeFilesystem) -> None:
        await composite.write(CTX, "/tmp/a.txt", "A")
        await composite.write(CTX, "/projects/b.txt", "B")
        await composite.write(CTX, "/c.txt", "C")

        assert (await composite.read(CTX, "/tmp/a.txt")).content == "A"
        assert (await composite.read(CTX, "/projects/b.txt")).content == "B"
        assert (await composite.read(CTX, "/c.txt")).content == "C"

    @pytest.mark.asyncio
    async def test_cross_backend_move(self, composite: CompositeFilesystem) -> None:
        await composite.write(CTX, "/tmp/src.txt", "data")
        await composite.move(CTX, "/tmp/src.txt", "/projects/dst.txt")
        assert not await composite.exists(CTX, "/tmp/src.txt")
        assert (await composite.read(CTX, "/projects/dst.txt")).content == "data"

    @pytest.mark.asyncio
    async def test_root_grep_aggregation(self, composite: CompositeFilesystem) -> None:
        await composite.write(CTX, "/tmp/x.txt", "hello")
        await composite.write(CTX, "/projects/y.txt", "hello")
        result = await composite.grep(CTX, "hello", "/")
        assert len(result.matches) == 2

    @pytest.mark.asyncio
    async def test_root_glob_aggregation(self, composite: CompositeFilesystem) -> None:
        await composite.write(CTX, "/tmp/1.txt", "")
        await composite.write(CTX, "/projects/2.txt", "")
        result = await composite.glob(CTX, "*.txt", "/")
        assert len(result.paths) == 2


# ---------------------------------------------------------------------------
# OverlayFilesystem
# ---------------------------------------------------------------------------

class TestOverlayFilesystem:
    @pytest.fixture
    def overlay(self, tmp_path: Path) -> OverlayFilesystem:
        lower = LocalFilesystem(str(tmp_path / "lower"))
        upper = LocalFilesystem(str(tmp_path / "upper"))
        return OverlayFilesystem(upper=upper, lower=lower)

    @pytest.mark.asyncio
    async def test_read_from_lower(self, overlay: OverlayFilesystem) -> None:
        await overlay._lower.write(CTX, "base.txt", "base")
        result = await overlay.read(CTX, "base.txt")
        assert result.content == "base"

    @pytest.mark.asyncio
    async def test_upper_masks_lower(self, overlay: OverlayFilesystem) -> None:
        await overlay._lower.write(CTX, "shared.txt", "lower")
        await overlay._upper.write(CTX, "shared.txt", "upper")
        result = await overlay.read(CTX, "shared.txt")
        assert result.content == "upper"

    @pytest.mark.asyncio
    async def test_write_only_upper(self, overlay: OverlayFilesystem) -> None:
        await overlay.write(CTX, "new.txt", "only upper")
        assert await overlay._upper.exists(CTX, "new.txt")
        assert not await overlay._lower.exists(CTX, "new.txt")

    @pytest.mark.asyncio
    async def test_edit_promotes_from_lower(self, overlay: OverlayFilesystem) -> None:
        await overlay._lower.write(CTX, "promo.txt", "old")
        edit = await overlay.edit(CTX, "promo.txt", "old", "new")
        assert edit.replacements == 1
        assert (await overlay._upper.read(CTX, "promo.txt")).content == "new"
        assert (await overlay._lower.read(CTX, "promo.txt")).content == "old"

    @pytest.mark.asyncio
    async def test_delete_only_upper(self, overlay: OverlayFilesystem) -> None:
        await overlay._lower.write(CTX, "del.txt", "exists")
        await overlay.delete(CTX, "del.txt")
        # lower still has it, upper deleted (or no-op if not in upper)
        assert await overlay.exists(CTX, "del.txt")
        assert not await overlay._upper.exists(CTX, "del.txt")


# ---------------------------------------------------------------------------
# WorkspaceIndex
# ---------------------------------------------------------------------------

class TestWorkspaceIndex:
    @pytest.fixture
    def index(self, tmp_path: Path) -> WorkspaceIndex:
        return WorkspaceIndex(tmp_path / "index.db")

    @pytest.mark.asyncio
    async def test_upsert_and_list(self, index: WorkspaceIndex) -> None:
        await index.upsert("/a.txt", size=10, mtime=1.0)
        await index.upsert("/dir/b.txt", size=20, mtime=2.0)
        children = await index.list_children("/")
        names = [c["name"] for c in children]
        assert "a.txt" in names
        assert "dir" in names

    @pytest.mark.asyncio
    async def test_remove(self, index: WorkspaceIndex) -> None:
        await index.upsert("/x.txt", size=5)
        await index.remove("/x.txt")
        children = await index.list_children("/")
        assert not any(c["name"] == "x.txt" for c in children)

    @pytest.mark.asyncio
    async def test_clear(self, index: WorkspaceIndex) -> None:
        await index.upsert("/y.txt", size=5)
        await index.clear()
        children = await index.list_children("/")
        assert children == []
