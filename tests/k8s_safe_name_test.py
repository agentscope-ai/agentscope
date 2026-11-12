# -*- coding: utf-8 -*-
"""Tests for the K8s Pod-name sanitiser."""
from unittest import TestCase

from agentscope.workspace._k8s._constants import _k8s_safe_name


class K8sSafeNameTest(TestCase):
    """Tests for :func:`_k8s_safe_name`."""

    def test_short_ascii_name_is_unchanged(self) -> None:
        """A short lowercase id keeps its readable name (back-compat)."""
        self.assertEqual(_k8s_safe_name("reports"), "as-ws-reports")

    def test_case_normalisation_is_stable(self) -> None:
        """Upper/lower spellings of one id map to the same Pod."""
        self.assertEqual(
            _k8s_safe_name("Reports"),
            _k8s_safe_name("reports"),
        )

    def test_name_is_rfc1123_compliant(self) -> None:
        """Result stays RFC-1123 compliant for lossy ids."""
        for wid in (
            "a" * 200,
            "\u4e2d\u6587",
            "A.B_C",
            "  ",
            "---",
            "workspace-" + "x" * 60 + "-aaa",
        ):
            name = _k8s_safe_name(wid)
            self.assertLessEqual(len(name), 63)
            self.assertRegex(
                name,
                r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$",
            )
            self.assertFalse(name.endswith("-"))

    def test_long_ids_sharing_a_prefix_do_not_collide(self) -> None:
        """Distinct long ids sharing a prefix need distinct Pod names."""
        left = "workspace-" + "x" * 60 + "-aaa"
        right = "workspace-" + "x" * 60 + "-bbb"
        self.assertNotEqual(_k8s_safe_name(left), _k8s_safe_name(right))

    def test_ids_differing_in_replaced_chars_do_not_collide(self) -> None:
        """Ids differing only in characters replaced by '-' stay distinct."""
        self.assertNotEqual(
            _k8s_safe_name("\u4e2d\u6587"),
            _k8s_safe_name("\u00c4\u00d6\u00dc"),
        )
        self.assertNotEqual(_k8s_safe_name("a.b"), _k8s_safe_name("a_b"))

    def test_name_is_deterministic(self) -> None:
        """The same id always yields the same Pod name."""
        self.assertEqual(
            _k8s_safe_name("a" * 100),
            _k8s_safe_name("a" * 100),
        )
