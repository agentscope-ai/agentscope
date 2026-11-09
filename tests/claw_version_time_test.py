# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Regression tests for ClawHub timestamp-version humanizing."""
from unittest import TestCase

from agentscope.app.hub._skill._claw_hub import _humanize_version


class HumanizeVersionTest(TestCase):
    """Only versions with a real date AND time become a date."""

    def test_invalid_hour_is_rejected(self) -> None:
        """An hour of 99 is not a real time, so keep the version."""
        version = "0.20260729.999999"
        self.assertEqual(_humanize_version(version), version)

    def test_hour_24_is_rejected(self) -> None:
        """Hour 24 is out of range and must not be humanized."""
        version = "0.20260729.240000"
        self.assertEqual(_humanize_version(version), version)

    def test_invalid_minute_is_rejected(self) -> None:
        """Minute 60 is out of range and must not be humanized."""
        version = "0.20260729.126000"
        self.assertEqual(_humanize_version(version), version)

    def test_valid_timestamp_is_humanized(self) -> None:
        """A genuine timestamp version still renders as its date."""
        self.assertEqual(
            _humanize_version("0.20260729.110214"),
            "2026-07-29",
        )

    def test_invalid_date_is_untouched(self) -> None:
        """Feb 30 was already rejected and must stay rejected."""
        version = "0.20260230.110214"
        self.assertEqual(_humanize_version(version), version)

    def test_semver_is_untouched(self) -> None:
        """Semver and other shapes are returned verbatim."""
        self.assertEqual(_humanize_version("1.2.3"), "1.2.3")

    def test_none_is_untouched(self) -> None:
        """A missing version stays missing."""
        self.assertIsNone(_humanize_version(None))

    def test_short_time_is_untouched(self) -> None:
        """A version without a 6-digit time is not a timestamp."""
        version = "0.20260729.1"
        self.assertEqual(_humanize_version(version), version)
