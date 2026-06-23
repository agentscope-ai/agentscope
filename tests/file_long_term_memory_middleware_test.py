# -*- coding: utf-8 -*-
# pylint: disable=protected-access,unused-argument,consider-using-with
"""Unit tests for ``FileLongTermMemoryMiddleware`` and its Markdown store.

The tests use real temporary ``LocalWorkspace`` directories so they exercise
the same ``BackendBase`` path used in production, while chat-model behavior is
kept deterministic with ``MockModel``. Coverage is split across:

- constrained workspace-relative file access;
- Markdown layout, section-aware mutations, and lexical search;
- tool exposure and state-injected tool routing;
- periodic structured extraction and context-compaction behavior;
- workspace isolation and automatic LocalWorkspace fallback.

``protected-access`` is disabled because integration tests intentionally
inspect workspace-keyed store registries to verify isolation and persistence
boundaries that are not exposed as public application APIs.
"""
import os
import tempfile
from typing import Any
from datetime import datetime
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent
from agentscope.message import Msg, TextBlock, UserMsg
from agentscope.middleware import FileLongTermMemoryMiddleware
from agentscope.middleware._longterm_memory._fileLongTermMemory import (
    _accessor,
    _store,
)
from agentscope.model import ChatResponse, StructuredResponse
from agentscope.state import AgentState
from agentscope.tool import Toolkit
from agentscope.workspace import LocalWorkspace


WorkspaceFileAccessor = _accessor.WorkspaceFileAccessor
FileLTMStore = _store.FileLTMStore


# ----------------------------------------------------------------------
# Test helpers
# ----------------------------------------------------------------------


class _ExtractionModel(MockModel):
    """Mock chat model returning one deterministic structured extraction."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the model and its extraction-call counter."""
        super().__init__(*args, **kwargs)
        self.extraction_calls = 0
        self.extraction_messages: list[Msg] = []

    async def generate_structured_output(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> StructuredResponse:
        """Return edits spanning daily, USER, and MEMORY layers."""
        self.extraction_calls += 1
        self.extraction_messages = list(kwargs.get("messages", []))
        return StructuredResponse(
            content={
                "daily": {
                    "add": [
                        {
                            "section": "Work Log",
                            "content": "Implemented the file LTM.",
                            "create_section": True,
                        },
                        {
                            "section": "Next Steps",
                            "content": "Add integration documentation.",
                            "create_section": True,
                        },
                    ],
                    "replace": [
                        {
                            "old_text": "Initial notebook context.",
                            "new_text": "Refined notebook context.",
                        },
                    ],
                    "remove": [],
                },
                "user": {
                    "add": [
                        {
                            "section": "Communication Style",
                            "content": (
                                "The user prefers concise Chinese answers."
                            ),
                        },
                    ],
                    "replace": [],
                    "remove": [],
                },
                "memory": {
                    "add": [
                        {
                            "section": "Project Knowledge",
                            "content": (
                                "This workspace uses a file-backed LTM."
                            ),
                        },
                    ],
                    "replace": [],
                    "remove": [],
                },
            },
        )


# ----------------------------------------------------------------------
# Workspace accessor and Markdown store
# ----------------------------------------------------------------------


class TestFileLongTermMemoryStore(IsolatedAsyncioTestCase):
    """Tests for backend access and constrained Markdown persistence."""

    async def asyncSetUp(self) -> None:
        """Create an initialized temporary workspace and empty LTM store."""
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = LocalWorkspace(workdir=self.temp.name)
        await self.workspace.initialize()
        self.store = FileLTMStore(
            WorkspaceFileAccessor(self.workspace),
        )
        await self.store.ensure_layout()

    async def asyncTearDown(self) -> None:
        """Close the workspace and remove its temporary directory."""
        await self.workspace.close()
        self.temp.cleanup()

    async def test_layout_and_constrained_updates(self) -> None:
        """Layout and section-aware edits should be safe and deterministic."""
        snapshot = await self.store.read_snapshot()
        self.assertIn("# Long-term Memory", snapshot.memory)
        self.assertIn("# User Profile", snapshot.user)

        await self.store.update_target(
            action="add",
            target="memory",
            content="The project uses PostgreSQL.",
            section="Project Knowledge",
        )
        await self.store.update_target(
            action="replace",
            target="memory",
            old_text="PostgreSQL",
            content="PostgreSQL 16",
        )
        updated = await self.store.read_target("memory")
        self.assertIn("PostgreSQL 16", updated)
        self.assertLess(
            updated.index("PostgreSQL 16"),
            updated.index("## Decisions"),
        )

        with self.assertRaises(ValueError):
            await self.store.update_target(
                action="remove",
                target="memory",
                old_text="does not exist",
            )

        with self.assertRaisesRegex(ValueError, "Available sections"):
            await self.store.update_target(
                action="add",
                target="memory",
                content="This must not be silently misplaced.",
                section="Unknown Section",
            )

        await self.store.update_target(
            action="add",
            target="memory",
            content="A custom architectural constraint.",
            section="Architecture Constraints",
            create_section=True,
        )
        updated = await self.store.read_target("memory")
        self.assertIn("## Architecture Constraints", updated)
        self.assertIn("- A custom architectural constraint.", updated)
        _, sections = await self.store.read_target_with_sections("memory")
        self.assertIn("Architecture Constraints", sections)

        with self.assertRaisesRegex(ValueError, "plain heading"):
            await self.store.update_target(
                action="add",
                target="memory",
                content="Invalid heading content.",
                section="Injected\n## Heading",
                create_section=True,
            )

        with self.assertRaisesRegex(ValueError, "only valid for action=add"):
            await self.store.update_target(
                action="replace",
                target="memory",
                old_text="PostgreSQL 16",
                content="PostgreSQL 17",
                section="Project Knowledge",
                create_section=True,
            )

    async def test_daily_memory_and_lightweight_search(self) -> None:
        """Daily entries should be retrievable by lexical phrase matching."""
        now = datetime.fromisoformat("2026-06-21T10:30:00+08:00")
        await self.store.update_target(
            action="add",
            target="daily",
            content="Finished the authentication data model.",
            section="Progress",
            create_section=True,
            daily_date=now.date().isoformat(),
        )
        await self.store.update_target(
            action="add",
            target="daily",
            content="JWT access tokens expire in 15 minutes.",
            section="Decisions",
            create_section=True,
            daily_date=now.date().isoformat(),
        )

        results = await self.store.search(
            "JWT 15 minutes",
            days=30,
            limit=5,
            today=now.date(),
        )
        self.assertTrue(results)
        self.assertEqual(results[0].source, "memory/2026-06-21.md")
        self.assertIn("JWT", results[0].content)

    async def test_accessor_uses_workspace_backend(self) -> None:
        """Accessor operations should round-trip through Workspace backend."""
        accessor = WorkspaceFileAccessor(self.workspace)
        await accessor.write_text(
            "Memory/memory/2026-06-21.md",
            "# daily\n",
        )
        self.assertTrue(
            await accessor.exists("Memory/memory/2026-06-21.md"),
        )
        self.assertEqual(
            await accessor.read_text("Memory/memory/2026-06-21.md"),
            "# daily\n",
        )
        self.assertEqual(
            await accessor.list_files("Memory/memory", ".md"),
            ["2026-06-21.md"],
        )

    async def test_accessor_rejects_absolute_and_parent_paths(self) -> None:
        """Absolute and parent paths must stay outside the backend."""
        accessor = WorkspaceFileAccessor(self.workspace)
        with self.assertRaises(ValueError):
            await accessor.read_text("../MEMORY.md")
        with self.assertRaises(ValueError):
            await accessor.read_text("C:\\MEMORY.md")


# ----------------------------------------------------------------------
# Middleware hooks, tools, and workspace routing
# ----------------------------------------------------------------------


class TestFileLongTermMemoryMiddleware(IsolatedAsyncioTestCase):
    """Tests for Agent hooks, tools, extraction, and workspace isolation."""

    async def asyncSetUp(self) -> None:
        """Create the LocalWorkspace used by most integration tests."""
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = LocalWorkspace(workdir=self.temp.name)
        await self.workspace.initialize()

    async def asyncTearDown(self) -> None:
        """Close and remove the explicit test workspace."""
        await self.workspace.close()
        self.temp.cleanup()

    async def test_modes_expose_expected_tools(self) -> None:
        """Only auto-capable modes should expose the write tool."""
        static = FileLongTermMemoryMiddleware(
            workspace=self.workspace,
            mode="static",
        )
        auto = FileLongTermMemoryMiddleware(
            workspace=self.workspace,
            mode="auto",
        )
        static_names = [tool.name for tool in await static.list_tools()]
        auto_names = [tool.name for tool in await auto.list_tools()]
        self.assertEqual(static_names, ["memory_read", "memory_search"])
        self.assertIn("memory_manage", auto_names)

    async def test_prompt_and_manage_tool_use_workspace_files(self) -> None:
        """Prompt snapshots and managed writes should share one store."""
        middleware = FileLongTermMemoryMiddleware(
            workspace=self.workspace,
            mode="auto",
        )
        state = AgentState(session_id="session-tools")
        model = MockModel(context_size=100_000)
        agent = Agent(
            name="assistant",
            system_prompt="base",
            model=model,
            state=state,
            middlewares=[middleware],
            offloader=self.workspace,
        )
        await middleware.on_system_prompt(agent, "base")
        manage = next(
            tool
            for tool in await middleware.list_tools()
            if tool.name == "memory_manage"
        )
        read = next(
            tool
            for tool in await middleware.list_tools()
            if tool.name == "memory_read"
        )
        read_result = await read(
            target="user",
            _agent_state=state,
        )
        self.assertIn(
            "Available ## sections: Identity And Background, Preferences",
            read_result.content[0].text,
        )
        result = await manage(
            action="add",
            target="user",
            thinking="This is a stable preference.",
            content="The user prefers Chinese responses.",
            section="Preferences",
            _agent_state=state,
        )
        self.assertIn("Memory updated", result.content[0].text)

        state.reply_id = "next-reply"
        prompt = await middleware.on_system_prompt(agent, "base")
        self.assertIn("The user prefers Chinese responses.", prompt)
        self.assertIn("MEMORY.md", prompt)

        created = await manage(
            action="add",
            target="user",
            thinking="No existing section describes accessibility needs.",
            content="The user requires screen-reader friendly output.",
            section="Accessibility Needs",
            create_section=True,
            _agent_state=state,
        )
        self.assertIn("Memory updated", created.content[0].text)
        user_memory = await middleware._stores[
            self.workspace.workspace_id
        ].read_target("user")
        self.assertIn("## Accessibility Needs", user_memory)

        daily_before = await read(
            target="daily",
            _agent_state=state,
        )
        self.assertIn(
            "Available ## sections: (none)",
            daily_before.content[0].text,
        )
        daily_created = await manage(
            action="add",
            target="daily",
            thinking="Today's notebook needs a section for active ideas.",
            content="Explore section-aware daily memory.",
            section="Ideas",
            create_section=True,
            _agent_state=state,
        )
        self.assertIn("Memory updated", daily_created.content[0].text)

        await read(target="daily", _agent_state=state)
        daily_rewritten = await manage(
            action="replace",
            target="daily",
            thinking="The idea is now a concrete implementation task.",
            old_text="Explore section-aware daily memory.",
            content="Implement section-aware daily memory.",
            _agent_state=state,
        )
        self.assertIn("Memory updated", daily_rewritten.content[0].text)
        daily = await middleware._stores[
            self.workspace.workspace_id
        ].read_target("daily")
        self.assertIn("## Ideas", daily)
        self.assertIn("Implement section-aware daily memory.", daily)

        state.reply_id = "daily-not-in-system-prompt"
        prompt = await middleware.on_system_prompt(agent, "base")
        self.assertNotIn("Implement section-aware daily memory.", prompt)

    async def test_shared_middleware_isolates_workspaces(self) -> None:
        """One middleware instance must not mix two workspace memories."""
        second_temp = tempfile.TemporaryDirectory()
        second_workspace = LocalWorkspace(workdir=second_temp.name)
        await second_workspace.initialize()
        try:
            middleware = FileLongTermMemoryMiddleware(mode="auto")
            first_state = AgentState(session_id="first-session")
            second_state = AgentState(session_id="second-session")
            first_agent = Agent(
                name="assistant",
                system_prompt="base",
                model=MockModel(context_size=100_000),
                state=first_state,
                middlewares=[middleware],
                offloader=self.workspace,
            )
            second_agent = Agent(
                name="assistant",
                system_prompt="base",
                model=MockModel(context_size=100_000),
                state=second_state,
                middlewares=[middleware],
                offloader=second_workspace,
            )
            await middleware.on_system_prompt(first_agent, "base")
            await middleware.on_system_prompt(second_agent, "base")
            manage = next(
                tool
                for tool in await middleware.list_tools()
                if tool.name == "memory_manage"
            )

            await manage(
                action="add",
                target="memory",
                thinking="First workspace fact.",
                content="Only the first workspace knows alpha.",
                section="Stable Facts",
                _agent_state=first_state,
            )
            await manage(
                action="add",
                target="memory",
                thinking="Second workspace fact.",
                content="Only the second workspace knows beta.",
                section="Stable Facts",
                _agent_state=second_state,
            )

            first_memory = await middleware._stores[
                self.workspace.workspace_id
            ].read_target("memory")
            second_memory = await middleware._stores[
                second_workspace.workspace_id
            ].read_target("memory")
            self.assertIn("alpha", first_memory)
            self.assertNotIn("beta", first_memory)
            self.assertIn("beta", second_memory)
            self.assertNotIn("alpha", second_memory)
        finally:
            await second_workspace.close()
            second_temp.cleanup()

    async def test_static_extraction_writes_all_memory_layers(self) -> None:
        """Static extraction should update daily, USER, and MEMORY files."""
        seed_store = FileLTMStore(WorkspaceFileAccessor(self.workspace))
        await seed_store.update_target(
            action="add",
            target="daily",
            content="Initial notebook context.",
            section="Scratchpad",
            create_section=True,
        )
        model = _ExtractionModel(context_size=100_000)
        model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="Done.")],
                    is_last=True,
                ),
            ],
        )
        middleware = FileLongTermMemoryMiddleware(
            mode="static",
            extraction_interval=1,
        )
        toolkit = Toolkit(tools=await middleware.list_tools())
        agent = Agent(
            name="assistant",
            system_prompt="base",
            model=model,
            toolkit=toolkit,
            middlewares=[middleware],
            offloader=self.workspace,
        )

        reply = await agent.reply(UserMsg("user", "Please implement LTM."))
        self.assertEqual(reply.get_text_content(), "Done.")
        extraction_input = model.extraction_messages[-1].get_text_content()
        self.assertIn("Today's daily memory", extraction_input)
        self.assertIn("Initial notebook context.", extraction_input)

        store = middleware._stores[self.workspace.workspace_id]
        self.assertIn(
            "concise Chinese",
            await store.read_target("user"),
        )
        self.assertIn(
            "file-backed LTM",
            await store.read_target("memory"),
        )
        daily = await store.read_target(
            "daily",
            daily_date=datetime.now().astimezone().date().isoformat(),
        )
        self.assertIn("Implemented the file LTM", daily)
        self.assertIn("## Work Log", daily)
        self.assertIn("Refined notebook context.", daily)

    async def test_compaction_hook_does_not_extract_below_threshold(
        self,
    ) -> None:
        """Compression hook should remain idle below its token threshold."""
        model = _ExtractionModel(context_size=100_000)
        model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="First reply.")],
                    is_last=True,
                ),
                ChatResponse(
                    content=[TextBlock(text="Second reply.")],
                    is_last=True,
                ),
            ],
        )
        middleware = FileLongTermMemoryMiddleware(
            mode="static",
            extraction_interval=8,
        )
        agent = Agent(
            name="assistant",
            system_prompt="base",
            model=model,
            middlewares=[middleware],
            offloader=self.workspace,
        )

        await agent.reply(UserMsg("user", "first"))
        await agent.reply(UserMsg("user", "second"))
        self.assertEqual(model.extraction_calls, 0)

    async def test_creates_local_workspace_when_none_is_supplied(self) -> None:
        """Missing workspace/offloader should create a local fallback."""
        model = MockModel(context_size=100_000)
        model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="Ready.")],
                    is_last=True,
                ),
            ],
        )
        fallback_root = os.path.join(self.temp.name, "fallback")
        middleware = FileLongTermMemoryMiddleware(
            mode="auto",
            fallback_workspace_root=fallback_root,
        )
        agent = Agent(
            name="coding assistant",
            system_prompt="base",
            model=model,
            middlewares=[middleware],
        )

        await agent.reply(UserMsg("user", "hello"))
        self.assertIsNone(agent.offloader)
        self.assertTrue(
            os.path.isfile(
                os.path.join(
                    fallback_root,
                    "coding-assistant",
                    "Memory",
                    "MEMORY.md",
                ),
            ),
        )
        self.assertEqual(len(middleware._owned_workspaces), 1)
        await middleware.close()
