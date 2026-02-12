# -*- coding: utf-8 -*-
"""Test AgentSkillManager with focus on core functionality."""
# pylint: disable=protected-access
import os
import tempfile
import shutil
import time
import threading
from unittest import TestCase

from agentscope.tool._skill_manager import AgentSkillManager


class SkillManagerTestBase(TestCase):
    """Base test class with shared setup and utilities."""

    def setUp(self) -> None:
        """Set up test environment before each test."""
        self.test_dir = tempfile.mkdtemp()
        self.manager = AgentSkillManager(
            agent_skill_instruction="Test instruction: {skills}",
            agent_skill_template="Skill: {name} - {description} ({dir})",
        )

    def tearDown(self) -> None:
        """Clean up test environment after each test."""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _create_skill_dir(self, name: str, description: str) -> str:
        """Helper to create a valid skill directory with SKILL.md."""
        skill_dir = os.path.join(self.test_dir, name)
        os.makedirs(skill_dir, exist_ok=True)

        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        with open(skill_md_path, "w", encoding="utf-8") as f:
            f.write("---\n")
            f.write(f"name: {name}\n")
            f.write(f"description: {description}\n")
            f.write("---\n")
            f.write("\n# Skill Content\n")

        return skill_dir

    def _create_skill_dir_in(
        self,
        parent_dir: str,
        name: str,
        description: str,
    ) -> str:
        """Helper to create a skill directory in a specific parent."""
        skill_dir = os.path.join(parent_dir, name)
        os.makedirs(skill_dir, exist_ok=True)

        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        with open(skill_md_path, "w", encoding="utf-8") as f:
            f.write("---\n")
            f.write(f"name: {name}\n")
            f.write(f"description: {description}\n")
            f.write("---\n")
            f.write("\n# Skill Content\n")

        return skill_dir


class SkillManagerBasicTest(SkillManagerTestBase):
    """Test basic functionality: monitoring, registration, and validation."""

    def test_monitor_directory_with_path_normalization(self) -> None:
        """Test monitoring directories with path normalization."""
        # Test with relative path normalization
        subdir = os.path.join(self.test_dir, "subdir")
        os.makedirs(subdir)
        relative_path = os.path.join(subdir, "..")

        self.manager.monitor_agent_skills(relative_path)
        abs_path = os.path.abspath(os.path.normpath(relative_path))
        self.assertIn(abs_path, self.manager.monitored_dirs)

        # Test monitoring multiple directories
        dir1 = os.path.join(self.test_dir, "dir1")
        dir2 = os.path.join(self.test_dir, "dir2")
        os.makedirs(dir1)
        os.makedirs(dir2)

        self.manager.monitor_agent_skills(dir1)
        self.manager.monitor_agent_skills(dir2)

        self.assertEqual(len(self.manager.monitored_dirs), 3)
        self.assertIn(os.path.abspath(dir1), self.manager.monitored_dirs)
        self.assertIn(os.path.abspath(dir2), self.manager.monitored_dirs)

    def test_monitor_invalid_directory_raises_error(self) -> None:
        """Test monitoring invalid directory or file raises ValueError."""
        # Test nonexistent directory
        invalid_dir = os.path.join(self.test_dir, "nonexistent")
        with self.assertRaises(ValueError) as context:
            self.manager.monitor_agent_skills(invalid_dir)
        self.assertIn("Invalid directory", str(context.exception))

        # Test file instead of directory
        test_file = os.path.join(self.test_dir, "test.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("test")
        with self.assertRaises(ValueError) as context:
            self.manager.monitor_agent_skills(test_file)
        self.assertIn("Invalid directory", str(context.exception))

    def test_remove_monitored_directory_preserves_manual_skills(
        self,
    ) -> None:
        """Test removing monitored dir preserves manual skills."""
        # Add a manual skill
        manual_skill_dir = self._create_skill_dir(
            "manual_skill",
            "Manual skill",
        )
        self.manager.register_agent_skill(manual_skill_dir)

        # Add a monitored skill using proper API
        monitored_dir = os.path.join(self.test_dir, "monitored")
        os.makedirs(monitored_dir)
        self._create_skill_dir_in(
            monitored_dir,
            "monitored_skill",
            "Monitored skill",
        )

        # Monitor directory and trigger discovery
        self.manager.monitor_agent_skills(monitored_dir)
        self.manager.get_agent_skill_prompt()

        # Verify both skills exist
        self.assertIn("manual_skill", self.manager.skills)
        self.assertIn("monitored_skill", self.manager.skills)
        self.assertEqual(len(self.manager.skills), 2)

        # Remove the monitored directory
        self.manager.remove_monitored_directory(monitored_dir)

        # Verify manual skill preserved, monitored skill removed
        self.assertIn("manual_skill", self.manager.skills)
        self.assertNotIn("monitored_skill", self.manager.skills)
        self.assertNotIn(
            os.path.abspath(monitored_dir),
            self.manager.monitored_dirs,
        )
        self.assertEqual(len(self.manager.skills), 1)

    def test_remove_monitored_directory_complete_workflow(self) -> None:
        """Test complete workflow: monitor -> discover -> remove."""
        # Create monitored directory with multiple skills
        monitored_dir = os.path.join(self.test_dir, "monitored")
        os.makedirs(monitored_dir)
        self._create_skill_dir_in(monitored_dir, "skill1", "Skill 1")
        self._create_skill_dir_in(monitored_dir, "skill2", "Skill 2")

        # Create nested skill
        nested_dir = os.path.join(monitored_dir, "subdir")
        os.makedirs(nested_dir)
        self._create_skill_dir_in(nested_dir, "skill3", "Skill 3")

        # Monitor directory (should not scan immediately)
        self.manager.monitor_agent_skills(monitored_dir)
        self.assertEqual(len(self.manager.skills), 0)
        self.assertIn(
            os.path.abspath(monitored_dir),
            self.manager.monitored_dirs,
        )

        # Trigger skill discovery
        prompt = self.manager.get_agent_skill_prompt()

        # Verify all skills discovered
        self.assertEqual(len(self.manager.skills), 3)
        self.assertIn("skill1", self.manager.skills)
        self.assertIn("skill2", self.manager.skills)
        self.assertIn("skill3", self.manager.skills)
        self.assertIsNotNone(prompt)
        self.assertIn("skill1", prompt)
        self.assertIn("skill2", prompt)
        self.assertIn("skill3", prompt)

        # Verify all skills have correct metadata
        for skill_name in ["skill1", "skill2", "skill3"]:
            skill = self.manager.skills[skill_name]
            self.assertEqual(skill["source"], "monitored")
            self.assertEqual(
                skill["monitored_root"],
                os.path.abspath(monitored_dir),
            )

        # Remove the monitored directory
        self.manager.remove_monitored_directory(monitored_dir)

        # Verify all skills removed
        self.assertEqual(len(self.manager.skills), 0)
        self.assertNotIn("skill1", self.manager.skills)
        self.assertNotIn("skill2", self.manager.skills)
        self.assertNotIn("skill3", self.manager.skills)
        self.assertNotIn(
            os.path.abspath(monitored_dir),
            self.manager.monitored_dirs,
        )

        # Verify prompt is None after removal
        prompt_after = self.manager.get_agent_skill_prompt()
        self.assertIsNone(prompt_after)

    def test_remove_one_monitored_directory_preserves_others(
        self,
    ) -> None:
        """Test removing one monitored dir preserves others."""
        # Create two monitored directories
        dir1 = os.path.join(self.test_dir, "dir1")
        dir2 = os.path.join(self.test_dir, "dir2")
        os.makedirs(dir1)
        os.makedirs(dir2)

        self._create_skill_dir_in(dir1, "skill_from_dir1", "Skill from dir1")
        self._create_skill_dir_in(dir2, "skill_from_dir2", "Skill from dir2")

        # Monitor both directories
        self.manager.monitor_agent_skills(dir1)
        self.manager.monitor_agent_skills(dir2)
        self.manager.get_agent_skill_prompt()

        # Verify both skills exist
        self.assertEqual(len(self.manager.skills), 2)
        self.assertIn("skill_from_dir1", self.manager.skills)
        self.assertIn("skill_from_dir2", self.manager.skills)

        # Remove only dir1
        self.manager.remove_monitored_directory(dir1)

        # Verify only dir1 skills removed, dir2 skills preserved
        self.assertEqual(len(self.manager.skills), 1)
        self.assertNotIn("skill_from_dir1", self.manager.skills)
        self.assertIn("skill_from_dir2", self.manager.skills)
        self.assertNotIn(os.path.abspath(dir1), self.manager.monitored_dirs)
        self.assertIn(os.path.abspath(dir2), self.manager.monitored_dirs)

    def test_remove_monitored_directory_not_monitored(self) -> None:
        """Test removing a directory that is not monitored logs warning."""
        non_monitored_dir = os.path.join(self.test_dir, "not_monitored")
        os.makedirs(non_monitored_dir)

        # Should not raise error, just log warning
        self.manager.remove_monitored_directory(non_monitored_dir)

        # Verify nothing changed
        self.assertEqual(len(self.manager.monitored_dirs), 0)
        self.assertEqual(len(self.manager.skills), 0)

    def test_register_valid_skill_with_path_normalization(self) -> None:
        """Test registering a valid skill with path normalization."""
        skill_dir = self._create_skill_dir("test_skill", "A test skill")

        # Register with relative path
        relative_path = os.path.join(skill_dir, "..", "test_skill")
        self.manager.register_agent_skill(relative_path)

        # Verify skill is registered with normalized path
        self.assertIn("test_skill", self.manager.skills)
        skill = self.manager.skills["test_skill"]
        self.assertEqual(skill["name"], "test_skill")
        self.assertEqual(skill["description"], "A test skill")
        self.assertEqual(skill["dir"], os.path.abspath(skill_dir))
        self.assertEqual(skill["source"], "manual")
        self.assertIsNone(skill["monitored_root"])

    def test_register_invalid_skill_raises_error(self) -> None:
        """Test registering invalid skills raises ValueError."""
        # Nonexistent directory
        with self.assertRaises(ValueError) as context:
            self.manager.register_agent_skill(
                os.path.join(self.test_dir, "nonexistent"),
            )
        self.assertIn("does not exist", str(context.exception))

        # File instead of directory
        test_file = os.path.join(self.test_dir, "test.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("test")
        with self.assertRaises(ValueError) as context:
            self.manager.register_agent_skill(test_file)
        self.assertIn("not a directory", str(context.exception))

        # Missing SKILL.md
        skill_dir = os.path.join(self.test_dir, "no_skill_md")
        os.makedirs(skill_dir)
        with self.assertRaises(ValueError) as context:
            self.manager.register_agent_skill(skill_dir)
        self.assertIn("SKILL.md", str(context.exception))

        # Missing required fields
        skill_dir2 = os.path.join(self.test_dir, "missing_name")
        os.makedirs(skill_dir2)
        with open(
            os.path.join(skill_dir2, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("---\ndescription: A test skill\n---\n")
        with self.assertRaises(ValueError) as context:
            self.manager.register_agent_skill(skill_dir2)
        self.assertIn("name", str(context.exception).lower())

    def test_register_duplicate_skill_name_raises_error(self) -> None:
        """Test registering skill with duplicate name raises ValueError."""
        skill_dir1 = self._create_skill_dir("duplicate_skill", "First skill")
        self.manager.register_agent_skill(skill_dir1)

        # Create second skill with same name
        skill_dir2 = os.path.join(self.test_dir, "duplicate_skill_2")
        os.makedirs(skill_dir2)
        with open(
            os.path.join(skill_dir2, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(
                "---\nname: duplicate_skill\ndescription: Second skill\n---\n",
            )

        with self.assertRaises(ValueError) as context:
            self.manager.register_agent_skill(skill_dir2)
        self.assertIn("already registered", str(context.exception))

    def test_properties_are_readonly(self) -> None:
        """Test that properties return immutable views."""
        self.manager.monitor_agent_skills(self.test_dir)
        self._create_skill_dir("test_skill", "Test")
        self.manager.get_agent_skill_prompt()

        # Test monitored_dirs is frozenset
        monitored = self.manager.monitored_dirs
        self.assertIsInstance(monitored, frozenset)
        with self.assertRaises(AttributeError):
            monitored.add("test")  # type: ignore

        # Test skills is immutable
        skills = self.manager.skills
        with self.assertRaises(TypeError):
            skills["new_skill"] = {}  # type: ignore


class SkillManagerLazyLoadingTest(SkillManagerTestBase):
    """Test lazy loading and caching mechanisms."""

    def test_lazy_loading_workflow(self) -> None:
        """Test complete lazy loading workflow."""
        # Create multiple skills including nested
        self._create_skill_dir("skill1", "First skill")
        self._create_skill_dir("skill2", "Second skill")

        nested_dir = os.path.join(self.test_dir, "level1", "level2")
        os.makedirs(nested_dir, exist_ok=True)
        nested_skill_dir = os.path.join(nested_dir, "nested_skill")
        os.makedirs(nested_skill_dir)
        with open(
            os.path.join(nested_skill_dir, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(
                "---\nname: nested_skill\ndescription: A nested skill\n---\n",
            )

        # Monitor does not scan immediately
        self.manager.monitor_agent_skills(self.test_dir)
        self.assertEqual(len(self.manager.skills), 0)

        # Trigger lazy loading
        prompt = self.manager.get_agent_skill_prompt()

        # Verify all skills discovered
        self.assertEqual(len(self.manager.skills), 3)
        self.assertIn("skill1", self.manager.skills)
        self.assertIn("skill2", self.manager.skills)
        self.assertIn("nested_skill", self.manager.skills)
        self.assertIn("skill1", prompt)
        self.assertIn("nested_skill", prompt)

    def test_lazy_loading_skips_invalid_and_handles_conflicts(
        self,
    ) -> None:
        """Test lazy loading skips invalid skills and handles conflicts."""
        # Valid skill
        self._create_skill_dir("valid_skill", "A valid skill")

        # Invalid skill (missing description)
        invalid_dir = os.path.join(self.test_dir, "invalid_skill")
        os.makedirs(invalid_dir)
        with open(
            os.path.join(invalid_dir, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("---\nname: invalid_skill\n---\n")

        # Manually register a skill
        manual_skill_dir = self._create_skill_dir(
            "conflict_skill",
            "Manual skill",
        )
        self.manager.register_agent_skill(manual_skill_dir)

        # Create monitored skill with same name
        conflict_dir = os.path.join(self.test_dir, "subdir")
        os.makedirs(conflict_dir)
        conflict_skill_dir = os.path.join(conflict_dir, "conflict_skill_2")
        os.makedirs(conflict_skill_dir)
        with open(
            os.path.join(conflict_skill_dir, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(
                "---\nname: conflict_skill\n"
                "description: Monitored skill\n---\n",
            )

        self.manager.monitor_agent_skills(self.test_dir)
        prompt = self.manager.get_agent_skill_prompt()

        # Verify only valid skill discovered, invalid skipped
        self.assertEqual(len(self.manager.skills), 2)
        self.assertIn("valid_skill", self.manager.skills)
        self.assertNotIn("invalid_skill", self.manager.skills)
        self.assertEqual(
            self.manager.skills["conflict_skill"]["source"],
            "manual",
        )
        self.assertIsNotNone(prompt)

    def test_lazy_loading_returns_none_when_no_skills(self) -> None:
        """Test that get_agent_skill_prompt returns None with no skills."""
        self.manager.monitor_agent_skills(self.test_dir)
        prompt = self.manager.get_agent_skill_prompt()
        self.assertIsNone(prompt)

    def test_lazy_loading_includes_manual_and_monitored_skills(self) -> None:
        """Test that prompt includes both manual and monitored skills."""
        # Manual skill
        manual_skill_dir = self._create_skill_dir(
            "manual_skill",
            "Manual skill",
        )
        self.manager.register_agent_skill(manual_skill_dir)

        # Monitored skill
        monitored_dir = os.path.join(self.test_dir, "monitored")
        os.makedirs(monitored_dir)
        monitored_skill_dir = os.path.join(monitored_dir, "monitored_skill")
        os.makedirs(monitored_skill_dir)
        with open(
            os.path.join(monitored_skill_dir, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(
                "---\nname: monitored_skill\n"
                "description: Monitored skill\n---\n",
            )

        self.manager.monitor_agent_skills(monitored_dir)
        prompt = self.manager.get_agent_skill_prompt()

        # Verify both in prompt
        self.assertEqual(len(self.manager.skills), 2)
        self.assertIn("manual_skill", prompt)
        self.assertIn("monitored_skill", prompt)

    def test_cache_fast_and_slow_paths(self) -> None:
        """Test cache uses fast path when unchanged, slow when changed."""
        self._create_skill_dir("test_skill", "A test skill")

        self.manager.monitor_agent_skills(self.test_dir)
        self.manager.get_agent_skill_prompt()

        # Save cache state
        cache_before = dict(self.manager._skill_dir_mtime_cache)

        # Second scan without changes (fast path)
        self.manager.get_agent_skill_prompt()
        self.assertEqual(cache_before, self.manager._skill_dir_mtime_cache)

        # Add new skill (slow path)
        time.sleep(0.01)
        self._create_skill_dir("skill2", "Second skill")

        self.manager.get_agent_skill_prompt()

        # Verify new skill discovered
        self.assertEqual(len(self.manager.skills), 2)
        self.assertIn("test_skill", self.manager.skills)
        self.assertIn("skill2", self.manager.skills)

    def test_force_refresh_and_cache_cleanup(self) -> None:
        """Test force refresh clears caches and cache cleanup on removal."""
        skill_dir = self._create_skill_dir("test_skill", "A test skill")

        self.manager.monitor_agent_skills(self.test_dir)
        self.manager.get_agent_skill_prompt()

        # Verify caches populated
        self.assertGreater(len(self.manager._skill_dir_mtime_cache), 0)
        self.assertGreater(len(self.manager._monitored_dir_last_scan_mtime), 0)

        # Force refresh
        stats = self.manager.refresh_monitored_skills(force=True)
        self.assertIn("added", stats)
        self.assertIn("modified", stats)
        self.assertIn("removed", stats)

        # Verify cache entries exist
        abs_skill_dir = os.path.abspath(skill_dir)
        normalized_test_dir = os.path.abspath(self.test_dir)
        self.assertIn(abs_skill_dir, self.manager._skill_dir_mtime_cache)
        self.assertIn(
            normalized_test_dir,
            self.manager._monitored_dir_last_scan_mtime,
        )

        # Remove monitored directory and verify cache cleanup
        self.manager.remove_monitored_directory(self.test_dir)
        self.assertNotIn(
            normalized_test_dir,
            self.manager._monitored_dir_last_scan_mtime,
        )
        self.assertEqual(len(self.manager._skill_dir_mtime_cache), 0)


class SkillManagerChangeDetectionTest(SkillManagerTestBase):
    """Test change detection: additions, modifications, deletions."""

    def test_detect_new_and_modified_skills(self) -> None:
        """Test detection of new skills and modifications."""
        # Initial skill
        skill_dir = self._create_skill_dir("skill1", "Original description")

        self.manager.monitor_agent_skills(self.test_dir)
        self.manager.get_agent_skill_prompt()
        self.assertEqual(len(self.manager.skills), 1)
        self.assertEqual(
            self.manager.skills["skill1"]["description"],
            "Original description",
        )

        # Add new skills
        time.sleep(0.01)
        skill2_dir = self._create_skill_dir("skill2", "Second skill")
        self._create_skill_dir("skill3", "Third skill")

        self.manager.get_agent_skill_prompt()
        self.assertEqual(len(self.manager.skills), 3)

        # Verify new skill metadata
        skill2 = self.manager.skills["skill2"]
        self.assertEqual(skill2["name"], "skill2")
        self.assertEqual(skill2["description"], "Second skill")
        self.assertEqual(skill2["dir"], os.path.abspath(skill2_dir))
        self.assertEqual(skill2["source"], "monitored")

        # Modify description
        time.sleep(0.01)
        with open(
            os.path.join(skill_dir, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(
                "---\nname: skill1\ndescription: Modified description\n---\n",
            )
        os.utime(skill_dir, None)

        self.manager.get_agent_skill_prompt()
        self.assertEqual(
            self.manager.skills["skill1"]["description"],
            "Modified description",
        )

    def test_detect_deleted_skills(self) -> None:
        """Test detection of deleted skills."""
        skill1_dir = self._create_skill_dir("skill1", "First skill")
        skill2_dir = self._create_skill_dir("skill2", "Second skill")
        self._create_skill_dir("skill3", "Third skill")

        self.manager.monitor_agent_skills(self.test_dir)
        self.manager.get_agent_skill_prompt()
        self.assertEqual(len(self.manager.skills), 3)

        # Delete two skills
        time.sleep(0.01)
        shutil.rmtree(skill1_dir)
        shutil.rmtree(skill2_dir)

        self.manager.get_agent_skill_prompt()

        # Verify deleted skills removed
        self.assertEqual(len(self.manager.skills), 1)
        self.assertNotIn("skill1", self.manager.skills)
        self.assertNotIn("skill2", self.manager.skills)
        self.assertIn("skill3", self.manager.skills)

        # Verify cache cleaned up
        self.assertNotIn(
            os.path.abspath(skill1_dir),
            self.manager._skill_dir_mtime_cache,
        )

    def test_detect_all_skills_deleted(self) -> None:
        """Test that deleting all skills results in empty dict."""
        skill1_dir = self._create_skill_dir("skill1", "First skill")
        skill2_dir = self._create_skill_dir("skill2", "Second skill")

        self.manager.monitor_agent_skills(self.test_dir)
        self.manager.get_agent_skill_prompt()
        self.assertEqual(len(self.manager.skills), 2)

        # Delete all skills
        time.sleep(0.01)
        for skill_dir in [skill1_dir, skill2_dir]:
            shutil.rmtree(skill_dir)

        prompt = self.manager.get_agent_skill_prompt()

        # Verify all removed and prompt is None
        self.assertEqual(len(self.manager.skills), 0)
        self.assertIsNone(prompt)

    def test_detect_skill_name_changes(self) -> None:
        """Test detection of skill name changes and conflict handling."""
        skill_dir = self._create_skill_dir("old_name", "A test skill")
        self._create_skill_dir("skill2", "Second skill")

        self.manager.monitor_agent_skills(self.test_dir)
        self.manager.get_agent_skill_prompt()
        self.assertIn("old_name", self.manager.skills)

        # Change name and description
        time.sleep(0.01)
        with open(
            os.path.join(skill_dir, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("---\nname: new_name\ndescription: New description\n---\n")
        os.utime(skill_dir, None)

        self.manager.get_agent_skill_prompt()

        # Verify old name removed, new name added with new description
        self.assertEqual(len(self.manager.skills), 2)
        self.assertNotIn("old_name", self.manager.skills)
        self.assertIn("new_name", self.manager.skills)
        self.assertEqual(
            self.manager.skills["new_name"]["description"],
            "New description",
        )
        self.assertEqual(
            self.manager.skills["new_name"]["dir"],
            os.path.abspath(skill_dir),
        )

        # Test name change conflict (rename to existing skill name)
        time.sleep(0.01)
        with open(
            os.path.join(skill_dir, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(
                "---\nname: skill2\ndescription: Renamed to conflict\n---\n",
            )
        os.utime(skill_dir, None)

        self.manager.get_agent_skill_prompt()

        # Verify conflict handled (existing skill2 kept, new_name removed)
        self.assertIn("skill2", self.manager.skills)
        self.assertNotIn("new_name", self.manager.skills)
        self.assertEqual(
            self.manager.skills["skill2"]["description"],
            "Second skill",
        )

    def test_detect_skill_md_deletion_and_recreation(self) -> None:
        """Test detection of SKILL.md deletion and recreation."""
        skill1_dir = self._create_skill_dir("skill1", "First skill")
        self._create_skill_dir("skill2", "Second skill")

        self.manager.monitor_agent_skills(self.test_dir)
        self.manager.get_agent_skill_prompt()
        self.assertEqual(len(self.manager.skills), 2)

        # Delete SKILL.md from skill1
        time.sleep(0.01)
        skill_md_path = os.path.join(skill1_dir, "SKILL.md")
        os.remove(skill_md_path)
        os.utime(skill1_dir, None)

        self.manager.get_agent_skill_prompt()

        # Verify skill1 removed, skill2 remains
        self.assertEqual(len(self.manager.skills), 1)
        self.assertNotIn("skill1", self.manager.skills)
        self.assertIn("skill2", self.manager.skills)
        self.assertNotIn(
            os.path.abspath(skill1_dir),
            self.manager._skill_dir_mtime_cache,
        )

        # Recreate with different description
        time.sleep(0.01)
        with open(skill_md_path, "w", encoding="utf-8") as f:
            f.write(
                "---\nname: skill1\ndescription: Recreated description\n---\n",
            )
        os.utime(skill1_dir, None)
        os.utime(self.test_dir, None)

        self.manager.get_agent_skill_prompt()

        # Verify skill re-added with new description
        self.assertIn("skill1", self.manager.skills)
        self.assertEqual(
            self.manager.skills["skill1"]["description"],
            "Recreated description",
        )


class SkillManagerConcurrencyTest(SkillManagerTestBase):
    """Test thread safety of AgentSkillManager."""

    def test_concurrent_read_operations(self) -> None:
        """Test that multiple threads can read concurrently."""
        for i in range(5):
            skill_dir = self._create_skill_dir(f"skill_{i}", f"Skill {i}")
            self.manager.register_agent_skill(skill_dir)

        errors = []
        results = []

        def reader() -> None:
            try:
                for _ in range(50):
                    skills = self.manager.skills
                    results.append(len(skills))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertTrue(all(r == 5 for r in results))

    def test_concurrent_write_operations(self) -> None:
        """Test that multiple threads can write without race conditions."""
        errors = []

        def writer(thread_id: int) -> None:
            try:
                for i in range(5):
                    skill_name = f"skill_{thread_id}_{i}"
                    skill_dir = self._create_skill_dir(
                        skill_name,
                        f"Skill {thread_id}-{i}",
                    )
                    self.manager.register_agent_skill(skill_dir)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(i,)) for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertEqual(len(self.manager.skills), 25)

    def test_concurrent_read_write_operations(self) -> None:
        """Test mixed read and write operations from multiple threads."""
        for i in range(3):
            skill_dir = self._create_skill_dir(f"initial_{i}", f"Initial {i}")
            self.manager.register_agent_skill(skill_dir)

        errors = []
        read_counts = []

        def reader() -> None:
            try:
                for _ in range(30):
                    skills = self.manager.skills
                    read_counts.append(len(skills))
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def writer(thread_id: int) -> None:
            try:
                for i in range(5):
                    skill_name = f"new_{thread_id}_{i}"
                    skill_dir = self._create_skill_dir(
                        skill_name,
                        f"New {thread_id}-{i}",
                    )
                    self.manager.register_agent_skill(skill_dir)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=reader) for _ in range(3)]
        threads += [
            threading.Thread(target=writer, args=(i,)) for i in range(2)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertEqual(len(self.manager.skills), 13)
        self.assertTrue(all(3 <= count <= 13 for count in read_counts))

    def test_concurrent_get_prompt_and_no_deadlock(self) -> None:
        """Test that get_agent_skill_prompt is thread-safe."""
        monitored_dir = os.path.join(self.test_dir, "monitored")
        os.makedirs(monitored_dir)
        for i in range(3):
            skill_dir = os.path.join(monitored_dir, f"skill_{i}")
            os.makedirs(skill_dir)
            with open(
                os.path.join(skill_dir, "SKILL.md"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(f"---\nname: skill_{i}\ndescription: Skill {i}\n---\n")

        self.manager.monitor_agent_skills(monitored_dir)

        errors = []
        prompts = []

        def get_prompt() -> None:
            try:
                for _ in range(10):
                    prompt = self.manager.get_agent_skill_prompt()
                    prompts.append(prompt)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=get_prompt) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertTrue(all(p == prompts[0] for p in prompts))
        self.assertIsNotNone(prompts[0])
        for i in range(3):
            self.assertIn(f"skill_{i}", prompts[0])

        # Test no deadlock with nested calls
        skill_dir = self._create_skill_dir("test_skill", "Test skill")
        self.manager.register_agent_skill(skill_dir)

        def nested_operation() -> None:
            skills = self.manager.skills
            self.assertEqual(len(skills), 4)
            prompt = self.manager.get_agent_skill_prompt()
            self.assertIsNotNone(prompt)

        thread = threading.Thread(target=nested_operation)
        thread.start()
        thread.join(timeout=5.0)

        self.assertFalse(thread.is_alive(), "Deadlock detected!")
