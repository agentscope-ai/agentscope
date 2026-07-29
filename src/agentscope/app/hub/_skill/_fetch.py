# -*- coding: utf-8 -*-
"""Materialize a hub skill into a local directory."""
import asyncio
import io
import os
import tempfile
import zipfile
from contextlib import asynccontextmanager
from typing import AsyncIterator

from ._base import SkillHubBase


class SkillFetchError(ValueError):
    """Raised when a downloaded skill archive is unusable."""


def _extract(payload: bytes, target: str) -> str:
    """Unpack a skill ZIP and return the directory holding ``SKILL.md``.

    Archives come flat (``SKILL.md`` at the root) or wrapped in a single
    top-level folder; both are accepted. ``zipfile`` strips absolute and
    ``..`` components while extracting, so a malicious archive cannot
    escape ``target``.

    Args:
        payload (`bytes`):
            The ZIP archive bytes.
        target (`str`):
            The directory to unpack into.

    Returns:
        `str`:
            The path of the directory containing ``SKILL.md``.

    Raises:
        `SkillFetchError`:
            When the payload is not a ZIP or holds no ``SKILL.md``.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            archive.extractall(target)
    except zipfile.BadZipFile as e:
        raise SkillFetchError(f"Not a valid ZIP archive: {e}") from e

    if os.path.isfile(os.path.join(target, "SKILL.md")):
        return target

    entries = [
        os.path.join(target, name)
        for name in os.listdir(target)
        if os.path.isdir(os.path.join(target, name))
    ]
    for entry in entries:
        if os.path.isfile(os.path.join(entry, "SKILL.md")):
            return entry

    raise SkillFetchError("The skill archive contains no SKILL.md")


@asynccontextmanager
async def fetch_skill_dir(
    hub: SkillHubBase,
    card_id: str,
    version: str | None = None,
    dir_name: str | None = None,
) -> AsyncIterator[str]:
    """Download a skill and yield a local directory ready to install.

    The directory is named after ``dir_name`` because the sandboxed
    workspaces derive the installed folder from the basename of the path
    they are handed — a raw temp-dir name would leak through. The local
    workspace ignores it and uses the ``SKILL.md`` frontmatter instead.
    Everything is removed on exit.

    Args:
        hub (`SkillHubBase`):
            The hub to download from.
        card_id (`str`):
            The :attr:`SkillCard.id` addressing the card on that hub.
        version (`str | None`, optional):
            A specific version to download, latest when ``None``.
        dir_name (`str | None`, optional):
            The name to give the skill directory. Defaults to ``card_id``.

    Yields:
        `str`:
            The path of the extracted skill directory.

    Raises:
        `SkillFetchError`:
            When ``dir_name`` is not a plain directory name, or the
            archive is unusable.
    """
    name = dir_name or card_id
    if name in (".", "..") or os.path.basename(name) != name:
        raise SkillFetchError(
            f"Skill directory name {name!r} must be a plain name without "
            f"path separators.",
        )

    chunks = [chunk async for chunk in hub.download(card_id, version)]

    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, name)
        os.makedirs(target)
        skill_dir = await asyncio.to_thread(
            _extract,
            b"".join(chunks),
            target,
        )
        yield skill_dir
