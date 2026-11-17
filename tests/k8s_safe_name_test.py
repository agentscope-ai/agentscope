# -*- coding: utf-8 -*-
"""Tests for the K8s Pod-name sanitiser."""

from unittest import TestCase

from agentscope.workspace._k8s._constants import (
    _k8s_legacy_name,
    _k8s_safe_name,
)


class K8sSafeNameTest(TestCase):
    """Tests for :func:`_k8s_safe_name`."""

    def test_names_include_original_id_digest(self) -> None:
        """Plain, lossy, and case-variant IDs have distinct names."""
        self.assertEqual(
            {
                wid: _k8s_safe_name(wid)
                for wid in ("reports", "Reports", "a.b", "a_b", "a-b-2e7336dc")
            },
            {
                "reports": "as-ws-reports-7f26104f77a7ce47",
                "Reports": "as-ws-reports-dacca3cba3f346a4",
                "a.b": "as-ws-a-b-2e7336dc8eba87ef",
                "a_b": "as-ws-a-b-648fa9b31bc7ff7e",
                "a-b-2e7336dc": "as-ws-a-b-2e7336dc-e2ae217fec562ab0",
            },
        )

    def test_plain_id_cannot_copy_a_hashed_name(self) -> None:
        """A generated suffix is hashed again when used as a plain ID."""
        name = _k8s_safe_name("a.b")
        self.assertNotEqual(name, _k8s_safe_name(name.removeprefix("as-ws-")))

    def test_legacy_names_are_preserved_for_lookup(self) -> None:
        """Recover the exact names used before workspace ID hashing."""
        self.assertEqual(
            [_k8s_legacy_name(wid) for wid in ("Reports", "a_b", "x" * 80)],
            ["as-ws-reports", "as-ws-a-b", "as-ws-" + "x" * 57],
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
