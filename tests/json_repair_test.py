# -*- coding: utf-8 -*-
"""Reproduce test for issue #1272: json_repair stream_stable parameter bug."""

import unittest

from agentscope._utils._common import _json_loads_with_repair


class JsonRepairBugTest(unittest.TestCase):
    """Test that _json_loads_with_repair handles valid JSON correctly."""

    def test_valid_json_should_parse(self):
        """Valid JSON should be parsed correctly, not return empty dict.

        Bug: repair_json() is called with stream_stable=True parameter
        which doesn't exist, causing TypeError and returning empty dict.
        """
        # Test case from issue #1272
        json_str = '{"command": "ls", "timeout": 300}'
        result = _json_loads_with_repair(json_str)

        # Expected: {"command": "ls", "timeout": 300}
        # Actual (with bug): {}
        self.assertEqual(result, {"command": "ls", "timeout": 300},
                        "Valid JSON should be parsed correctly, not return empty dict")

    def test_tool_parameters_parsing(self):
        """Tool parameters should be parsed correctly."""
        # Another example with different tool parameters
        json_str = '{"file_path": "/tmp/test.txt", "content": "hello"}'
        result = _json_loads_with_repair(json_str)

        self.assertEqual(result, {"file_path": "/tmp/test.txt", "content": "hello"},
                        "Tool parameters should be parsed correctly")


if __name__ == "__main__":
    unittest.main()
