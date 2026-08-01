# -*- coding: utf-8 -*-
"""ClawHub card-building test case, without any network."""
from unittest import TestCase

from agentscope.app.hub import ClawSkillHub


class ClawCardTest(TestCase):
    """Turning an upstream catalog record into a ``SkillCard``."""

    def setUp(self) -> None:
        """Build a hub with no token; nothing here makes a request."""
        self.hub = ClawSkillHub()

    def _card(self, **item: object) -> object:
        """Build a card from a catalog record with sane defaults."""
        # pylint: disable=protected-access
        return self.hub._to_card({"slug": "demo", **item})

    def test_timestamp_version_renders_as_a_date(self) -> None:
        """ClawHub's generator stamps ``0.<date>.<time>`` versions."""
        card = self._card(latestVersion={"version": "0.20260729.110214"})

        self.assertEqual(card.version, "2026-07-29")

    def test_semver_is_left_alone(self) -> None:
        """A hub that versions properly is never rewritten."""
        card = self._card(latestVersion={"version": "1.0.1"})

        self.assertEqual(card.version, "1.0.1")

    def test_timestamp_shaped_but_invalid_date_is_left_alone(self) -> None:
        """Eight digits that are not a date stay verbatim."""
        card = self._card(latestVersion={"version": "0.20261399.110214"})

        self.assertEqual(card.version, "0.20261399.110214")

    def test_missing_version_stays_none(self) -> None:
        """The search endpoint reports no version at all."""
        self.assertIsNone(self._card().version)

    def test_topics_become_tags(self) -> None:
        """Upstream ``tags`` is a version map, so ``topics`` is used."""
        card = self._card(
            topics=["development"],
            tags={"latest": "0.20260729.110214"},
        )

        self.assertEqual(card.tags, ["development"])

    def test_updated_at_converted_from_millis(self) -> None:
        """``updatedAt`` is reported in Unix milliseconds."""
        card = self._card(updatedAt=1785300394493)

        self.assertAlmostEqual(card.updated_at, 1785300394.493, places=3)

    def test_installs_come_from_stats(self) -> None:
        """``stats.installs`` is the count worth showing."""
        card = self._card(stats={"installs": 25, "downloads": 888})

        self.assertEqual(card.installs, 25)

    def test_installs_stay_none_without_stats(self) -> None:
        """No stats means unknown, which is not the same as zero."""
        self.assertIsNone(self._card().installs)

    def test_url_falls_back_to_the_slug_route(self) -> None:
        """The catalog endpoint carries no link, so one is built."""
        card = self._card()

        self.assertEqual(card.url, "https://clawhub.ai/skills/demo")

    def test_canonical_url_is_preferred(self) -> None:
        """The search endpoint knows the owner-scoped path."""
        card = self._card(canonicalUrl="/someone/skills/demo")

        self.assertEqual(card.url, "https://clawhub.ai/someone/skills/demo")

    def test_author_and_icon_from_the_detail_owner(self) -> None:
        """The detail endpoint puts the owner beside the skill."""
        card = self._card(
            owner={"displayName": "Len", "handle": "lentiancn", "image": "u"},
        )

        self.assertEqual(card.author, "Len")
        self.assertEqual(card.icon_url, "u")

    def test_author_falls_back_to_the_handle(self) -> None:
        """Not every owner sets a display name."""
        card = self._card(owner={"handle": "lentiancn"})

        self.assertEqual(card.author, "lentiancn")

    def test_owner_read_from_the_search_shape(self) -> None:
        """Search nests the owner under ``native``."""
        card = self._card(native={"owner": {"handle": "someone"}})

        self.assertEqual(card.author, "someone")

    def test_catalog_records_have_no_author(self) -> None:
        """The catalog endpoint omits the owner entirely."""
        card = self._card(stats={"downloads": 496})

        self.assertIsNone(card.author)
        self.assertIsNone(card.icon_url)

    def test_downloads_from_either_shape(self) -> None:
        """Nested under ``stats`` on the catalog, top level on search."""
        self.assertEqual(self._card(stats={"downloads": 496}).downloads, 496)
        self.assertEqual(self._card(downloads=17058).downloads, 17058)

    def test_card_id_is_owner_scoped_when_the_owner_is_known(self) -> None:
        """A slug is not unique, so the owner pins the card down."""
        self.assertEqual(self._card(ownerHandle="runware").id, "runware/demo")
        self.assertEqual(
            self._card(owner={"handle": "runware"}).id,
            "runware/demo",
        )

    def test_card_id_falls_back_to_the_bare_slug(self) -> None:
        """The catalog endpoint names no owner, so there is nothing to add."""
        self.assertEqual(self._card().id, "demo")

    def test_card_name_never_carries_the_owner(self) -> None:
        """The name becomes a workspace directory, so it stays a slug."""
        self.assertEqual(self._card(ownerHandle="runware").name, "demo")


class ClawCardIdTest(TestCase):
    """Splitting a card id back into the parameters ClawHub takes."""

    def test_owner_scoped_id_splits(self) -> None:
        """``owner/slug`` becomes a slug plus an ``ownerHandle`` param."""
        # pylint: disable=protected-access
        self.assertEqual(
            ClawSkillHub._split_card_id("runware/music"),
            ("music", {"ownerHandle": "runware"}),
        )

    def test_bare_slug_splits_to_no_params(self) -> None:
        """A card from the catalog carries no owner to pass on."""
        # pylint: disable=protected-access
        self.assertEqual(
            ClawSkillHub._split_card_id("gifgrep"),
            ("gifgrep", {}),
        )


class ClawErrorTest(TestCase):
    """Rendering upstream error bodies."""

    _AMBIGUOUS = (
        '{"code":"AMBIGUOUS_SKILL_SLUG","slug":"music","matches":'
        '[{"ref":"@a/music"},{"ref":"@b/music"}]}'
    )

    def test_ambiguous_slug_names_the_candidates(self) -> None:
        """The raw JSON is unreadable, so the refs are spelled out."""
        # pylint: disable=protected-access
        message = ClawSkillHub._describe_error(409, self._AMBIGUOUS)

        self.assertIn("'music'", message)
        self.assertIn("@a/music, @b/music", message)

    def test_other_bodies_pass_through(self) -> None:
        """Only the one structured error is rewritten."""
        # pylint: disable=protected-access
        self.assertEqual(ClawSkillHub._describe_error(500, "boom"), "boom")
        self.assertEqual(ClawSkillHub._describe_error(409, "plain"), "plain")
        self.assertEqual(ClawSkillHub._describe_error(409, "{}"), "{}")
