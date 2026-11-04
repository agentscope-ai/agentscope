# -*- coding: utf-8 -*-
"""Unit tests for task tools."""
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.state import AgentState, Task
from agentscope.tool import TaskCreate, TaskGet, TaskList, TaskUpdate


class TestTaskCreate(IsolatedAsyncioTestCase):
    """Test cases for TaskCreate tool."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.agent_state = AgentState()
        self.task_create = TaskCreate()

    async def test_create_single_task(self) -> None:
        """Test creating a single task."""
        result = await self.task_create(
            subject="Test Task 1",
            description="This is a test task",
            _agent_state=self.agent_state,
        )

        # Check result using model_dump
        task_id = self.agent_state.tasks_context.tasks[0].id
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": f"Task (id={task_id}) created successfully: "
                    f"Test Task 1",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)

        # Check task was added to agent state using model_dump
        tasks_dump = [
            task.model_dump() for task in self.agent_state.tasks_context.tasks
        ]
        expected = [
            {
                "subject": "Test Task 1",
                "description": "This is a test task",
                "metadata": {},
                "created_at": AnyString(),
                "state": "pending",
                "id": task_id,
                "owner": None,
                "blocks": [],
                "blocked_by": [],
            },
        ]
        self.assertEqual(tasks_dump, expected)

    async def test_create_multiple_tasks(self) -> None:
        """Test creating multiple tasks."""
        # Create first task
        result1 = await self.task_create(
            subject="Task 1",
            description="First task",
            _agent_state=self.agent_state,
        )
        task1_id = self.agent_state.tasks_context.tasks[0].id
        expected_result1 = {
            "content": [
                {
                    "text": f"Task (id={task1_id}) created successfully: "
                    f"Task 1",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result1.model_dump(mode="json"), expected_result1)

        # Create second task
        result2 = await self.task_create(
            subject="Task 2",
            description="Second task",
            _agent_state=self.agent_state,
        )
        task2_id = self.agent_state.tasks_context.tasks[1].id
        expected_result2 = {
            "content": [
                {
                    "text": f"Task (id={task2_id}) created successfully: "
                    f"Task 2",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result2.model_dump(mode="json"), expected_result2)

        # Create third task with metadata
        result3 = await self.task_create(
            subject="Task 3",
            description="Third task",
            metadata={"priority": "high"},
            _agent_state=self.agent_state,
        )
        task3_id = self.agent_state.tasks_context.tasks[2].id
        expected_result3 = {
            "content": [
                {
                    "text": f"Task (id={task3_id}) created successfully: "
                    f"Task 3",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result3.model_dump(mode="json"), expected_result3)

        # Check all tasks using model_dump
        tasks_dump = [
            task.model_dump() for task in self.agent_state.tasks_context.tasks
        ]
        expected = [
            {
                "subject": "Task 1",
                "description": "First task",
                "metadata": {},
                "created_at": AnyString(),
                "state": "pending",
                "id": AnyString(),
                "owner": None,
                "blocks": [],
                "blocked_by": [],
            },
            {
                "subject": "Task 2",
                "description": "Second task",
                "metadata": {},
                "created_at": AnyString(),
                "state": "pending",
                "id": AnyString(),
                "owner": None,
                "blocks": [],
                "blocked_by": [],
            },
            {
                "subject": "Task 3",
                "description": "Third task",
                "metadata": {"priority": "high"},
                "created_at": AnyString(),
                "state": "pending",
                "id": AnyString(),
                "owner": None,
                "blocks": [],
                "blocked_by": [],
            },
        ]
        self.assertEqual(tasks_dump, expected)

    async def test_create_task_with_metadata(self) -> None:
        """Test creating a task with metadata."""
        metadata = {"priority": "high", "tags": ["urgent", "bug"]}
        result = await self.task_create(
            subject="Bug Fix",
            description="Fix critical bug",
            metadata=metadata,
            _agent_state=self.agent_state,
        )

        # Check result using model_dump
        task_id = self.agent_state.tasks_context.tasks[0].id
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": f"Task (id={task_id}) created successfully: "
                    f"Bug Fix",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)

        # Check task using model_dump
        tasks_dump = [
            task.model_dump() for task in self.agent_state.tasks_context.tasks
        ]
        expected = [
            {
                "subject": "Bug Fix",
                "description": "Fix critical bug",
                "metadata": {"priority": "high", "tags": ["urgent", "bug"]},
                "created_at": AnyString(),
                "state": "pending",
                "id": AnyString(),
                "owner": None,
                "blocks": [],
                "blocked_by": [],
            },
        ]
        self.assertEqual(tasks_dump, expected)


class TestTaskList(IsolatedAsyncioTestCase):
    """Test cases for TaskList tool."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.agent_state = AgentState()
        self.task_list = TaskList()
        self.task_create = TaskCreate()

    async def test_list_no_tasks(self) -> None:
        """Test listing when there are no tasks."""
        result = await self.task_list(_agent_state=self.agent_state)

        self.assertEqual(len(result.content), 1)
        self.assertEqual(result.content[0].text, "No tasks available.")

    async def test_list_with_tasks(self) -> None:
        """Test listing when there are tasks."""
        # Create multiple tasks
        await self.task_create(
            subject="Task 1",
            description="First task",
            _agent_state=self.agent_state,
        )
        await self.task_create(
            subject="Task 2",
            description="Second task",
            _agent_state=self.agent_state,
        )
        await self.task_create(
            subject="Task 3",
            description="Third task",
            _agent_state=self.agent_state,
        )

        task1_id = self.agent_state.tasks_context.tasks[0].id
        task2_id = self.agent_state.tasks_context.tasks[1].id
        task3_id = self.agent_state.tasks_context.tasks[2].id

        # List tasks
        result = await self.task_list(_agent_state=self.agent_state)

        # Check result using model_dump
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": f"{task1_id} [pending] Task 1\n"
                    f"{task2_id} [pending] Task 2\n"
                    f"{task3_id} [pending] Task 3",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)


class TestTaskGet(IsolatedAsyncioTestCase):
    """Test cases for TaskGet tool."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.agent_state = AgentState()
        self.task_get = TaskGet()
        self.task_create = TaskCreate()

    async def test_get_existing_task(self) -> None:
        """Test getting an existing task."""
        # Create a task
        await self.task_create(
            subject="Test Task",
            description="This is a test task with details",
            metadata={"priority": "high"},
            _agent_state=self.agent_state,
        )

        task_id = self.agent_state.tasks_context.tasks[0].id

        # Get the task
        result = await self.task_get(
            task_id=task_id,
            _agent_state=self.agent_state,
        )

        # Check result using model_dump
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": f"Task (id={task_id}): Test Task\n"
                    f"Status: pending\n"
                    f"Description: This is a test task with details\n"
                    f"Metadata: {{'priority': 'high'}}",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)

    async def test_get_nonexistent_task(self) -> None:
        """Test getting a task that doesn't exist."""
        result = await self.task_get(
            task_id="nonexistent-id",
            _agent_state=self.agent_state,
        )

        # Check result using model_dump
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": "Task not found",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "error",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)


class TestTaskUpdate(IsolatedAsyncioTestCase):
    """Test cases for TaskUpdate tool."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.agent_state = AgentState()
        self.task_update = TaskUpdate()
        self.task_create = TaskCreate()

    async def test_update_subject(self) -> None:
        """Test updating task subject."""
        # Create a task
        await self.task_create(
            subject="Original Subject",
            description="Test description",
            _agent_state=self.agent_state,
        )
        task_id = self.agent_state.tasks_context.tasks[0].id

        # Update subject
        result = await self.task_update(
            task_id=task_id,
            subject="Updated Subject",
            _agent_state=self.agent_state,
        )

        # Check result using model_dump
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": f"Update task (id={task_id}) subject.",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)

        # Check task using model_dump
        tasks_dump = [
            task.model_dump() for task in self.agent_state.tasks_context.tasks
        ]
        expected = [
            {
                "subject": "Updated Subject",
                "description": "Test description",
                "metadata": {},
                "created_at": AnyString(),
                "state": "pending",
                "id": task_id,
                "owner": None,
                "blocks": [],
                "blocked_by": [],
            },
        ]
        self.assertEqual(tasks_dump, expected)

    async def test_update_description(self) -> None:
        """Test updating task description."""
        # Create a task
        await self.task_create(
            subject="Test Task",
            description="Original description",
            _agent_state=self.agent_state,
        )
        task_id = self.agent_state.tasks_context.tasks[0].id

        # Update description
        result = await self.task_update(
            task_id=task_id,
            description="Updated description",
            _agent_state=self.agent_state,
        )

        # Check result using model_dump
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": f"Update task (id={task_id}) description.",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)

        # Check task using model_dump
        tasks_dump = [
            task.model_dump() for task in self.agent_state.tasks_context.tasks
        ]
        expected = [
            {
                "subject": "Test Task",
                "description": "Updated description",
                "metadata": {},
                "created_at": AnyString(),
                "state": "pending",
                "id": task_id,
                "owner": None,
                "blocks": [],
                "blocked_by": [],
            },
        ]
        self.assertEqual(tasks_dump, expected)

    async def test_update_status(self) -> None:
        """Test updating task status."""
        # Create a task
        await self.task_create(
            subject="Test Task",
            description="Test description",
            _agent_state=self.agent_state,
        )
        task_id = self.agent_state.tasks_context.tasks[0].id

        # Update status to in_progress
        result = await self.task_update(
            task_id=task_id,
            status="in_progress",
            _agent_state=self.agent_state,
        )

        # Check result using model_dump
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": f"Update task (id={task_id}) status.",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)

        # Check task using model_dump
        tasks_dump = [
            task.model_dump() for task in self.agent_state.tasks_context.tasks
        ]
        expected = [
            {
                "subject": "Test Task",
                "description": "Test description",
                "metadata": {},
                "created_at": AnyString(),
                "state": "in_progress",
                "id": task_id,
                "owner": None,
                "blocks": [],
                "blocked_by": [],
            },
        ]
        self.assertEqual(tasks_dump, expected)

        # Update status to completed
        result = await self.task_update(
            task_id=task_id,
            status="completed",
            _agent_state=self.agent_state,
        )

        # Check result using model_dump
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": f"Update task (id={task_id}) status.\n\n"
                    f"Task completed. "
                    f"Call TaskList now to find your next available "
                    f"task or see if your work unblocked others.",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)

        # Check task using model_dump
        tasks_dump = [
            task.model_dump() for task in self.agent_state.tasks_context.tasks
        ]
        expected[0]["state"] = "completed"
        self.assertEqual(tasks_dump, expected)

        # Test deleted status - create a new task first
        await self.task_create(
            subject="Task to Delete",
            description="This task will be deleted",
            _agent_state=self.agent_state,
        )
        task_to_delete_id = self.agent_state.tasks_context.tasks[1].id

        # Update status to deleted
        result = await self.task_update(
            task_id=task_to_delete_id,
            status="deleted",
            _agent_state=self.agent_state,
        )

        # Check result using model_dump
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": f"Task (id={task_to_delete_id}) has been deleted.",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)

        # Check that only the first task remains
        self.assertEqual(len(self.agent_state.tasks_context.tasks), 1)
        self.assertEqual(self.agent_state.tasks_context.tasks[0].id, task_id)

    async def test_update_owner(self) -> None:
        """Test updating task owner."""
        # Create a task
        await self.task_create(
            subject="Test Task",
            description="Test description",
            _agent_state=self.agent_state,
        )
        task_id = self.agent_state.tasks_context.tasks[0].id

        # Update owner
        result = await self.task_update(
            task_id=task_id,
            owner="agent-1",
            _agent_state=self.agent_state,
        )

        # Check result using model_dump
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": f"Update task (id={task_id}) owner.",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)

        # Check task using model_dump
        tasks_dump = [
            task.model_dump() for task in self.agent_state.tasks_context.tasks
        ]
        expected = [
            {
                "subject": "Test Task",
                "description": "Test description",
                "metadata": {},
                "created_at": AnyString(),
                "state": "pending",
                "id": task_id,
                "owner": "agent-1",
                "blocks": [],
                "blocked_by": [],
            },
        ]
        self.assertEqual(tasks_dump, expected)

    async def test_update_metadata(self) -> None:
        """Test updating task metadata."""
        # Create a task with initial metadata
        await self.task_create(
            subject="Test Task",
            description="Test description",
            metadata={"priority": "low", "tags": ["test"]},
            _agent_state=self.agent_state,
        )
        task_id = self.agent_state.tasks_context.tasks[0].id

        # Update metadata
        result = await self.task_update(
            task_id=task_id,
            metadata={"priority": "high", "new_field": "value"},
            _agent_state=self.agent_state,
        )

        # Check result using model_dump
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": f"Update task (id={task_id}) metadata.",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)

        # Check task using model_dump
        tasks_dump = [
            task.model_dump() for task in self.agent_state.tasks_context.tasks
        ]
        expected = [
            {
                "subject": "Test Task",
                "description": "Test description",
                "metadata": {
                    "priority": "high",
                    "tags": ["test"],
                    "new_field": "value",
                },
                "created_at": AnyString(),
                "state": "pending",
                "id": task_id,
                "owner": None,
                "blocks": [],
                "blocked_by": [],
            },
        ]
        self.assertEqual(tasks_dump, expected)

    async def test_update_add_blocks(self) -> None:
        """Test updating task blocks relationship."""
        # Create two tasks
        await self.task_create(
            subject="Task 1",
            description="First task",
            _agent_state=self.agent_state,
        )
        await self.task_create(
            subject="Task 2",
            description="Second task",
            _agent_state=self.agent_state,
        )

        task1_id = self.agent_state.tasks_context.tasks[0].id
        task2_id = self.agent_state.tasks_context.tasks[1].id

        # Update task1 to block task2
        result = await self.task_update(
            task_id=task1_id,
            add_blocks=[task2_id],
            _agent_state=self.agent_state,
        )

        # Check result using model_dump
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": f"Update task (id={task1_id}) add_blocks.",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)

        # Check tasks using model_dump
        tasks_dump = [
            task.model_dump() for task in self.agent_state.tasks_context.tasks
        ]
        expected = [
            {
                "subject": "Task 1",
                "description": "First task",
                "metadata": {},
                "created_at": AnyString(),
                "state": "pending",
                "id": task1_id,
                "owner": None,
                "blocks": [task2_id],
                "blocked_by": [],
            },
            {
                "subject": "Task 2",
                "description": "Second task",
                "metadata": {},
                "created_at": AnyString(),
                "state": "pending",
                "id": task2_id,
                "owner": None,
                "blocks": [],
                "blocked_by": [task1_id],
            },
        ]
        self.assertEqual(tasks_dump, expected)

    async def test_update_add_blocked_by(self) -> None:
        """Test updating task blockedBy relationship."""
        # Create two tasks
        await self.task_create(
            subject="Task 1",
            description="First task",
            _agent_state=self.agent_state,
        )
        await self.task_create(
            subject="Task 2",
            description="Second task",
            _agent_state=self.agent_state,
        )

        task1_id = self.agent_state.tasks_context.tasks[0].id
        task2_id = self.agent_state.tasks_context.tasks[1].id

        # Update task2 to be blocked by task1
        result = await self.task_update(
            task_id=task2_id,
            add_blocked_by=[task1_id],
            _agent_state=self.agent_state,
        )

        # Check result using model_dump
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": f"Update task (id={task2_id}) add_blocked_by.",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)

        # Check tasks using model_dump
        tasks_dump = [
            task.model_dump() for task in self.agent_state.tasks_context.tasks
        ]
        expected = [
            {
                "subject": "Task 1",
                "description": "First task",
                "metadata": {},
                "created_at": AnyString(),
                "state": "pending",
                "id": task1_id,
                "owner": None,
                "blocks": [task2_id],
                "blocked_by": [],
            },
            {
                "subject": "Task 2",
                "description": "Second task",
                "metadata": {},
                "created_at": AnyString(),
                "state": "pending",
                "id": task2_id,
                "owner": None,
                "blocks": [],
                "blocked_by": [task1_id],
            },
        ]
        self.assertEqual(tasks_dump, expected)

    async def test_update_completed_unblocks_dependents(self) -> None:
        """Test completing a task removes it from dependents' blocked_by."""
        await self.task_create(
            subject="Task 1",
            description="First task",
            _agent_state=self.agent_state,
        )
        await self.task_create(
            subject="Task 2",
            description="Second task",
            _agent_state=self.agent_state,
        )
        await self.task_create(
            subject="Task 3",
            description="Third task",
            _agent_state=self.agent_state,
        )
        task1_id = self.agent_state.tasks_context.tasks[0].id
        task2_id = self.agent_state.tasks_context.tasks[1].id
        task3_id = self.agent_state.tasks_context.tasks[2].id

        await self.task_update(
            task_id=task3_id,
            add_blocked_by=[task1_id, task2_id],
            _agent_state=self.agent_state,
        )

        # Complete task1, task3 should remain blocked by task2 only
        result = await self.task_update(
            task_id=task1_id,
            status="completed",
            _agent_state=self.agent_state,
        )

        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": f"Update task (id={task1_id}) status.\n\n"
                    f"Task completed. "
                    f"Call TaskList now to find your next available "
                    f"task or see if your work unblocked others.",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)

        tasks_dump = [
            task.model_dump() for task in self.agent_state.tasks_context.tasks
        ]
        expected = [
            {
                "subject": "Task 1",
                "description": "First task",
                "metadata": {},
                "created_at": AnyString(),
                "state": "completed",
                "id": task1_id,
                "owner": None,
                "blocks": [task3_id],
                "blocked_by": [],
            },
            {
                "subject": "Task 2",
                "description": "Second task",
                "metadata": {},
                "created_at": AnyString(),
                "state": "pending",
                "id": task2_id,
                "owner": None,
                "blocks": [task3_id],
                "blocked_by": [],
            },
            {
                "subject": "Task 3",
                "description": "Third task",
                "metadata": {},
                "created_at": AnyString(),
                "state": "pending",
                "id": task3_id,
                "owner": None,
                "blocks": [],
                "blocked_by": [task2_id],
            },
        ]
        self.assertEqual(tasks_dump, expected)

        result = await TaskList()(_agent_state=self.agent_state)
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": f"{task1_id} [completed] Task 1\n"
                    f"{task2_id} [pending] Task 2\n"
                    f"{task3_id} [pending] Task 3[blocked by {task2_id}]",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)

    async def test_update_delete_task(self) -> None:
        """Test deleting a task and removing it from blocks/blocked_by."""
        # Create three tasks with dependencies
        await self.task_create(
            subject="Task 1",
            description="First task",
            _agent_state=self.agent_state,
        )
        await self.task_create(
            subject="Task 2",
            description="Second task",
            _agent_state=self.agent_state,
        )
        await self.task_create(
            subject="Task 3",
            description="Third task",
            _agent_state=self.agent_state,
        )

        task1_id = self.agent_state.tasks_context.tasks[0].id
        task2_id = self.agent_state.tasks_context.tasks[1].id
        task3_id = self.agent_state.tasks_context.tasks[2].id

        # Set up dependencies: Task 1 blocks Task 2, Task 2 blocks Task 3
        await self.task_update(
            task_id=task1_id,
            add_blocks=[task2_id],
            _agent_state=self.agent_state,
        )
        await self.task_update(
            task_id=task2_id,
            add_blocks=[task3_id],
            _agent_state=self.agent_state,
        )

        # Verify dependencies are set up correctly
        tasks_before = [
            task.model_dump() for task in self.agent_state.tasks_context.tasks
        ]
        self.assertEqual(tasks_before[0]["blocks"], [task2_id])
        self.assertEqual(tasks_before[1]["blocked_by"], [task1_id])
        self.assertEqual(tasks_before[1]["blocks"], [task3_id])
        self.assertEqual(tasks_before[2]["blocked_by"], [task2_id])

        # Delete task 2 (which is in the middle of the dependency chain)
        result = await self.task_update(
            task_id=task2_id,
            status="deleted",
            _agent_state=self.agent_state,
        )

        # Check result using model_dump
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": f"Task (id={task2_id}) has been deleted.",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "running",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)

        # Check task was removed and task2_id was removed from
        # blocks/blocked_by
        self.assertEqual(len(self.agent_state.tasks_context.tasks), 2)

        tasks_after = [
            task.model_dump() for task in self.agent_state.tasks_context.tasks
        ]
        # Task 1 should no longer have task2_id in blocks
        self.assertEqual(tasks_after[0]["id"], task1_id)
        self.assertEqual(tasks_after[0]["blocks"], [])

        # Task 3 should no longer have task2_id in blocked_by
        self.assertEqual(tasks_after[1]["id"], task3_id)
        self.assertEqual(tasks_after[1]["blocked_by"], [])

    async def test_update_nonexistent_task(self) -> None:
        """Test updating a task that doesn't exist."""
        result = await self.task_update(
            task_id="nonexistent-id",
            subject="New Subject",
            description=None,
            _agent_state=self.agent_state,
        )

        # Check result using model_dump
        result_dump = result.model_dump(mode="json")
        expected_result = {
            "content": [
                {
                    "text": "TaskNotFoundError: "
                    "The task (id=nonexistent-id) does not exist.",
                    "type": "text",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "finished_at": None,
                },
            ],
            "state": "error",
            "is_last": True,
            "metadata": {},
            "id": AnyString(),
        }
        self.assertDictEqual(result_dump, expected_result)


class TestTaskIdNotReused(IsolatedAsyncioTestCase):
    """Numeric task ids must not be reused after deletion."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.agent_state = AgentState()
        self.task_create = TaskCreate()
        self.task_update = TaskUpdate()
        self.task_get = TaskGet()

    async def _create(self, subject: str) -> str:
        """Create a task and return its id."""
        await self.task_create(
            subject=subject,
            description=subject,
            _agent_state=self.agent_state,
        )
        return self.agent_state.tasks_context.tasks[-1].id

    async def test_delete_highest_id_is_not_reused(self) -> None:
        """Deleting the highest id must not free that id for a new task."""
        deleted_id = await self._create("Original task")
        await self.task_update(
            task_id=deleted_id,
            status="deleted",
            _agent_state=self.agent_state,
        )
        new_id = await self._create("Unrelated new task")

        self.assertEqual(deleted_id, "1")
        self.assertEqual(new_id, "2")
        self.assertEqual(len(self.agent_state.tasks_context.tasks), 1)
        replacement = self.agent_state.tasks_context.tasks[0]
        self.assertEqual(replacement.id, new_id)
        self.assertEqual(replacement.subject, "Unrelated new task")
        self.assertEqual(replacement.state, "pending")

    async def test_delete_every_task_does_not_restart_ids(self) -> None:
        """Deleting every task must not restart allocation at 1."""
        created = [await self._create(f"Task {i}") for i in range(1, 4)]
        for task_id in created:
            await self.task_update(
                task_id=task_id,
                status="deleted",
                _agent_state=self.agent_state,
            )

        self.assertEqual(self.agent_state.tasks_context.tasks, [])
        new_id = await self._create("After wipe")
        self.assertEqual(new_id, "4")

    async def test_high_water_survives_serialization(self) -> None:
        """The allocation mark is restored with the task context."""
        deleted_id = await self._create("Original task")
        await self.task_update(
            task_id=deleted_id,
            status="deleted",
            _agent_state=self.agent_state,
        )
        payload = self.agent_state.model_dump(mode="json")
        mark = payload["tasks_context"].get("task_id_high_water") or 0
        self.assertGreaterEqual(mark, 1)

        self.agent_state = AgentState.model_validate(payload)
        new_id = await self._create("After restore")
        self.assertEqual(new_id, "2")
        self.assertNotEqual(new_id, deleted_id)

    async def test_nonnumeric_ids_do_not_reset_allocation(self) -> None:
        """Legacy non-numeric ids are ignored and do not rewind the mark."""
        deleted_id = await self._create("Numeric")
        self.agent_state.tasks_context.tasks.append(
            Task(
                id="legacy-uuid",
                subject="Legacy",
                description="Non-numeric id",
                metadata={},
            ),
        )
        await self.task_update(
            task_id=deleted_id,
            status="deleted",
            _agent_state=self.agent_state,
        )

        new_id = await self._create("Next")
        ids = [task.id for task in self.agent_state.tasks_context.tasks]
        self.assertEqual(new_id, "2")
        self.assertIn("legacy-uuid", ids)
        self.assertNotIn(deleted_id, ids)

    async def test_stale_update_and_get_miss_deleted_id(self) -> None:
        """A stale id must not read or modify the task created after it."""
        deleted_id = await self._create("Original task")
        await self.task_update(
            task_id=deleted_id,
            status="deleted",
            _agent_state=self.agent_state,
        )
        new_id = await self._create("Unrelated new task")
        self.assertNotEqual(new_id, deleted_id)

        update_result = await self.task_update(
            task_id=deleted_id,
            status="completed",
            _agent_state=self.agent_state,
        )
        self.assertEqual(
            update_result.content[0].text,
            f"TaskNotFoundError: The task (id={deleted_id}) does not exist.",
        )
        self.assertEqual(update_result.state, "error")

        replacement = self.agent_state.tasks_context.tasks[0]
        self.assertEqual(replacement.id, new_id)
        self.assertEqual(replacement.subject, "Unrelated new task")
        self.assertEqual(replacement.state, "pending")

        get_result = await self.task_get(
            task_id=deleted_id,
            _agent_state=self.agent_state,
        )
        self.assertEqual(get_result.content[0].text, "Task not found")
        self.assertEqual(get_result.state, "error")

    async def test_legacy_state_without_mark_uses_surviving_ids(
        self,
    ) -> None:
        """Saves from before the mark continue from ids that are still stored.

        An id deleted before the mark existed is not in the saved state, so
        it cannot be recovered. Surviving numeric ids still advance the
        sequence.
        """
        await self._create("Keep")
        await self._create("Also keep")
        payload = self.agent_state.model_dump(mode="json")
        payload["tasks_context"].pop("task_id_high_water", None)

        self.agent_state = AgentState.model_validate(payload)
        self.assertGreaterEqual(
            self.agent_state.tasks_context.task_id_high_water,
            2,
        )
        self.assertEqual(await self._create("Next"), "3")

    async def test_legacy_state_may_reuse_ids_deleted_before_the_mark(
        self,
    ) -> None:
        """A pre-mark save has no record of ids that were already deleted."""
        await self._create("Survivor")
        await self._create("Already gone in an older save")
        payload = self.agent_state.model_dump(mode="json")
        payload["tasks_context"].pop("task_id_high_water", None)
        payload["tasks_context"]["tasks"] = [
            task
            for task in payload["tasks_context"]["tasks"]
            if task["subject"] == "Survivor"
        ]

        self.agent_state = AgentState.model_validate(payload)
        self.assertEqual(await self._create("May reuse"), "2")
