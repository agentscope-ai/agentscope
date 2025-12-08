# -*- coding: utf-8 -*-
"""The plan module related tests."""
import os
from unittest import IsolatedAsyncioTestCase

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.model import DashScopeChatModel
from agentscope.plan import SubTask, Plan, PlanNotebook


class PlanTest(IsolatedAsyncioTestCase):
    """Test the plan module."""

    async def asyncSetUp(self) -> None:
        """Set up the test case."""
        self.subtask1 = SubTask(
            name="Task 1",
            description="Description 1",
            expected_outcome="Expected outcome 1",
            state="done",
        )

        self.subtask2 = SubTask(
            name="Task 2",
            description="Description 2",
            expected_outcome="Expected outcome 2",
            state="in_progress",
        )

        self.subtask3 = SubTask(
            name="Task 3",
            description="Description 3",
            expected_outcome="Expected outcome 3",
            state="todo",
        )

        self.plan = Plan(
            name="Create website",
            description="Create a personal portfolio website.",
            expected_outcome="A new personal portfolio website is created on "
            "GitHub.",
            subtasks=[self.subtask1, self.subtask2, self.subtask3],
        )

    async def test_plan_model(self) -> None:
        """Test the models used in plan module."""

        self.assertEqual(
            self.subtask1.to_markdown(detailed=True),
            f"""- [x] Task 1
\t- Created At: {self.subtask1.created_at}
\t- Description: Description 1
\t- Expected Outcome: Expected outcome 1
\t- State: done
\t- Finished At: None
\t- Actual Outcome: None""",
        )

        self.assertEqual(
            self.subtask1.to_markdown(detailed=False),
            """- [x] Task 1""",
        )

        self.assertEqual(
            self.plan.to_markdown(detailed=True),
            f"""# Create website
**Description**: Create a personal portfolio website.
**Expected Outcome**: A new personal portfolio website is created on GitHub.
**State**: todo
**Created At**: {self.plan.created_at}
## Subtasks
- [x] Task 1
\t- Created At: {self.subtask1.created_at}
\t- Description: Description 1
\t- Expected Outcome: Expected outcome 1
\t- State: done
\t- Finished At: None
\t- Actual Outcome: None
- [ ] [WIP]Task 2
\t- Created At: {self.subtask2.created_at}
\t- Description: Description 2
\t- Expected Outcome: Expected outcome 2
\t- State: in_progress
- [ ] Task 3
\t- Created At: {self.subtask3.created_at}
\t- Description: Description 3
\t- Expected Outcome: Expected outcome 3
\t- State: todo""",
        )

        self.assertEqual(
            self.plan.to_markdown(detailed=True),
            f"""# Create website
**Description**: Create a personal portfolio website.
**Expected Outcome**: A new personal portfolio website is created on GitHub.
**State**: todo
**Created At**: {self.plan.created_at}
## Subtasks
- [x] Task 1
\t- Created At: {self.subtask1.created_at}
\t- Description: Description 1
\t- Expected Outcome: Expected outcome 1
\t- State: done
\t- Finished At: None
\t- Actual Outcome: None
- [ ] [WIP]Task 2
\t- Created At: {self.subtask2.created_at}
\t- Description: Description 2
\t- Expected Outcome: Expected outcome 2
\t- State: in_progress
- [ ] Task 3
\t- Created At: {self.subtask3.created_at}
\t- Description: Description 3
\t- Expected Outcome: Expected outcome 3
\t- State: todo""",
        )

    async def test_plan_subtasks(self) -> None:
        """Test the plan and subtask models."""
        plan_notebook = PlanNotebook()

        self.assertListEqual(
            [_.__name__ for _ in plan_notebook.list_tools()],
            [
                "view_subtasks",
                "update_subtask_state",
                "finish_subtask",
                "create_plan",
                "revise_current_plan",
                "finish_plan",
                "view_historical_plans",
                "recover_historical_plan",
            ],
        )

        plan_hint = await plan_notebook.get_current_hint()
        self.assertEqual(
            plan_hint.get_text_content(),
            "<system-hint>If the user's query is complex (e.g. "
            "programming a website, game or app), or requires a long chain of "
            "steps to complete (e.g. conduct research on a certain topic from "
            "different sources), you NEED to create a plan first by calling "
            "'create_plan'. Otherwise, you can directly execute the user's "
            "query without planning.</system-hint>",
        )

        res = await plan_notebook.create_plan(
            name="Example Plan",
            description="Example Description",
            expected_outcome="Example Expected Outcome",
            subtasks=[self.subtask1, self.subtask2, self.subtask3],
        )
        self.assertEqual(
            res.content[0]["text"],
            "Plan 'Example Plan' created successfully.",
        )

        res = await plan_notebook.view_subtasks([3])
        self.assertEqual(
            res.content[0]["text"],
            "Invalid subtask_idx '[3]'. Must be between 0 and 2.",
        )
        res = await plan_notebook.view_subtasks([0, 2])
        self.assertEqual(
            res.content[0]["text"],
            f"""Subtask at index 0:
```
- [x] Task 1
\t- Created At: {self.subtask1.created_at}
\t- Description: Description 1
\t- Expected Outcome: Expected outcome 1
\t- State: done
\t- Finished At: None
\t- Actual Outcome: None
```

Subtask at index 2:
```
- [ ] Task 3
\t- Created At: {self.subtask3.created_at}
\t- Description: Description 3
\t- Expected Outcome: Expected outcome 3
\t- State: todo
```
""",
        )

        await plan_notebook.revise_current_plan(
            1,
            action="add",
            subtask=SubTask(
                name="Task 11",
                description="Description 11",
                expected_outcome="Expected outcome 11",
            ),
        )
        self.assertEqual(
            plan_notebook.current_plan.subtasks[1].name,
            "Task 11",
        )
        self.assertEqual(
            len(plan_notebook.current_plan.subtasks),
            4,
        )

        res = await plan_notebook.revise_current_plan(
            1,
            "delete",
        )
        self.assertEqual(
            res.content[0]["text"],
            "Subtask (named 'Task 11') at index 1 is deleted successfully.",
        )
        self.assertEqual(
            len(plan_notebook.current_plan.subtasks),
            3,
        )

        res = await plan_notebook.revise_current_plan(
            1,
            "revise",
            subtask=SubTask(
                name="Task 22",
                description="Description 22",
                expected_outcome="Expected outcome 22",
            ),
        )
        self.assertEqual(
            res.content[0]["text"],
            "Subtask at index 1 is revised successfully.",
        )
        self.assertEqual(
            plan_notebook.current_plan.subtasks[1].name,
            "Task 22",
        )
        self.assertEqual(
            len(plan_notebook.current_plan.subtasks),
            3,
        )

        res = await plan_notebook.update_subtask_state(
            2,
            "in_progress",
        )
        self.assertEqual(
            res.content[0]["text"],
            "Subtask (at index 1) named 'Task 22' is not done yet. "
            "You should finish the previous subtasks first.",
        )

        await plan_notebook.update_subtask_state(0, "in_progress")
        res = await plan_notebook.update_subtask_state(
            1,
            "in_progress",
        )
        self.assertEqual(
            res.content[0]["text"],
            "Subtask (at index 0) named 'Task 1' is not done yet. You "
            "should finish the previous subtasks first.",
        )

        res = await plan_notebook.finish_subtask(
            0,
            "Fake outcome for task 1",
        )
        self.assertEqual(
            res.content[0]["text"],
            "Subtask (at index 0) named 'Task 1' is marked as done "
            "successfully. The next subtask named 'Task 22' is activated.",
        )
        self.assertEqual(
            plan_notebook.current_plan.subtasks[1].state,
            "in_progress",
        )

    async def test_serialization(self) -> None:
        """Test the serialization and deserialization of plan and subtask."""
        plan_notebook = PlanNotebook()
        agent = ReActAgent(
            name="Friday",
            sys_prompt="You are a helpful assistant named Friday. ",
            model=DashScopeChatModel(
                model_name="qwen-max",
                api_key=os.environ.get("DASH_API_KEY"),
            ),
            formatter=DashScopeChatFormatter(),
            plan_notebook=plan_notebook,
        )

        await plan_notebook.create_plan(
            name="text",
            description="abc",
            expected_outcome="edf",
            subtasks=[
                SubTask(
                    name="1",
                    description="1",
                    expected_outcome="1",
                ),
                SubTask(
                    name="2",
                    description="2",
                    expected_outcome="2",
                ),
            ],
        )

        self.assertIsNotNone(plan_notebook.current_plan)

        # Check the exported state
        state = agent.state_dict()
        subtasks = plan_notebook.current_plan.subtasks

        self.assertDictEqual(
            state,
            {
                "memory": {"content": []},
                "toolkit": {"active_groups": []},
                "plan_notebook": {
                    "storage": {
                        "plans": {},
                    },
                    "current_plan": {
                        "id": plan_notebook.current_plan.id,
                        "name": "text",
                        "description": "abc",
                        "expected_outcome": "edf",
                        "subtasks": [
                            {
                                "name": "1",
                                "description": "1",
                                "expected_outcome": "1",
                                "outcome": None,
                                "state": "todo",
                                "created_at": subtasks[0].created_at,
                                "finished_at": None,
                            },
                            {
                                "name": "2",
                                "description": "2",
                                "expected_outcome": "2",
                                "outcome": None,
                                "state": "todo",
                                "created_at": subtasks[1].created_at,
                                "finished_at": None,
                            },
                        ],
                        "created_at": plan_notebook.current_plan.created_at,
                        "state": "todo",
                        "finished_at": None,
                        "outcome": None,
                    },
                },
                "_reasoning_hint_msgs": {"content": []},
                "name": "Friday",
                "_sys_prompt": "You are a helpful assistant named Friday. ",
            },
        )

        # Test finish the plan serialization
        res = await plan_notebook.update_subtask_state(
            0,
            "in_progress",
        )
        self.assertEqual(
            res.content[0]["text"],
            "Subtask at index 0, named '1' is marked as 'in_progress' "
            "successfully. The plan state has been updated to 'in_progress'.",
        )

        self.assertEqual(
            plan_notebook.state_dict(),
            {
                "storage": {
                    "plans": {},
                },
                "current_plan": {
                    "id": plan_notebook.current_plan.id,
                    "name": "text",
                    "description": "abc",
                    "expected_outcome": "edf",
                    "subtasks": [
                        {
                            "name": "1",
                            "description": "1",
                            "expected_outcome": "1",
                            "state": "in_progress",
                            "created_at": subtasks[0].created_at,
                            "outcome": None,
                            "finished_at": None,
                        },
                        {
                            "name": "2",
                            "description": "2",
                            "expected_outcome": "2",
                            "state": "todo",
                            "created_at": subtasks[1].created_at,
                            "outcome": None,
                            "finished_at": None,
                        },
                    ],
                    "state": "in_progress",
                    "created_at": plan_notebook.current_plan.created_at,
                    "finished_at": None,
                    "outcome": None,
                },
            },
        )

        # When finish a subtask
        await plan_notebook.finish_subtask(
            0,
            subtask_outcome="abc",
        )
        self.assertDictEqual(
            plan_notebook.state_dict(),
            {
                "storage": {
                    "plans": {},
                },
                "current_plan": {
                    "id": plan_notebook.current_plan.id,
                    "name": "text",
                    "description": "abc",
                    "expected_outcome": "edf",
                    "created_at": plan_notebook.current_plan.created_at,
                    "subtasks": [
                        {
                            "name": "1",
                            "description": "1",
                            "expected_outcome": "1",
                            "state": "done",
                            "created_at": subtasks[0].created_at,
                            "finished_at": plan_notebook.current_plan.subtasks[
                                0
                            ].finished_at,
                            "outcome": "abc",
                        },
                        {
                            "name": "2",
                            "description": "2",
                            "expected_outcome": "2",
                            "state": "in_progress",
                            "created_at": subtasks[1].created_at,
                            "finished_at": None,
                            "outcome": None,
                        },
                    ],
                    "finished_at": None,
                    "outcome": None,
                    "state": "in_progress",
                },
            },
        )

        # Test deserialization
        await plan_notebook.finish_subtask(1, "def")
        self.assertDictEqual(
            plan_notebook.state_dict(),
            {
                "storage": {
                    "plans": {},
                },
                "current_plan": {
                    "id": plan_notebook.current_plan.id,
                    "name": "text",
                    "description": "abc",
                    "expected_outcome": "edf",
                    "created_at": plan_notebook.current_plan.created_at,
                    "subtasks": [
                        {
                            "name": "1",
                            "description": "1",
                            "expected_outcome": "1",
                            "state": "done",
                            "created_at": subtasks[0].created_at,
                            "finished_at": plan_notebook.current_plan.subtasks[
                                0
                            ].finished_at,
                            "outcome": "abc",
                        },
                        {
                            "name": "2",
                            "description": "2",
                            "expected_outcome": "2",
                            "state": "done",
                            "created_at": subtasks[1].created_at,
                            "finished_at": plan_notebook.current_plan.subtasks[
                                1
                            ].finished_at,
                            "outcome": "def",
                        },
                    ],
                    "finished_at": None,
                    "outcome": None,
                    "state": "in_progress",
                },
            },
        )

        # Finish the plan
        await plan_notebook.finish_plan("done", "Overall outcome")
        self.assertIsNone(
            plan_notebook.current_plan,
        )

        # Check the finished plan
        plan = (await plan_notebook.storage.get_plans())[0]

        self.assertDictEqual(
            plan_notebook.state_dict(),
            {
                "storage": {
                    "plans": {
                        plan.id: {
                            "id": plan.id,
                            "name": "text",
                            "description": "abc",
                            "expected_outcome": "edf",
                            "subtasks": [
                                {
                                    "name": "1",
                                    "description": "1",
                                    "expected_outcome": "1",
                                    "outcome": "abc",
                                    "state": "done",
                                    "created_at": plan.subtasks[0].created_at,
                                    "finished_at": plan.subtasks[
                                        0
                                    ].finished_at,
                                },
                                {
                                    "name": "2",
                                    "description": "2",
                                    "expected_outcome": "2",
                                    "outcome": "def",
                                    "state": "done",
                                    "created_at": plan.subtasks[1].created_at,
                                    "finished_at": plan.subtasks[
                                        1
                                    ].finished_at,
                                },
                            ],
                            "created_at": plan.created_at,
                            "state": "done",
                            "finished_at": plan.finished_at,
                            "outcome": "Overall outcome",
                        },
                    },
                },
                "current_plan": None,
            },
        )

        # Load the state
        new_plan_notebook = PlanNotebook()
        new_plan_notebook.load_state_dict(
            plan_notebook.state_dict(),
        )
        self.assertDictEqual(
            new_plan_notebook.state_dict(),
            {
                "storage": {
                    "plans": {
                        plan.id: {
                            "id": plan.id,
                            "name": "text",
                            "description": "abc",
                            "expected_outcome": "edf",
                            "subtasks": [
                                {
                                    "name": "1",
                                    "description": "1",
                                    "expected_outcome": "1",
                                    "outcome": "abc",
                                    "state": "done",
                                    "created_at": plan.subtasks[0].created_at,
                                    "finished_at": plan.subtasks[
                                        0
                                    ].finished_at,
                                },
                                {
                                    "name": "2",
                                    "description": "2",
                                    "expected_outcome": "2",
                                    "outcome": "def",
                                    "state": "done",
                                    "created_at": plan.subtasks[1].created_at,
                                    "finished_at": plan.subtasks[
                                        1
                                    ].finished_at,
                                },
                            ],
                            "created_at": plan.created_at,
                            "state": "done",
                            "finished_at": plan.finished_at,
                            "outcome": "Overall outcome",
                        },
                    },
                },
                "current_plan": None,
            },
        )

        plan_notebook.current_plan = None
        self.assertIsNone(
            agent.plan_notebook.current_plan,
        )
        agent.load_state_dict(state)
        self.assertIsNotNone(
            agent.plan_notebook.current_plan,
        )
