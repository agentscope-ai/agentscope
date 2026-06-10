# -*- coding: utf-8 -*-
"""Tests for WorkspaceSpec, WorkspaceSpecApplier, Snapshot layer."""

import io
import os
import tarfile
from pathlib import Path

import pytest

from agentscope.sandbox import (
    WorkspaceSpec,
    FileEntry,
    DirEntry,
    LocalFileEntry,
    LocalDirEntry,
    BindMountEntry,
    GitRepoEntry,
    WorkspaceProjectionEntry,
    WorkspaceSpecApplier,
    WorkspaceArchiveExtractor,
    ArchiveExtractError,
    WorkspaceProjectionApplier,
    ProjectionPayload,
    NoopSandboxSnapshot,
    NoopSnapshotSpec,
    LocalSandboxSnapshot,
    LocalSnapshotSpec,
)


# ---------------------------------------------------------------------------
# WorkspaceSpecApplier
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


def test_applier_file_entry(tmp_dir):
    spec = WorkspaceSpec(
        entries={"hello.txt": FileEntry(content="world", encoding="utf-8")},
    )
    WorkspaceSpecApplier().apply(spec, tmp_dir)
    assert (tmp_dir / "hello.txt").read_text(encoding="utf-8") == "world"


def test_applier_dir_entry(tmp_dir):
    spec = WorkspaceSpec(
        entries={
            "config": DirEntry(
                children={
                    "app.json": FileEntry(content='{"port":8080}'),
                },
            ),
        },
    )
    WorkspaceSpecApplier().apply(spec, tmp_dir)
    assert (tmp_dir / "config" / "app.json").read_text() == '{"port":8080}'


def test_applier_local_file_entry(tmp_dir):
    src = tmp_dir / "src.txt"
    src.write_text("source data")
    spec = WorkspaceSpec(
        entries={"dest.txt": LocalFileEntry(source_path=str(src))},
    )
    WorkspaceSpecApplier().apply(spec, tmp_dir / "out")
    assert (tmp_dir / "out" / "dest.txt").read_text() == "source data"


def test_applier_local_dir_entry(tmp_dir):
    src = tmp_dir / "tree"
    src.mkdir()
    (src / "a.txt").write_text("A")
    (src / "sub").mkdir()
    (src / "sub" / "b.txt").write_text("B")
    spec = WorkspaceSpec(
        entries={"copied": LocalDirEntry(source_path=str(src))},
    )
    WorkspaceSpecApplier().apply(spec, tmp_dir / "out")
    assert (tmp_dir / "out" / "copied" / "a.txt").read_text() == "A"
    assert (tmp_dir / "out" / "copied" / "sub" / "b.txt").read_text() == "B"


def test_applier_skips_bindmount_and_gitrepo(tmp_dir, caplog):
    spec = WorkspaceSpec(
        entries={
            "mnt": BindMountEntry(host_path="/host/data"),
            "repo": GitRepoEntry(url="https://example.com/repo.git"),
        },
    )
    WorkspaceSpecApplier().apply(spec, tmp_dir)
    assert not (tmp_dir / "mnt").exists()
    assert not (tmp_dir / "repo").exists()


def test_applier_only_ephemeral(tmp_dir):
    spec = WorkspaceSpec(
        entries={
            "persisted.txt": FileEntry(content="old"),
            "ephemeral.txt": FileEntry(content="new", ephemeral=True),
        },
    )
    WorkspaceSpecApplier().apply(spec, tmp_dir, only_ephemeral=True)
    assert not (tmp_dir / "persisted.txt").exists()
    assert (tmp_dir / "ephemeral.txt").read_text() == "new"


def test_applier_path_traversal_guard(tmp_dir, caplog):
    # Create a sentinel directory outside tmp_dir to detect escape
    sentinel = tmp_dir.parent / "sentinel_escape_test"
    sentinel.mkdir(exist_ok=True)
    rel_escape = "../sentinel_escape_test/hacked.txt"
    spec = WorkspaceSpec(
        entries={rel_escape: FileEntry(content="bad")},
    )
    WorkspaceSpecApplier().apply(spec, tmp_dir)
    assert not (sentinel / "hacked.txt").exists()


# ---------------------------------------------------------------------------
# WorkspaceArchiveExtractor
# ---------------------------------------------------------------------------


def test_extractor_normal(tmp_dir):
    # Build a simple tar
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        data = b"hello world"
        info = tarfile.TarInfo(name="hello.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    buf.seek(0)

    WorkspaceArchiveExtractor.extract_tar_archive(tmp_dir, buf)
    assert (tmp_dir / "hello.txt").read_bytes() == b"hello world"


def test_extractor_rejects_absolute_path(tmp_dir):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        data = b"evil"
        info = tarfile.TarInfo(name="/etc/passwd")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    buf.seek(0)

    with pytest.raises(ArchiveExtractError, match="absolute path"):
        WorkspaceArchiveExtractor.extract_tar_archive(tmp_dir, buf)


def test_extractor_rejects_dotdot(tmp_dir):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        data = b"evil"
        info = tarfile.TarInfo(name="../secret.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    buf.seek(0)

    with pytest.raises(ArchiveExtractError, match="\\.\\."):
        WorkspaceArchiveExtractor.extract_tar_archive(tmp_dir, buf)


# ---------------------------------------------------------------------------
# WorkspaceProjectionApplier
# ---------------------------------------------------------------------------


def test_projection_payload_hash(tmp_dir):
    # Create source tree
    src = tmp_dir / "src"
    src.mkdir()
    (src / "a.txt").write_text("A")
    (src / "b.txt").write_text("B")

    spec = WorkspaceSpec(
        entries={
            "proj": WorkspaceProjectionEntry(
                source_root=str(src),
                include_roots=["."],
            ),
        },
    )
    payload = WorkspaceProjectionApplier.build(spec)
    assert isinstance(payload, ProjectionPayload)
    assert payload.file_count == 2
    assert len(payload.hash) == 64  # SHA-256 hex
    assert len(payload.tar_bytes) > 0

    # Same tree → same hash
    payload2 = WorkspaceProjectionApplier.build(spec)
    assert payload2.hash == payload.hash

    # Modify tree → different hash
    (src / "c.txt").write_text("C")
    payload3 = WorkspaceProjectionApplier.build(spec)
    assert payload3.hash != payload.hash


# ---------------------------------------------------------------------------
# Snapshot layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_noop_snapshot_discards():
    snap = NoopSandboxSnapshot("test-id")
    assert snap.type == "noop"
    assert not snap.is_persistence_enabled()
    assert not await snap.is_restorable()

    buf = io.BytesIO(b"data")
    await snap.persist(buf)

    with pytest.raises(FileNotFoundError):
        await snap.restore()


@pytest.mark.asyncio
async def test_local_snapshot_round_trip(tmp_dir):
    spec = LocalSnapshotSpec(str(tmp_dir))
    snap = spec.build("session-abc")
    assert snap.type == "local"

    data = b"tar archive contents"
    await snap.persist(io.BytesIO(data))
    assert await snap.is_restorable()

    stream = await snap.restore()
    assert stream.read() == data
    stream.close()

    # Snapshot ID validation
    with pytest.raises(ValueError, match="forbidden"):
        spec.build("../escape")


@pytest.mark.asyncio
async def test_local_snapshot_atomic_write(tmp_dir):
    spec = LocalSnapshotSpec(str(tmp_dir))
    snap = spec.build("atomic")

    await snap.persist(io.BytesIO(b"first"))
    # Should not leave temp files
    temps = list(tmp_dir.glob(".*.tmp"))
    assert len(temps) == 0
