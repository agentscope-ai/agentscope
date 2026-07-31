# -*- coding: utf-8 -*-
"""Turn a browser folder upload into a tar stream for a workspace.

The browser sends one part per file plus a manifest of paths and sizes.
The manifest is what makes streaming possible at all: a tar header must
carry the member size *before* its bytes, and multipart parts do not
declare their length. Sizes are therefore taken on trust to build the
headers and verified as the bytes go by — a mismatch aborts the install
before anything is renamed into place.
"""
import asyncio
import tarfile
from typing import AsyncIterator

from fastapi import UploadFile
from pydantic import BaseModel, Field

#: Ceilings on one upload. Peak memory is a chunk per concurrent
#: install; these bound the sandbox's disk and the time a slot is held.
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_FILE_COUNT = 100
MAX_CONCURRENT_INSTALLS = 5

_CHUNK_SIZE = 64 * 1024
_TAR_BLOCK = 512

#: Serializes installs process-wide, so a burst of uploads cannot
#: multiply into an unbounded number of open sandbox writes.
_install_slots = asyncio.Semaphore(MAX_CONCURRENT_INSTALLS)


class SkillUploadError(ValueError):
    """Raised when an upload is malformed or over a limit."""


class UploadEntry(BaseModel):
    """One file in a folder upload, as the browser described it."""

    path: str = Field(
        description=(
            "The file's path relative to the picked folder's parent, "
            "i.e. ``webkitRelativePath``."
        ),
    )
    size: int = Field(ge=0, description="The file's size in bytes.")


class UploadManifest(BaseModel):
    """The declared contents of a folder upload."""

    entries: list[UploadEntry]


def _validate_manifest(manifest: UploadManifest) -> str:
    """Check a manifest and return the skill's root directory name.

    Args:
        manifest (`UploadManifest`):
            The declared upload contents.

    Returns:
        `str`:
            The single top-level directory every path sits under.

    Raises:
        SkillUploadError:
            If a limit is exceeded, a path is unsafe, the paths span
            more than one root, or no ``SKILL.md`` is present.
    """
    entries = manifest.entries
    if not entries:
        raise SkillUploadError("The upload contains no files.")
    if len(entries) > MAX_FILE_COUNT:
        raise SkillUploadError(
            f"A skill may hold at most {MAX_FILE_COUNT} files, "
            f"got {len(entries)}.",
        )

    total = 0
    roots: set[str] = set()
    for entry in entries:
        if entry.size > MAX_FILE_BYTES:
            raise SkillUploadError(
                f"{entry.path!r} is {entry.size} bytes, over the "
                f"{MAX_FILE_BYTES}-byte per-file limit.",
            )
        total += entry.size

        parts = entry.path.replace("\\", "/").split("/")
        if len(parts) < 2 or any(p in ("", ".", "..") for p in parts):
            raise SkillUploadError(f"Unsafe upload path: {entry.path!r}")
        if entry.path.startswith("/"):
            raise SkillUploadError(f"Unsafe upload path: {entry.path!r}")
        roots.add(parts[0])

    if total > MAX_TOTAL_BYTES:
        raise SkillUploadError(
            f"The upload is {total} bytes, over the "
            f"{MAX_TOTAL_BYTES}-byte limit.",
        )
    if len(roots) != 1:
        raise SkillUploadError(
            f"A skill must be a single folder, got {sorted(roots)}.",
        )

    root = roots.pop()
    if not any(e.path == f"{root}/SKILL.md" for e in manifest.entries):
        raise SkillUploadError(
            f"No SKILL.md at the root of {root!r}.",
        )
    return root


async def _tar_stream(
    manifest: UploadManifest,
    files: list[UploadFile],
) -> AsyncIterator[bytes]:
    """Emit a tar of the uploaded files, chunk by chunk.

    Args:
        manifest (`UploadManifest`):
            The validated manifest; its order must match ``files``.
        files (`list[UploadFile]`):
            The uploaded parts, in manifest order.

    Yields:
        `bytes`:
            The next chunk of the tar archive.

    Raises:
        SkillUploadError:
            If a part's real length differs from its declared size.
    """
    for entry, upload in zip(manifest.entries, files):
        info = tarfile.TarInfo(name=entry.path)
        info.size = entry.size
        info.mtime = 0
        yield info.tobuf(tarfile.GNU_FORMAT)

        written = 0
        while chunk := await upload.read(_CHUNK_SIZE):
            written += len(chunk)
            if written > entry.size:
                raise SkillUploadError(
                    f"{entry.path!r} is larger than its declared "
                    f"{entry.size} bytes.",
                )
            yield chunk

        if written != entry.size:
            raise SkillUploadError(
                f"{entry.path!r} declared {entry.size} bytes but sent "
                f"{written}.",
            )
        padding = -entry.size % _TAR_BLOCK
        if padding:
            yield b"\0" * padding

    # Two zero blocks mark the end of a tar archive.
    yield b"\0" * (2 * _TAR_BLOCK)
