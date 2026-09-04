# -*- coding: utf-8 -*-
"""Skill archives and the sources they are fetched from."""
from abc import ABC, abstractmethod
from typing import AsyncIterator, Literal, NamedTuple


class SkillArchive(NamedTuple):
    """A skill archive, already opened for reading.

    The format travels with the bytes so a source can forward its
    upstream response untouched — repacking to a fixed format would
    force every source to buffer the whole archive.

    Attributes:
        format: The archive format, as the installer must unpack it.
        stream: The archive bytes, in order.
    """

    format: Literal["zip", "tar", "tar.gz"]
    stream: AsyncIterator[bytes]


class SkillSourceBase(ABC):
    """Where a skill archive comes from, before it is fetched.

    :class:`SkillLoaderBase` enumerates skills that already exist as
    directories, and hands out the path an agent reads them from. This
    one has no directory yet — only a way to get the bytes, which the
    workspace expands into the agent's partition.

    :meth:`open` returns a fresh archive per call rather than the
    source holding one: a stream is consumed once, while a source is
    kept for a process's lifetime and installed into many workspaces.
    """

    def __init__(self, name: str) -> None:
        """Bind the name the skill installs as.

        Args:
            name (`str`):
                Directory name to install as, inside ``skills/``.
        """
        self.name = name

    @abstractmethod
    async def open(self) -> SkillArchive:
        """Fetch the archive.

        Returns:
            `SkillArchive`:
                The format plus the archive bytes.
        """
