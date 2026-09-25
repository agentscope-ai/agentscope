# -*- coding: utf-8 -*-
"""Unit tests for the realtime context-usage tracker.

Pure state-machine tests: no model, no transport, no event harness.
The tracker must answer one question for the compression layer — how
many tokens does the server-side context occupy, and how reliable is
that number — without ever accumulating per-response input tokens
(provider semantics differ; see issue #2566).
"""
# pylint: disable=missing-function-docstring

from agentscope.agent._realtime._context_usage import (
    ContextUsageTracker,
    UsageProvenance,
    estimate_context_tokens,
)
from agentscope.message import AssistantMsg, UserMsg


def test_provider_report_sets_estimate_and_provenance() -> None:
    tracker = ContextUsageTracker()
    tracker.observe_provider_report(500, 20)

    assert tracker.estimate_tokens == 500
    assert tracker.provenance == UsageProvenance.PROVIDER
    assert not tracker.is_stale


def test_latest_report_replaces_not_accumulates() -> None:
    """input_tokens can cover the full context processed for that
    response; summing 500 + 600 would double-count the history."""
    tracker = ContextUsageTracker()
    tracker.observe_provider_report(500, 20)
    tracker.observe_provider_report(600, 25)

    assert tracker.estimate_tokens == 600


def test_output_tokens_never_grow_the_estimate() -> None:
    """Billed output (e.g. Qwen-Audio speech) is not necessarily
    retained in context, so output never grows the estimate."""
    tracker = ContextUsageTracker()
    tracker.observe_provider_report(1000, 300)

    assert tracker.estimate_tokens == 1000


def test_missing_report_marks_stale_and_keeps_previous_estimate() -> None:
    """A provider omitting usage must not read as "reported zero"."""
    tracker = ContextUsageTracker()
    tracker.observe_provider_report(500, 20)

    tracker.observe_provider_report(None, None)

    assert tracker.is_stale
    assert tracker.estimate_tokens == 500
    assert tracker.provenance == UsageProvenance.PROVIDER


def test_first_ever_report_being_missing_leaves_no_estimate() -> None:
    tracker = ContextUsageTracker()

    tracker.observe_provider_report(None, None)

    assert tracker.estimate_tokens is None
    assert tracker.is_stale


def test_output_only_report_does_not_fabricate_zero_estimate() -> None:
    tracker = ContextUsageTracker()
    tracker.observe_provider_report(500, 20)

    tracker.observe_provider_report(None, 7)

    assert tracker.estimate_tokens == 500
    assert tracker.is_stale
    assert tracker.provenance == UsageProvenance.PROVIDER


def test_first_output_only_report_leaves_no_estimate() -> None:
    tracker = ContextUsageTracker()

    tracker.observe_provider_report(None, 7)

    assert tracker.estimate_tokens is None
    assert tracker.is_stale


def test_reported_zero_input_is_a_fresh_estimate() -> None:
    tracker = ContextUsageTracker()

    tracker.observe_provider_report(0, 7)

    assert tracker.estimate_tokens == 0
    assert not tracker.is_stale


def test_local_append_marks_stale() -> None:
    tracker = ContextUsageTracker()
    tracker.observe_provider_report(500, 20)

    tracker.note_local_append()

    assert tracker.is_stale
    assert tracker.estimate_tokens == 500
    assert tracker.local_appends_since_observation == 1


def test_new_report_clears_staleness() -> None:
    tracker = ContextUsageTracker()
    tracker.note_local_append()
    tracker.observe_provider_report(600, 25)

    assert not tracker.is_stale


def test_baseline_untrusted_after_injected_history() -> None:
    tracker = ContextUsageTracker()
    tracker.observe_provider_report(500, 20)

    tracker.mark_baseline_untrusted(injected_messages=7)

    assert tracker.is_stale
    assert "7" in (tracker.observation.stale_reason or "")
    # the stale marker must carry a reason mentioning the re-injection
    assert tracker.estimate_tokens == 500


def test_mark_stale_before_any_observation() -> None:
    tracker = ContextUsageTracker()
    tracker.mark_stale("interrupted")

    assert tracker.is_stale
    assert tracker.estimate_tokens is None


def test_local_transcript_estimate_sets_estimated_provenance() -> None:
    tracker = ContextUsageTracker()

    tracker.observe_estimate(125)

    assert tracker.estimate_tokens == 125
    assert tracker.provenance == UsageProvenance.ESTIMATED
    assert not tracker.is_stale


def test_provider_report_replaces_local_estimate() -> None:
    tracker = ContextUsageTracker()
    tracker.observe_estimate(125)

    tracker.observe_provider_report(100, 20)

    assert tracker.estimate_tokens == 100
    assert tracker.provenance == UsageProvenance.PROVIDER
    assert not tracker.is_stale


def test_context_estimate_includes_user_and_assistant_content() -> None:
    short = [UserMsg(name="user", content="a" * 40)]
    extended = [
        *short,
        AssistantMsg(name="assistant", content="b" * 80),
    ]

    assert estimate_context_tokens(short) > 0
    assert estimate_context_tokens(extended) > estimate_context_tokens(short)


def test_local_estimate_does_not_lower_provider_baseline() -> None:
    tracker = ContextUsageTracker()
    tracker.observe_provider_report(500, 20)

    tracker.observe_estimate(100)

    assert tracker.estimate_tokens == 500
    assert tracker.provenance == UsageProvenance.ESTIMATED


def test_empty_context_estimates_zero_tokens() -> None:
    assert estimate_context_tokens([]) == 0
