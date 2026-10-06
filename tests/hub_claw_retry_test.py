# -*- coding: utf-8 -*-
"""ClawHub retry-delay test case, without any network."""
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest import TestCase

from agentscope.app.hub import ClawSkillHub


class ClawRetryDelayTest(TestCase):
    """Turning ClawHub's rate-limit headers into a sleep duration."""

    def test_retry_after_seconds_are_the_delay(self) -> None:
        """The documented delay form passes straight through."""
        # pylint: disable=protected-access
        self.assertEqual(
            ClawSkillHub._retry_delay({"Retry-After": "3600"}),
            3600.0,
        )

    def test_retry_after_matches_a_lowercase_header(self) -> None:
        """Lower-cased headers are read as well as canonical ones."""
        # pylint: disable=protected-access
        self.assertEqual(
            ClawSkillHub._retry_delay({"retry-after": "12"}),
            12.0,
        )

    def test_retry_after_http_date_is_seconds_remaining(self) -> None:
        """A date form names the instant to retry at, not a duration."""
        # pylint: disable=protected-access
        when = datetime.now(timezone.utc) + timedelta(seconds=1800)
        delay = ClawSkillHub._retry_delay(
            {"Retry-After": format_datetime(when, usegmt=True)},
        )

        self.assertAlmostEqual(delay, 1800.0, delta=5.0)

    def test_retry_after_http_date_without_an_offset_is_gmt(self) -> None:
        """A zone-less date must not slide by the machine's UTC offset."""
        # pylint: disable=protected-access
        when = datetime.now(timezone.utc) + timedelta(seconds=180)
        delay = ClawSkillHub._retry_delay(
            {"Retry-After": format_datetime(when.replace(tzinfo=None))},
        )

        self.assertAlmostEqual(delay, 180.0, delta=5.0)

    def test_retry_after_http_date_already_past_waits_nothing(self) -> None:
        """A deadline behind us is a retry now, not a negative sleep."""
        # pylint: disable=protected-access
        when = datetime.now(timezone.utc) - timedelta(seconds=60)
        delay = ClawSkillHub._retry_delay(
            {"Retry-After": format_datetime(when, usegmt=True)},
        )

        self.assertEqual(delay, 0.0)

    def test_unreadable_retry_after_falls_back_to_the_next_header(
        self,
    ) -> None:
        """A value that is neither seconds nor a date is not a dead end."""
        # pylint: disable=protected-access
        self.assertEqual(
            ClawSkillHub._retry_delay(
                {"Retry-After": "soon", "RateLimit-Reset": "45"},
            ),
            45.0,
        )

    def test_rate_limit_reset_is_a_delay(self) -> None:
        """``RateLimit-Reset`` counts down from now, it is not an instant."""
        # pylint: disable=protected-access
        self.assertEqual(
            ClawSkillHub._retry_delay({"RateLimit-Reset": "45"}),
            45.0,
        )

    def test_x_rate_limit_reset_is_an_epoch(self) -> None:
        """The legacy ``X-RateLimit-Reset`` is an absolute Unix time."""
        # pylint: disable=protected-access
        delay = ClawSkillHub._retry_delay(
            {"X-RateLimit-Reset": str(time.time() + 60)},
        )

        self.assertAlmostEqual(delay, 60.0, delta=5.0)

    def test_no_rate_limit_header_keeps_the_floor(self) -> None:
        """Without a hint, a short pause still avoids a busy loop."""
        # pylint: disable=protected-access
        self.assertEqual(ClawSkillHub._retry_delay({}), 1.0)
