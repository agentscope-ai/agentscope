# -*- coding: utf-8 -*-
"""Tests for elapsed-time formatting in the terminal UI."""
from datetime import datetime, timezone, tzinfo
from typing import cast
from unittest import TestCase
from unittest.mock import patch

from agentscope.tui._messages import _elapsed


_NO_TZ = object()


class _ClockDatetime:
    """Stand in for datetime with a fixed current instant."""

    @staticmethod
    def fromisoformat(value: str) -> datetime:
        """Parse with the standard ISO parser."""
        return datetime.fromisoformat(value)

    @staticmethod
    def now(tz: object = _NO_TZ) -> datetime:
        """Return a fixed now value for the requested clock convention."""
        if tz is _NO_TZ:
            # A no-timezone call mixes clocks: pretend it is noon.
            return datetime(2026, 1, 1, 12, 0, 2)
        if tz is None:
            return datetime(2026, 1, 1, 0, 0, 2)
        return datetime(
            2026,
            1,
            1,
            0,
            0,
            2,
            tzinfo=timezone.utc,
        ).astimezone(cast(tzinfo, tz))


class ElapsedTest(TestCase):
    """Elapsed labels must not mix incompatible clock conventions."""

    def test_finished_timestamps_with_different_offsets(self) -> None:
        """Compare explicit offsets as absolute instants."""
        self.assertEqual(
            _elapsed(
                "2026-01-01T00:00:00+02:00",
                "2026-01-01T00:00:01+00:00",
            ),
            "120m 01s",
        )

    def test_mixed_naive_and_aware_timestamps_are_not_offset(self) -> None:
        """A mixed pair uses wall-clock fields instead of a local offset."""
        self.assertEqual(
            _elapsed(
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:01+00:00",
            ),
            "1.0s",
        )
        self.assertEqual(
            _elapsed(
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:01",
            ),
            "1.0s",
        )

    def test_running_time_uses_the_created_at_clock(self) -> None:
        """A running block measures against a clock of the same kind."""
        cases = (
            "2026-01-01T00:00:00",
            "2026-01-01T00:00:00+00:00",
        )
        for created_at in cases:
            with self.subTest(created_at=created_at):
                with patch(
                    "agentscope.tui._messages.datetime",
                    _ClockDatetime,
                ):
                    elapsed = _elapsed(created_at, None)
                self.assertEqual(elapsed, "2.0s")
