# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Test cases for LocalSkillLoader."""
import os
import tempfile
import shutil
import time
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.skill import LocalSkillLoader


class SkillLoaderTest(IsolatedAsyncioTestCase):
    """Test cases for LocalSkillLoader."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        # Create a temporary directory for testing
        self.test_dir = tempfile.mkdtemp()

        # Create test SKILL.md files
        # 1. Root level skill
        self.root_skill_content = """---
name: root_skill
description: A skill in the root directory
---

This is the root skill content.
"""
        with open(
            os.path.join(self.test_dir, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(self.root_skill_content)

        # 2. Subdirectory skill
        self.subdir1 = os.path.join(self.test_dir, "subdir1")
        os.makedirs(self.subdir1)
        self.subdir1_skill_content = """---
name: subdir1_skill
description: A skill in subdirectory 1
---

This is the subdir1 skill content.
"""
        with open(
            os.path.join(self.subdir1, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(self.subdir1_skill_content)

        # 3. Nested subdirectory skill
        self.subdir2 = os.path.join(self.test_dir, "subdir1", "subdir2")
        os.makedirs(self.subdir2)
        self.subdir2_skill_content = """---
name: subdir2_skill
description: A skill in nested subdirectory 2
---

This is the subdir2 skill content.
"""
        with open(
            os.path.join(self.subdir2, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(self.subdir2_skill_content)

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        # Remove the temporary directory
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    async def test_nonexistent_skill(self) -> None:
        """Test loading from a directory without SKILL.md."""
        # Create a directory without SKILL.md
        empty_dir = tempfile.mkdtemp()
        try:
            loader = LocalSkillLoader(empty_dir, scan_subdir=False)
            skills = await loader.list_skills()
            self.assertEqual(len(skills), 0)
        finally:
            shutil.rmtree(empty_dir)

    async def test_scan_subdir_flag_controls_subdirectory_scanning(
        self,
    ) -> None:
        """Test that scan_subdir only controls subdirectory scanning.

        The root directory is always scanned regardless of scan_subdir.
        When scan_subdir=False, only root SKILL.md is loaded.
        When scan_subdir=True, root + all subdirectory SKILL.md files
        are loaded.
        """
        loader_no_scan = LocalSkillLoader(self.test_dir, scan_subdir=False)
        skills_no_scan = await loader_no_scan.list_skills()

        loader_with_scan = LocalSkillLoader(self.test_dir, scan_subdir=True)
        skills_with_scan = await loader_with_scan.list_skills()

        # scan_subdir=False: only root skill
        self.assertEqual(len(skills_no_scan), 1)
        self.assertEqual(skills_no_scan[0].name, "root_skill")

        # scan_subdir=True: root skill + both subdirectory skills
        self.assertEqual(len(skills_with_scan), 3)
        skill_names = {s.name for s in skills_with_scan}
        self.assertIn("root_skill", skill_names)
        self.assertIn("subdir1_skill", skill_names)
        self.assertIn("subdir2_skill", skill_names)

    async def test_load_skill_without_scan_subdir(self) -> None:
        """Test loading skill from root directory only (scan_subdir=False)."""
        loader = LocalSkillLoader(self.test_dir, scan_subdir=False)
        skills = await loader.list_skills()

        # Should only load the root skill
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, "root_skill")
        self.assertEqual(
            skills[0].description,
            "A skill in the root directory",
        )
        self.assertEqual(skills[0].dir, self.test_dir)
        self.assertIn("This is the root skill content.", skills[0].markdown)
        self.assertIsInstance(skills[0].updated_at, float)
        self.assertGreater(skills[0].updated_at, 0)

    async def test_load_skill_with_scan_subdir(self) -> None:
        """Test loading skills from subdirectories (scan_subdir=True)."""
        loader = LocalSkillLoader(self.test_dir, scan_subdir=True)
        skills = await loader.list_skills()

        # Should load all three skills
        self.assertEqual(len(skills), 3)

        # Check skill names
        skill_names = {skill.name for skill in skills}
        self.assertEqual(
            skill_names,
            {"root_skill", "subdir1_skill", "subdir2_skill"},
        )

        # Verify each skill has correct attributes
        for skill in skills:
            self.assertIsInstance(skill.name, str)
            self.assertIsInstance(skill.description, str)
            self.assertIsInstance(skill.dir, str)
            self.assertIsInstance(skill.markdown, str)
            self.assertIsInstance(skill.updated_at, float)
            self.assertGreater(skill.updated_at, 0)

    async def test_cache_mechanism(self) -> None:
        """Test that cache is used when file is not modified."""
        loader = LocalSkillLoader(self.test_dir, scan_subdir=False)

        # First load - should read from file
        skills_first = await loader.list_skills()
        self.assertEqual(len(skills_first), 1)
        first_skill = skills_first[0]

        # Verify cache is populated
        self.assertIn(self.test_dir, loader._cache)
        cached_skill = loader._cache[self.test_dir]
        self.assertEqual(cached_skill.name, first_skill.name)
        self.assertEqual(cached_skill.updated_at, first_skill.updated_at)

        # Second load - should use cache (file not modified)
        skills_second = await loader.list_skills()
        self.assertEqual(len(skills_second), 1)
        second_skill = skills_second[0]

        # Should return the same cached object
        self.assertIs(second_skill, cached_skill)
        self.assertEqual(second_skill.updated_at, first_skill.updated_at)

        # Modify the file
        time.sleep(0.01)  # Ensure different mtime
        modified_content = """---
name: modified_root_skill
description: Modified skill description
---

This is the modified content.
"""
        with open(
            os.path.join(self.test_dir, "SKILL.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(modified_content)

        # Third load - should detect change and reload
        skills_third = await loader.list_skills()
        self.assertEqual(len(skills_third), 1)
        third_skill = skills_third[0]

        # Should have new content
        self.assertEqual(third_skill.name, "modified_root_skill")
        self.assertEqual(third_skill.description, "Modified skill description")
        self.assertIn("This is the modified content.", third_skill.markdown)
        self.assertNotEqual(third_skill.updated_at, first_skill.updated_at)

        # Cache should be updated
        self.assertIn(self.test_dir, loader._cache)
        self.assertEqual(
            loader._cache[self.test_dir].name,
            "modified_root_skill",
        )
