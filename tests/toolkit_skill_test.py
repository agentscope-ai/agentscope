# -*- coding: utf-8 -*-
"""Test cases for Toolkit skill-related functionality."""
import warnings
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import SkillLoaderBase, LocalSkillLoader
from agentscope.tool import Toolkit
from agentscope.tool import Skill


def _make_skill(
    name: str,
    description: str = "desc",
    dir_: str = "/tmp",
) -> Skill:
    """Helper function to create a Skill object for testing."""
    return Skill(
        name=name,
        description=description,
        dir=dir_,
        markdown="",
        updated_at=0.0,
    )


class MockSkillLoader(SkillLoaderBase):
    """A mock skill loader for testing."""

    def __init__(self, skills: list[Skill]) -> None:
        self._skills = skills

    async def list_skills(self) -> list[Skill]:
        return self._skills


class ToolkitSkillTest(IsolatedAsyncioTestCase):
    """Test cases for Toolkit skill functionality."""

    async def test_init_with_skill_loader(self) -> None:
        """Test that Toolkit accepts SkillLoaderBase instances in
        constructor."""
        loader = MockSkillLoader([_make_skill("skill_a")])
        toolkit = Toolkit(skills=[loader])

        self.assertEqual(len(toolkit.skills), 1)
        self.assertIs(toolkit.skills[0], loader)

    async def test_init_with_string_path(self) -> None:
        """Test that Toolkit wraps string paths in LocalSkillLoader."""

        toolkit = Toolkit(skills=["/some/path"])

        self.assertEqual(len(toolkit.skills), 1)
        self.assertIsInstance(toolkit.skills[0], LocalSkillLoader)

    async def test_init_with_mixed_skills(self) -> None:
        """Test that Toolkit handles mixed string and loader inputs."""

        loader = MockSkillLoader([_make_skill("skill_b")])
        toolkit = Toolkit(skills=["/some/path", loader])

        self.assertEqual(len(toolkit.skills), 2)
        self.assertIsInstance(toolkit.skills[0], LocalSkillLoader)
        self.assertIs(toolkit.skills[1], loader)

    async def test_init_with_invalid_skill_type(self) -> None:
        """Test that Toolkit raises TypeError for invalid skill types."""
        with self.assertRaises(TypeError):
            Toolkit(skills=[123])  # type: ignore

    async def test_get_skill_instructions_no_skills(self) -> None:
        """Test that get_skill_instructions returns None when no skills
        registered."""
        toolkit = Toolkit()
        result = await toolkit.get_skill_instructions()
        self.assertIsNone(result)

    async def test_get_skill_instructions_with_skills(self) -> None:
        """Test that get_skill_instructions returns correct prompt."""
        skill = _make_skill(
            "my_skill",
            description="Does something useful",
            dir_="/skills/my_skill",
        )
        loader = MockSkillLoader([skill])
        toolkit = Toolkit(skills=[loader])

        result = await toolkit.get_skill_instructions()

        self.assertIsNotNone(result)
        self.assertIn("my_skill", result)
        self.assertIn("Does something useful", result)
        self.assertIn("/skills/my_skill", result)

    async def test_get_skill_instructions_multiple_loaders(self) -> None:
        """Test that get_skill_instructions aggregates skills from multiple
        loaders."""
        loader1 = MockSkillLoader([_make_skill("skill_x")])
        loader2 = MockSkillLoader([_make_skill("skill_y")])
        toolkit = Toolkit(skills=[loader1, loader2])

        result = await toolkit.get_skill_instructions()

        self.assertIsNotNone(result)
        self.assertIn("skill_x", result)
        self.assertIn("skill_y", result)

    async def test_duplicate_skill_name_renamed(self) -> None:
        """Test that duplicate skill names are renamed with a numeric
        suffix."""
        loader1 = MockSkillLoader(
            [_make_skill("duplicate_skill", dir_="/dir1")],
        )
        loader2 = MockSkillLoader(
            [_make_skill("duplicate_skill", dir_="/dir2")],
        )
        toolkit = Toolkit(skills=[loader1, loader2])

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = await toolkit.get_skill_instructions()

        self.assertIsNotNone(result)
        self.assertIn("duplicate_skill", result)
        self.assertIn("duplicate_skill_1", result)

        # A warning should have been issued
        self.assertTrue(
            any("duplicate_skill" in str(w.message) for w in caught),
            "Expected a warning about duplicate skill name",
        )

    async def test_duplicate_skill_name_multiple_conflicts(self) -> None:
        """Test that multiple duplicates get incrementing suffixes."""
        loader1 = MockSkillLoader([_make_skill("dup", dir_="/dir1")])
        loader2 = MockSkillLoader([_make_skill("dup", dir_="/dir2")])
        loader3 = MockSkillLoader([_make_skill("dup", dir_="/dir3")])
        toolkit = Toolkit(skills=[loader1, loader2, loader3])

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = await toolkit.get_skill_instructions()

        self.assertIsNotNone(result)
        self.assertIn("dup", result)
        self.assertIn("dup_1", result)
        self.assertIn("dup_2", result)

    async def test_get_skill_instructions_empty_loader(self) -> None:
        """Test that an empty loader contributes no skills."""
        loader = MockSkillLoader([])
        toolkit = Toolkit(skills=[loader])

        result = await toolkit.get_skill_instructions()
        self.assertIsNone(result)
