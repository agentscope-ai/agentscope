# -*- coding: utf-8 -*-
"""A skill hub as a workspace seed source."""
from ._base import SkillHubBase
from ....skill import SkillArchive, SkillSourceBase


class HubSkillSource(SkillSourceBase):
    """A skill a workspace can seed itself with, fetched from its hub.

    The library record keeps no copy of the archive, so the only way to
    put a skill into a new workspace is to download it again. Wrapping
    that in a source rather than doing it up front matters: a workspace
    that already has the skill — or that never boots — never pays for
    the request.
    """

    def __init__(
        self,
        hub: SkillHubBase,
        user_id: str,
        card_id: str,
        name: str,
        version: str | None = None,
    ) -> None:
        """Bind what it takes to fetch one skill.

        Args:
            hub (`SkillHubBase`):
                The hub the skill was installed from.
            user_id (`str`):
                Whose visibility the download is authorized against.
            card_id (`str`):
                The card's id on that hub.
            name (`str`):
                Directory name to install as.
            version (`str | None`, optional):
                The version recorded at install time. ``None`` takes
                whatever the hub currently calls latest.
        """
        super().__init__(name)
        self._hub = hub
        self._user_id = user_id
        self._card_id = card_id
        self._version = version

    async def open(self) -> SkillArchive:
        """Stream the archive from the hub."""
        return await self._hub.download(
            self._user_id,
            self._card_id,
            self._version,
        )
