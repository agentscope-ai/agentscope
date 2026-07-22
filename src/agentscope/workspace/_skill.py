# -*- coding: utf-8 -*-
"""Validation and packaging helpers for workspace skill archives."""

import hashlib
import io
import os
import re
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

import frontmatter
from yaml import YAMLError

MAX_SKILL_ARCHIVE_SIZE = 50 * 1024 * 1024
MAX_SKILL_FILES = 1000


@dataclass(frozen=True)
class SkillArchiveMetadata:
    """Validated metadata read from an uploaded skill archive."""

    name: str
    description: str
    content_hash: str


def sanitize_skill_dir_name(name: str) -> str:
    """Return a portable directory name derived from a skill name."""
    return re.sub(r"[^\w一-鿿-]", "_", name)


def _validate_member_name(name: str) -> str:
    """Validate and normalize a POSIX-relative archive member name."""
    if not name or "\x00" in name or "\\" in name:
        raise ValueError(f"Unsafe skill archive member: {name!r}.")
    path = PurePosixPath(name)
    windows_path = PureWindowsPath(name)
    if (
        path.is_absolute()
        or any(part in ("", ".", "..") for part in path.parts)
        or windows_path.drive
    ):
        raise ValueError(f"Unsafe skill archive member: {name!r}.")
    normalized = path.as_posix()
    if normalized != name:
        raise ValueError(f"Unsafe skill archive member: {name!r}.")
    return normalized


def validate_skill_archive(archive: bytes) -> SkillArchiveMetadata:
    """Validate a flat skill tar archive and parse its root ``SKILL.md``.

    The archive must contain regular files only, use safe relative POSIX
    paths, and place exactly one ``SKILL.md`` at its root. Symbolic links,
    hard links, device entries, and compressed tar payloads are rejected.
    """
    if not archive:
        raise ValueError("Skill archive is empty.")
    if len(archive) > MAX_SKILL_ARCHIVE_SIZE:
        raise ValueError("Skill archive exceeds the maximum size.")

    try:
        tar = tarfile.open(fileobj=io.BytesIO(archive), mode="r:")
    except tarfile.TarError as error:
        raise ValueError("Skill upload is not a valid tar archive.") from error

    with tar:
        try:
            members = tar.getmembers()
        except tarfile.TarError as error:
            raise ValueError(
                "Skill upload is not a valid tar archive.",
            ) from error
        if not members:
            raise ValueError("Skill archive contains no files.")
        if len(members) > MAX_SKILL_FILES:
            raise ValueError("Skill archive contains too many files.")

        seen: set[str] = set()
        total_size = 0
        skill_md: tarfile.TarInfo | None = None
        for member in members:
            if not member.isfile():
                raise ValueError(
                    "Skill archive may contain regular files only.",
                )
            name = _validate_member_name(member.name)
            portable_name = name.casefold()
            if portable_name in seen:
                raise ValueError(f"Duplicate skill archive member: {name!r}.")
            seen.add(portable_name)
            total_size += member.size
            if total_size > MAX_SKILL_ARCHIVE_SIZE:
                raise ValueError("Skill archive exceeds the maximum size.")
            if name == "SKILL.md":
                skill_md = member

        if skill_md is None:
            raise ValueError(
                "Skill archive must contain SKILL.md at its root.",
            )
        extracted = tar.extractfile(skill_md)
        if extracted is None:
            raise ValueError("Unable to read SKILL.md from skill archive.")
        try:
            skill_md_content = extracted.read().decode("utf-8")
            document = frontmatter.loads(skill_md_content)
        except (
            UnicodeDecodeError,
            TypeError,
            ValueError,
            YAMLError,
            tarfile.TarError,
        ) as error:
            raise ValueError(
                "SKILL.md is not valid UTF-8 frontmatter.",
            ) from error

    raw_name = document.get("name")
    raw_description = document.get("description")
    if not isinstance(raw_name, str) or not isinstance(raw_description, str):
        raise ValueError(
            "SKILL.md requires string name and description fields.",
        )
    name = raw_name.strip()
    description = raw_description.strip()
    if not name or not description:
        raise ValueError("SKILL.md name and description cannot be empty.")
    return SkillArchiveMetadata(
        name=name,
        description=description,
        content_hash=hashlib.sha256(
            skill_md_content.encode("utf-8"),
        ).hexdigest(),
    )


def build_skill_archive(skill_path: str) -> bytes:
    """Package a trusted local skill directory for workspace seeding."""
    root = Path(skill_path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Skill path {skill_path!r} is not a directory.")

    files: list[tuple[Path, str]] = []
    total_size = 0
    for current, dir_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for dir_name in dir_names:
            if (current_path / dir_name).is_symlink():
                raise ValueError("Skill directories may not contain symlinks.")
        dir_names.sort()
        for file_name in sorted(file_names):
            source = current_path / file_name
            if source.is_symlink() or not source.is_file():
                raise ValueError(
                    "Skill directories may contain regular files only.",
                )
            relative = source.relative_to(root).as_posix()
            _validate_member_name(relative)
            total_size += source.stat().st_size
            if total_size > MAX_SKILL_ARCHIVE_SIZE:
                raise ValueError("Skill archive exceeds the maximum size.")
            files.append((source, relative))
            if len(files) > MAX_SKILL_FILES:
                raise ValueError("Skill archive contains too many files.")

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for source, relative in files:
            stat = source.stat()
            info = tarfile.TarInfo(relative)
            info.size = stat.st_size
            info.mode = stat.st_mode & 0o777
            info.mtime = int(stat.st_mtime)
            with source.open("rb") as file_obj:
                tar.addfile(info, file_obj)
    archive = buffer.getvalue()
    validate_skill_archive(archive)
    return archive


def extract_skill_archive(archive: bytes, destination: str) -> None:
    """Safely extract a previously validated skill archive."""
    validate_skill_archive(archive)
    os.makedirs(destination, exist_ok=False)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            for member in tar.getmembers():
                relative = PurePosixPath(_validate_member_name(member.name))
                target = os.path.join(destination, *relative.parts)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    raise ValueError(
                        "Unable to read skill archive member "
                        f"{member.name!r}.",
                    )
                with source, open(target, "xb") as target_file:
                    shutil.copyfileobj(source, target_file)
                os.chmod(target, member.mode & 0o777)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
