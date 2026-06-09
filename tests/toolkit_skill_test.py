# -*- coding: utf-8 -*-
"""Test cases for Toolkit skill-related functionality."""
import json
import os
import tempfile
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.skill import SkillLoaderBase, Skill
from agentscope.tool import Toolkit, ToolChunk, ToolResponse, ToolGroup
from agentscope.message import ToolCallBlock
from agentscope.state import AgentState


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
        """Return a list of all skills."""
        return self._skills


class ToolkitSkillTest(IsolatedAsyncioTestCase):
    """Test cases for Toolkit skill functionality."""

    async def test_init_with_various_types(self) -> None:
        """Test Toolkit initialization with str path, SkillLoaderBase, and
        direct Skill objects, then assert get_skill_instructions output."""
        with tempfile.TemporaryDirectory() as skill_dir:
            # Create a minimal SKILL.md so LocalSkillLoader can load it
            skill_md_path = os.path.join(skill_dir, "SKILL.md")
            with open(skill_md_path, "w", encoding="utf-8") as f:
                f.write(
                    "---\n"
                    "name: path_skill\n"
                    "description: A skill loaded from a path\n"
                    "---\n\n"
                    "# Path Skill\n",
                )

            loader_skill = _make_skill(
                "loader_skill",
                description="A skill from loader",
                dir_="/loader/dir",
            )
            direct_skill = _make_skill(
                "direct_skill",
                description="A directly provided skill",
                dir_="/direct/dir",
            )

            toolkit = Toolkit(
                skills_or_loaders=[
                    skill_dir,  # str -> LocalSkillLoader
                    MockSkillLoader([loader_skill]),  # SkillLoaderBase
                    direct_skill,  # Skill directly
                ],
            )

            result = await toolkit.get_skill_instructions()

            self.assertEqual(
                result,
                # pylint: disable=line-too-long
                f"""<agent-skills>
Skills are a collection of instructions, scripts, and resources to extend your capabilities.

**IMPORTANT**: Skills are NOT tools, and you cannot call a skill directly. To use a skill, you MUST use the `Skill` tool to read the skill's full instructions, and then follow those instructions to use the tools and resources provided by the skill.

# Available Skills:
<skill>
<name>path_skill</name>
<description>A skill loaded from a path</description>
<dir>{skill_dir}</dir>
</skill>
<skill>
<name>loader_skill</name>
<description>A skill from loader</description>
<dir>/loader/dir</dir>
</skill>
<skill>
<name>direct_skill</name>
<description>A directly provided skill</description>
<dir>/direct/dir</dir>
</skill>
</agent-skills>""",  # noqa: E501
            )

    async def test_get_skill_instructions_no_skills(self) -> None:
        """Test that get_skill_instructions returns None when no skills
        registered."""
        toolkit = Toolkit()
        result = await toolkit.get_skill_instructions()
        self.assertIsNone(result)

    async def test_get_skill_instructions_multiple_loaders(self) -> None:
        """Test that get_skill_instructions aggregates skills from multiple
        loaders."""
        loader1 = MockSkillLoader([_make_skill("skill_x")])
        loader2 = MockSkillLoader([_make_skill("skill_y")])
        toolkit = Toolkit(skills_or_loaders=[loader1, loader2])

        result = await toolkit.get_skill_instructions()

        self.assertEqual(
            result,
            # pylint: disable=line-too-long
            """<agent-skills>
Skills are a collection of instructions, scripts, and resources to extend your capabilities.

**IMPORTANT**: Skills are NOT tools, and you cannot call a skill directly. To use a skill, you MUST use the `Skill` tool to read the skill's full instructions, and then follow those instructions to use the tools and resources provided by the skill.

# Available Skills:
<skill>
<name>skill_x</name>
<description>desc</description>
<dir>/tmp</dir>
</skill>
<skill>
<name>skill_y</name>
<description>desc</description>
<dir>/tmp</dir>
</skill>
</agent-skills>""",  # noqa: E501
        )

    async def test_get_skill_instructions_empty_loader(self) -> None:
        """Test that an empty loader contributes no skills."""
        loader = MockSkillLoader([])
        toolkit = Toolkit(skills_or_loaders=[loader])

        result = await toolkit.get_skill_instructions()
        self.assertIsNone(result)

    async def test_get_skill_instructions_includes_tool_group_skills(
        self,
    ) -> None:
        """Test that skill instructions include skills from custom groups."""
        toolkit = Toolkit(
            tool_groups=[
                ToolGroup(
                    name="repair",
                    description="Repair tools",
                    skills_or_loaders=[
                        MockSkillLoader([_make_skill("repair_skill")]),
                    ],
                ),
            ],
        )

        result = await toolkit.get_skill_instructions()

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("<name>repair_skill</name>", result)

    async def test_get_skill_instructions_filters_inactive_group_skills(
        self,
    ) -> None:
        """Test that inactive group skills are hidden from the prompt."""
        toolkit = Toolkit(
            skills_or_loaders=[MockSkillLoader([_make_skill("basic_skill")])],
            tool_groups=[
                ToolGroup(
                    name="repair",
                    description="Repair tools",
                    skills_or_loaders=[
                        MockSkillLoader([_make_skill("repair_skill")]),
                    ],
                ),
            ],
        )

        inactive_result = await toolkit.get_skill_instructions(
            activated_groups=[],
        )
        active_result = await toolkit.get_skill_instructions(
            activated_groups=["repair"],
        )

        self.assertIsNotNone(inactive_result)
        self.assertIsNotNone(active_result)
        assert inactive_result is not None
        assert active_result is not None
        self.assertIn("<name>basic_skill</name>", inactive_result)
        self.assertNotIn("<name>repair_skill</name>", inactive_result)
        self.assertIn("<name>basic_skill</name>", active_result)
        self.assertIn("<name>repair_skill</name>", active_result)


class ToolkitSkillViewerTest(IsolatedAsyncioTestCase):
    """Test cases for Toolkit SkillViewer functionality."""

    async def test_register_skill_and_get_function_schemas(self) -> None:
        """Test that registering skills makes SkillViewer available in
        function schemas."""
        skill = _make_skill("test_skill", description="A test skill")
        loader = MockSkillLoader([skill])
        toolkit = Toolkit(skills_or_loaders=[loader])

        schemas = await toolkit.get_tool_schemas()

        self.assertListEqual(
            schemas,
            [
                {
                    "type": "function",
                    "function": {
                        "name": "Skill",
                        "description": (
                            "Retrieve a skill within the conversation. "
                            "When users asks you to perform tasks, check if "
                            "any of the available skills match. "
                            "Skills provide specialized capabilities and "
                            "domain knowledge."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "skill": {
                                    "type": "string",
                                    "description": "The exact name of the "
                                    "skill to view. ",
                                },
                            },
                            "required": ["skill"],
                        },
                    },
                },
            ],
        )

    async def test_call_skill_viewer_success(self) -> None:
        """Test calling SkillViewer with an existing skill."""
        skill = _make_skill(
            "my_skill",
            description="My test skill",
            dir_="/test/dir",
        )
        skill.markdown = "# My Skill\nThis is the skill content."
        loader = MockSkillLoader([skill])
        toolkit = Toolkit(skills_or_loaders=[loader])

        tool_call = ToolCallBlock(
            id="test_call_1",
            name="Skill",
            input=json.dumps({"skill": "my_skill"}),
        )
        state = AgentState()

        chunks = []
        response = None
        async for result in toolkit.call_tool(tool_call, state):
            if isinstance(result, ToolChunk):
                chunks.append(result)
            elif isinstance(result, ToolResponse):
                response = result

        self.assertEqual(len(chunks), 1)
        self.assertDictEqual(
            chunks[0].model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "# My Skill\nThis is the skill content.",
                    },
                ],
                "state": "running",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        self.assertIsNotNone(response)
        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "# My Skill\nThis is the skill content.",
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "test_call_1",
            },
        )

    async def test_call_skill_viewer_not_found(self) -> None:
        """Test calling SkillViewer with a non-existent skill."""
        skill = _make_skill("existing_skill")
        loader = MockSkillLoader([skill])
        toolkit = Toolkit(skills_or_loaders=[loader])

        tool_call = ToolCallBlock(
            id="test_call_2",
            name="Skill",
            input=json.dumps({"skill": "non_existent_skill"}),
        )
        state = AgentState()

        chunks = []
        response = None
        async for result in toolkit.call_tool(tool_call, state):
            if isinstance(result, ToolChunk):
                chunks.append(result)
            elif isinstance(result, ToolResponse):
                response = result

        self.assertEqual(len(chunks), 1)
        self.assertDictEqual(
            chunks[0].model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "SkillNotFoundError: "
                        "Skill 'non_existent_skill' not found.",
                    },
                ],
                "state": "error",
                "is_last": True,
                "metadata": {},
                "id": AnyString(),
            },
        )

        self.assertIsNotNone(response)
        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "type": "text",
                        "id": AnyString(),
                        "text": "SkillNotFoundError: "
                        "Skill 'non_existent_skill' not found.",
                    },
                ],
                "state": "error",
                "metadata": {},
                "id": "test_call_2",
            },
        )

    async def test_skill_viewer_uses_active_tool_group_skills(self) -> None:
        """Test that activated custom-group skills expose SkillViewer."""
        skill = _make_skill("repair_skill")
        skill.markdown = "# Repair Skill\nUse this for repair tasks."
        toolkit = Toolkit(
            tool_groups=[
                ToolGroup(
                    name="repair",
                    description="Repair tools",
                    skills_or_loaders=[MockSkillLoader([skill])],
                ),
            ],
        )

        schema_names = [
            schema["function"]["name"]
            for schema in await toolkit.get_tool_schemas(groups=["repair"])
        ]
        self.assertIn("Skill", schema_names)

        tool_call = ToolCallBlock(
            id="test_call_group_skill",
            name="Skill",
            input=json.dumps({"skill": "repair_skill"}),
        )
        state = AgentState()
        state.tool_context.activated_groups.append("repair")

        chunks = []
        async for result in toolkit.call_tool(tool_call, state):
            if isinstance(result, ToolChunk):
                chunks.append(result)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(
            chunks[0].content[0].text,
            "# Repair Skill\nUse this for repair tasks.",
        )
