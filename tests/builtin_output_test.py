# -*- coding: utf-8 -*-
"""Output truncation helper test cases."""

import unittest

from agentscope.tool._builtin._output import (
    OUTPUT_TRUNCATION_LIMIT,
    truncate_output,
)


class TruncateOutputTest(unittest.TestCase):
    """Test the shared truncation notice for shell tools."""

    def test_output_within_limit_is_unchanged(self) -> None:
        """Leave short output untouched."""
        output = "x" * OUTPUT_TRUNCATION_LIMIT
        self.assertEqual(truncate_output(output), output)

    def test_truncation_states_original_and_omitted_lengths(self) -> None:
        """State how much of the output was kept and omitted."""
        output = "x" * (OUTPUT_TRUNCATION_LIMIT + 100)
        truncated = truncate_output(output)
        self.assertTrue(
            truncated.startswith("x" * OUTPUT_TRUNCATION_LIMIT),
        )
        self.assertIn(
            f"showing the first {OUTPUT_TRUNCATION_LIMIT} of "
            f"{OUTPUT_TRUNCATION_LIMIT + 100} characters",
            truncated,
        )
        self.assertIn("100 characters omitted", truncated)
        self.assertIn("re-run the command", truncated)

    def test_custom_limit_is_respected(self) -> None:
        """Honor a caller-provided limit."""
        self.assertEqual(
            truncate_output("abcdef", limit=3),
            "abc\n... (output truncated: showing the first 3 of "
            "6 characters; 3 characters omitted. To inspect the "
            "rest, re-run the command with its output redirected to "
            "a file, or narrow the output.)",
        )


if __name__ == "__main__":
    unittest.main()
