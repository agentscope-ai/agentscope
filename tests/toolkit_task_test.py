# -*- coding: utf-8 -*-
"""Unit tests for task tools executed through toolkit."""
import json
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.message import ToolCallBlock
from agentscope.state import AgentState
from agentscope.tool import (
    Toolkit,
    ToolResponse,
    TaskCreate,
    TaskGet,
    TaskList,
    TaskUpdate,
)


class _ToolkitTaskTestBase(IsolatedAsyncioTestCase):
    """Shared helpers for toolkit task tool tests."""

    async def asyncSetUp(self) -> None:
        """Set up shared test fixtures."""
        self.maxDiff = None
        self.agent_state = AgentState()
        self.toolkit = Toolkit(
            tools=[
                TaskCreate(),
                TaskList(),
                TaskGet(),
                TaskUpdate(),
            ],
        )

    async def _call_tool(
        self,
        name: str,
        tool_input: dict[str, Any],
        tool_call_id: str,
    ) -> ToolResponse:
        """Call a task tool through toolkit and return the final response."""
        response = None
        async for result in self.toolkit.call_tool(
            ToolCallBlock(
                id=tool_call_id,
                name=name,
                input=json.dumps(tool_input),
            ),
            self.agent_state,
        ):
            if isinstance(result, ToolResponse):
                response = result

        self.assertIsNotNone(response)
        return response

    def _dump_tasks(self) -> list[dict[str, Any]]:
        """Dump all tasks from the agent state for assertions."""
        return [
            task.model_dump() for task in self.agent_state.tasks_context.tasks
        ]


class TestToolkitTaskCreate(_ToolkitTaskTestBase):
    """Test cases for TaskCreate through toolkit."""

    async def test_create_single_task(self) -> None:
        """Test creating a single task."""
        response = await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Test Task 1",
                "description": "This is a test task",
            },
            tool_call_id="task-create-single",
        )

        task_id = self.agent_state.tasks_context.tasks[0].id
        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": f"Task {task_id} created successfully: "
                        "Test Task 1",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-create-single",
            },
        )

        self.assertEqual(
            self._dump_tasks(),
            [
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
            ],
        )

    async def test_create_multiple_tasks(self) -> None:
        """Test creating multiple tasks."""
        response_1 = await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Task 1",
                "description": "First task",
            },
            tool_call_id="task-create-1",
        )
        task_1_id = self.agent_state.tasks_context.tasks[0].id
        self.assertDictEqual(
            response_1.model_dump(),
            {
                "content": [
                    {
                        "text": f"Task {task_1_id} created successfully: "
                        f"Task 1",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-create-1",
            },
        )

        response_2 = await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Task 2",
                "description": "Second task",
            },
            tool_call_id="task-create-2",
        )
        task_2_id = self.agent_state.tasks_context.tasks[1].id
        self.assertDictEqual(
            response_2.model_dump(),
            {
                "content": [
                    {
                        "text": f"Task {task_2_id} created successfully: "
                        f"Task 2",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-create-2",
            },
        )

        response_3 = await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Task 3",
                "description": "Third task",
                "metadata": {"priority": "high"},
            },
            tool_call_id="task-create-3",
        )
        task_3_id = self.agent_state.tasks_context.tasks[2].id
        self.assertDictEqual(
            response_3.model_dump(),
            {
                "content": [
                    {
                        "text": f"Task {task_3_id} created successfully: "
                        f"Task 3",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-create-3",
            },
        )

        self.assertEqual(
            self._dump_tasks(),
            [
                {
                    "subject": "Task 1",
                    "description": "First task",
                    "metadata": {},
                    "created_at": AnyString(),
                    "state": "pending",
                    "id": task_1_id,
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
                    "id": task_2_id,
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
                    "id": task_3_id,
                    "owner": None,
                    "blocks": [],
                    "blocked_by": [],
                },
            ],
        )

    async def test_create_task_with_metadata(self) -> None:
        """Test creating a task with metadata."""
        response = await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Bug Fix",
                "description": "Fix critical bug",
                "metadata": {
                    "priority": "high",
                    "tags": ["urgent", "bug"],
                },
            },
            tool_call_id="task-create-metadata",
        )

        task_id = self.agent_state.tasks_context.tasks[0].id
        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": f"Task {task_id} created successfully: "
                        f"Bug Fix",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-create-metadata",
            },
        )

        self.assertEqual(
            self._dump_tasks(),
            [
                {
                    "subject": "Bug Fix",
                    "description": "Fix critical bug",
                    "metadata": {
                        "priority": "high",
                        "tags": ["urgent", "bug"],
                    },
                    "created_at": AnyString(),
                    "state": "pending",
                    "id": task_id,
                    "owner": None,
                    "blocks": [],
                    "blocked_by": [],
                },
            ],
        )


class TestToolkitTaskList(_ToolkitTaskTestBase):
    """Test cases for TaskList through toolkit."""

    async def test_list_no_tasks(self) -> None:
        """Test listing when there are no tasks."""
        response = await self._call_tool(
            name="TaskList",
            tool_input={},
            tool_call_id="task-list-empty",
        )

        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": "No tasks available.",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-list-empty",
            },
        )
        self.assertEqual(self._dump_tasks(), [])

    async def test_list_with_tasks(self) -> None:
        """Test listing when there are tasks."""
        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Task 1",
                "description": "First task",
            },
            tool_call_id="task-list-create-1",
        )
        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Task 2",
                "description": "Second task",
            },
            tool_call_id="task-list-create-2",
        )
        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Task 3",
                "description": "Third task",
            },
            tool_call_id="task-list-create-3",
        )

        task_1_id = self.agent_state.tasks_context.tasks[0].id
        task_2_id = self.agent_state.tasks_context.tasks[1].id
        task_3_id = self.agent_state.tasks_context.tasks[2].id

        response = await self._call_tool(
            name="TaskList",
            tool_input={},
            tool_call_id="task-list-with-tasks",
        )

        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": f"#{task_1_id} [pending] Task 1\n"
                        f"#{task_2_id} [pending] Task 2\n"
                        f"#{task_3_id} [pending] Task 3",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-list-with-tasks",
            },
        )

        self.assertEqual(
            self._dump_tasks(),
            [
                {
                    "subject": "Task 1",
                    "description": "First task",
                    "metadata": {},
                    "created_at": AnyString(),
                    "state": "pending",
                    "id": task_1_id,
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
                    "id": task_2_id,
                    "owner": None,
                    "blocks": [],
                    "blocked_by": [],
                },
                {
                    "subject": "Task 3",
                    "description": "Third task",
                    "metadata": {},
                    "created_at": AnyString(),
                    "state": "pending",
                    "id": task_3_id,
                    "owner": None,
                    "blocks": [],
                    "blocked_by": [],
                },
            ],
        )


class TestToolkitTaskGet(_ToolkitTaskTestBase):
    """Test cases for TaskGet through toolkit."""

    async def test_get_existing_task(self) -> None:
        """Test getting an existing task."""
        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Test Task",
                "description": "This is a test task with details",
                "metadata": {"priority": "high"},
            },
            tool_call_id="task-get-create",
        )

        task_id = self.agent_state.tasks_context.tasks[0].id
        response = await self._call_tool(
            name="TaskGet",
            tool_input={"task_id": task_id},
            tool_call_id="task-get-existing",
        )

        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": f"Task #{task_id}: Test Task\n"
                        "Status: pending\n"
                        "Description: This is a test task with details\n"
                        "Metadata: {'priority': 'high'}",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-get-existing",
            },
        )

        self.assertEqual(
            self._dump_tasks(),
            [
                {
                    "subject": "Test Task",
                    "description": "This is a test task with details",
                    "metadata": {"priority": "high"},
                    "created_at": AnyString(),
                    "state": "pending",
                    "id": task_id,
                    "owner": None,
                    "blocks": [],
                    "blocked_by": [],
                },
            ],
        )

    async def test_get_nonexistent_task(self) -> None:
        """Test getting a task that does not exist."""
        response = await self._call_tool(
            name="TaskGet",
            tool_input={"task_id": "nonexistent-id"},
            tool_call_id="task-get-missing",
        )

        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": "Task not found",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "error",
                "metadata": {},
                "id": "task-get-missing",
            },
        )
        self.assertEqual(self._dump_tasks(), [])


class TestToolkitTaskUpdate(_ToolkitTaskTestBase):
    """Test cases for TaskUpdate through toolkit."""

    async def test_update_subject(self) -> None:
        """Test updating task subject."""
        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Original Subject",
                "description": "Test description",
            },
            tool_call_id="task-update-subject-create",
        )
        task_id = self.agent_state.tasks_context.tasks[0].id

        response = await self._call_tool(
            name="TaskUpdate",
            tool_input={
                "task_id": task_id,
                "subject": "Updated Subject",
            },
            tool_call_id="task-update-subject",
        )

        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": f"Update task #{task_id} subject.",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-update-subject",
            },
        )

        self.assertEqual(
            self._dump_tasks(),
            [
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
            ],
        )

    async def test_update_description(self) -> None:
        """Test updating task description."""
        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Test Task",
                "description": "Original description",
            },
            tool_call_id="task-update-description-create",
        )
        task_id = self.agent_state.tasks_context.tasks[0].id

        response = await self._call_tool(
            name="TaskUpdate",
            tool_input={
                "task_id": task_id,
                "description": "Updated description",
            },
            tool_call_id="task-update-description",
        )

        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": f"Update task #{task_id} description.",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-update-description",
            },
        )

        self.assertEqual(
            self._dump_tasks(),
            [
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
            ],
        )

    async def test_update_status(self) -> None:
        """Test updating task status."""
        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Test Task",
                "description": "Test description",
            },
            tool_call_id="task-update-status-create",
        )
        task_id = self.agent_state.tasks_context.tasks[0].id

        response = await self._call_tool(
            name="TaskUpdate",
            tool_input={
                "task_id": task_id,
                "status": "in_progress",
            },
            tool_call_id="task-update-status-in-progress",
        )
        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": f"Update task #{task_id} status.",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-update-status-in-progress",
            },
        )
        self.assertEqual(
            self._dump_tasks(),
            [
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
            ],
        )

        response = await self._call_tool(
            name="TaskUpdate",
            tool_input={
                "task_id": task_id,
                "status": "completed",
            },
            tool_call_id="task-update-status-completed",
        )
        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": f"Update task #{task_id} status.\n\n"
                        "Task completed. Call TaskList now to find your "
                        "next available task or see if your work "
                        "unblocked others.",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-update-status-completed",
            },
        )
        self.assertEqual(
            self._dump_tasks(),
            [
                {
                    "subject": "Test Task",
                    "description": "Test description",
                    "metadata": {},
                    "created_at": AnyString(),
                    "state": "completed",
                    "id": task_id,
                    "owner": None,
                    "blocks": [],
                    "blocked_by": [],
                },
            ],
        )

        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Task to Delete",
                "description": "This task will be deleted",
            },
            tool_call_id="task-update-status-create-delete",
        )
        task_to_delete_id = self.agent_state.tasks_context.tasks[1].id

        response = await self._call_tool(
            name="TaskUpdate",
            tool_input={
                "task_id": task_to_delete_id,
                "status": "deleted",
            },
            tool_call_id="task-update-status-deleted",
        )
        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": f"Task {task_to_delete_id} has been deleted.",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-update-status-deleted",
            },
        )
        self.assertEqual(
            self._dump_tasks(),
            [
                {
                    "subject": "Test Task",
                    "description": "Test description",
                    "metadata": {},
                    "created_at": AnyString(),
                    "state": "completed",
                    "id": task_id,
                    "owner": None,
                    "blocks": [],
                    "blocked_by": [],
                },
            ],
        )

    async def test_update_owner(self) -> None:
        """Test updating task owner."""
        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Test Task",
                "description": "Test description",
            },
            tool_call_id="task-update-owner-create",
        )
        task_id = self.agent_state.tasks_context.tasks[0].id

        response = await self._call_tool(
            name="TaskUpdate",
            tool_input={
                "task_id": task_id,
                "owner": "agent-1",
            },
            tool_call_id="task-update-owner",
        )

        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": f"Update task #{task_id} owner.",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-update-owner",
            },
        )

        self.assertEqual(
            self._dump_tasks(),
            [
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
            ],
        )

    async def test_update_metadata(self) -> None:
        """Test updating task metadata."""
        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Test Task",
                "description": "Test description",
                "metadata": {
                    "priority": "low",
                    "tags": ["test"],
                },
            },
            tool_call_id="task-update-metadata-create",
        )
        task_id = self.agent_state.tasks_context.tasks[0].id

        response = await self._call_tool(
            name="TaskUpdate",
            tool_input={
                "task_id": task_id,
                "metadata": {
                    "priority": "high",
                    "new_field": "value",
                },
            },
            tool_call_id="task-update-metadata",
        )

        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": f"Update task #{task_id} metadata.",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-update-metadata",
            },
        )

        self.assertEqual(
            self._dump_tasks(),
            [
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
            ],
        )

    async def test_update_add_blocks(self) -> None:
        """Test updating task blocks relationship."""
        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Task 1",
                "description": "First task",
            },
            tool_call_id="task-update-add-blocks-create-1",
        )
        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Task 2",
                "description": "Second task",
            },
            tool_call_id="task-update-add-blocks-create-2",
        )

        task_1_id = self.agent_state.tasks_context.tasks[0].id
        task_2_id = self.agent_state.tasks_context.tasks[1].id
        response = await self._call_tool(
            name="TaskUpdate",
            tool_input={
                "task_id": task_1_id,
                "add_blocks": [task_2_id],
            },
            tool_call_id="task-update-add-blocks",
        )

        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": f"Update task #{task_1_id} add_blocks.",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-update-add-blocks",
            },
        )

        self.assertEqual(
            self._dump_tasks(),
            [
                {
                    "subject": "Task 1",
                    "description": "First task",
                    "metadata": {},
                    "created_at": AnyString(),
                    "state": "pending",
                    "id": task_1_id,
                    "owner": None,
                    "blocks": [task_2_id],
                    "blocked_by": [],
                },
                {
                    "subject": "Task 2",
                    "description": "Second task",
                    "metadata": {},
                    "created_at": AnyString(),
                    "state": "pending",
                    "id": task_2_id,
                    "owner": None,
                    "blocks": [],
                    "blocked_by": [task_1_id],
                },
            ],
        )

    async def test_update_add_blocked_by(self) -> None:
        """Test updating task blocked_by relationship."""
        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Task 1",
                "description": "First task",
            },
            tool_call_id="task-update-add-blocked-by-create-1",
        )
        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Task 2",
                "description": "Second task",
            },
            tool_call_id="task-update-add-blocked-by-create-2",
        )

        task_1_id = self.agent_state.tasks_context.tasks[0].id
        task_2_id = self.agent_state.tasks_context.tasks[1].id
        response = await self._call_tool(
            name="TaskUpdate",
            tool_input={
                "task_id": task_2_id,
                "add_blocked_by": [task_1_id],
            },
            tool_call_id="task-update-add-blocked-by",
        )

        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": f"Update task #{task_2_id} add_blocked_by.",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-update-add-blocked-by",
            },
        )

        self.assertEqual(
            self._dump_tasks(),
            [
                {
                    "subject": "Task 1",
                    "description": "First task",
                    "metadata": {},
                    "created_at": AnyString(),
                    "state": "pending",
                    "id": task_1_id,
                    "owner": None,
                    "blocks": [task_2_id],
                    "blocked_by": [],
                },
                {
                    "subject": "Task 2",
                    "description": "Second task",
                    "metadata": {},
                    "created_at": AnyString(),
                    "state": "pending",
                    "id": task_2_id,
                    "owner": None,
                    "blocks": [],
                    "blocked_by": [task_1_id],
                },
            ],
        )

    async def test_update_delete_task(self) -> None:
        """Test deleting a task and cleaning dependency relations."""
        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Task 1",
                "description": "First task",
            },
            tool_call_id="task-update-delete-create-1",
        )
        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Task 2",
                "description": "Second task",
            },
            tool_call_id="task-update-delete-create-2",
        )
        await self._call_tool(
            name="TaskCreate",
            tool_input={
                "subject": "Task 3",
                "description": "Third task",
            },
            tool_call_id="task-update-delete-create-3",
        )

        task_1_id = self.agent_state.tasks_context.tasks[0].id
        task_2_id = self.agent_state.tasks_context.tasks[1].id
        task_3_id = self.agent_state.tasks_context.tasks[2].id

        await self._call_tool(
            name="TaskUpdate",
            tool_input={
                "task_id": task_1_id,
                "add_blocks": [task_2_id],
            },
            tool_call_id="task-update-delete-link-1",
        )
        await self._call_tool(
            name="TaskUpdate",
            tool_input={
                "task_id": task_2_id,
                "add_blocks": [task_3_id],
            },
            tool_call_id="task-update-delete-link-2",
        )

        self.assertEqual(
            self._dump_tasks(),
            [
                {
                    "subject": "Task 1",
                    "description": "First task",
                    "metadata": {},
                    "created_at": AnyString(),
                    "state": "pending",
                    "id": task_1_id,
                    "owner": None,
                    "blocks": [task_2_id],
                    "blocked_by": [],
                },
                {
                    "subject": "Task 2",
                    "description": "Second task",
                    "metadata": {},
                    "created_at": AnyString(),
                    "state": "pending",
                    "id": task_2_id,
                    "owner": None,
                    "blocks": [task_3_id],
                    "blocked_by": [task_1_id],
                },
                {
                    "subject": "Task 3",
                    "description": "Third task",
                    "metadata": {},
                    "created_at": AnyString(),
                    "state": "pending",
                    "id": task_3_id,
                    "owner": None,
                    "blocks": [],
                    "blocked_by": [task_2_id],
                },
            ],
        )

        response = await self._call_tool(
            name="TaskUpdate",
            tool_input={
                "task_id": task_2_id,
                "status": "deleted",
            },
            tool_call_id="task-update-delete",
        )

        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": f"Task {task_2_id} has been deleted.",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "success",
                "metadata": {},
                "id": "task-update-delete",
            },
        )
        self.assertEqual(
            self._dump_tasks(),
            [
                {
                    "subject": "Task 1",
                    "description": "First task",
                    "metadata": {},
                    "created_at": AnyString(),
                    "state": "pending",
                    "id": task_1_id,
                    "owner": None,
                    "blocks": [],
                    "blocked_by": [],
                },
                {
                    "subject": "Task 3",
                    "description": "Third task",
                    "metadata": {},
                    "created_at": AnyString(),
                    "state": "pending",
                    "id": task_3_id,
                    "owner": None,
                    "blocks": [],
                    "blocked_by": [],
                },
            ],
        )

    async def test_update_nonexistent_task(self) -> None:
        """Test updating a task that does not exist."""
        response = await self._call_tool(
            name="TaskUpdate",
            tool_input={
                "task_id": "nonexistent-id",
                "subject": "New Subject",
                "description": None,
            },
            tool_call_id="task-update-missing",
        )

        self.assertDictEqual(
            response.model_dump(),
            {
                "content": [
                    {
                        "text": "TaskNotFoundError: "
                        "The task nonexistent-id does not exist.",
                        "type": "text",
                        "id": AnyString(),
                    },
                ],
                "state": "error",
                "metadata": {},
                "id": "task-update-missing",
            },
        )
        self.assertEqual(self._dump_tasks(), [])
