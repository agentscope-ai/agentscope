# -*- coding: utf-8 -*-
"""The skill hub base class."""
from abc import ABC, abstractmethod
from typing import AsyncIterator

from ._card import SkillCard, SkillHubPage
from .._base import HubBase


class SkillHubBase(HubBase, ABC):
    """The base class for skill hub implementations.

    A hub exposes a browsable catalog of :class:`SkillCard` records plus
    :meth:`download`, which streams the skill archive the installer
    extracts into a workspace.
    """

    @abstractmethod
    async def list_skills(
        self,
        user_id: str,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> SkillHubPage:
        """Browse the hub catalog.

        Implementations must not fetch per-card files here — listing a
        page costs one upstream request, and ``SKILL.md`` is left to
        :meth:`get_skill`.

        Pagination is cursor-based rather than page-numbered: registries
        commonly expose only a forward cursor and no total count, and an
        offset-based hub can always encode its offset into the cursor.

        Args:
            user_id (`str`):
                The user identifier to query skill cards for.
            q (`str | None`, optional):
                A keyword filtering the cards. When ``None`` the whole
                catalog is browsed.
            cursor (`str | None`, optional):
                The opaque cursor from a previous page. When ``None`` the
                first page is returned.
            limit (`int`, defaults to `20`):
                The maximum number of cards per page.

        Returns:
            `SkillHubPage`:
                The requested page of cards plus the next cursor.
        """

    @abstractmethod
    async def get_skill(
        self,
        user_id: str,
        card_id: str,
    ) -> SkillCard:
        """Fetch a single card by its hub-local id, with ``SKILL.md``.

        Args:
            user_id (`str`):
                The user identifier to query the card for.
            card_id (`str`):
                The :attr:`SkillCard.id` addressing the card on this hub.

        Returns:
            `SkillCard`:
                The matching card with :attr:`SkillCard.markdown` set.

        Raises:
            `KeyError`:
                When this hub has no card under ``card_id``.
        """

    @abstractmethod
    async def download(
        self,
        card_id: str,
        version: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Stream the skill archive as a ZIP.

        Args:
            card_id (`str`):
                The :attr:`SkillCard.id` addressing the card on this hub.
            version (`str | None`, optional):
                A specific version to download. When ``None`` the latest
                version is used.

        Yields:
            `bytes`:
                The next chunk of the skill ZIP archive.

        Raises:
            `KeyError`:
                When this hub has no card under ``card_id``.
        """
        # Never reached — the yield is what makes this an async
        # generator, so overrides type-check against the same signature.
        yield b""
