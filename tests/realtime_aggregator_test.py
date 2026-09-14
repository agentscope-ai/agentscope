# -*- coding: utf-8 -*-
"""Unit tests for TurnAggregator."""
import time
import unittest

from agentscope.agent import TurnAggregator


class TurnAggregatorTest(unittest.TestCase):
    """Collapsing transcripts into turns."""

    def test_continuation_within_window_merges(self) -> None:
        """A transcript arriving inside the merge window continues the
        previous turn; one arriving later starts a new one."""
        aggregator = TurnAggregator(merge_window_ms=300)

        self.assertListEqual(
            [
                aggregator.take("帮我订张机票"),
                aggregator.merges_with_previous(),
                aggregator.take("去上海的"),
                aggregator.merges_with_previous(),
            ],
            ["帮我订张机票", False, "去上海的", True],
        )

        time.sleep(0.4)
        self.assertListEqual(
            [aggregator.take("几点的"), aggregator.merges_with_previous()],
            ["几点的", False],
        )

    def test_backchannels_and_empty_transcripts_are_not_turns(self) -> None:
        """Acknowledgements are matched ignoring trailing punctuation."""
        aggregator = TurnAggregator(backchannels=frozenset({"嗯", "对"}))

        self.assertListEqual(
            [
                aggregator.take("嗯。"),
                aggregator.take("对，"),
                aggregator.take("   "),
                aggregator.take("对的"),
            ],
            [None, None, None, "对的"],
        )

    def test_reset_forgets_previous_turn(self) -> None:
        """After a reset the next transcript never merges."""
        aggregator = TurnAggregator(merge_window_ms=10_000)
        aggregator.take("第一句")
        aggregator.reset()

        self.assertListEqual(
            [aggregator.take("第二句"), aggregator.merges_with_previous()],
            ["第二句", False],
        )
