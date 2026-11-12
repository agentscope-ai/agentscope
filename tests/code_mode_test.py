# -*- coding: utf-8 -*-
# pylint: disable=protected-access,too-many-public-methods
"""Code Mode integration tests using actual isolated Python processes."""

import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Awaitable, Callable
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import AsyncMock, Mock, call as mock_call, patch

import psutil
from pydantic import BaseModel

from utils import AnyString, MockModel
from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.app.middleware import ToolOffloadMiddleware
from agentscope.event import (
    ConfirmResult,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
)
from agentscope.code_mode import CodeModeConfig, CodeModeToolkit
from agentscope.code_mode._process import WorkerProcess
from agentscope.code_mode._script import Script, _CLEANING_TASKS
from agentscope.code_mode._toolkit import python_name
from agentscope.message import (
    Base64Source,
    DataBlock,
    TextBlock,
    ToolCallBlock,
    ToolResultState,
    URLSource,
    UserMsg,
)
from agentscope.middleware import MiddlewareBase, ReplyBudgetControlMiddleware
from agentscope.model import ChatResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionDecision,
    PermissionMode,
    PermissionRule,
)
from agentscope.skill import Skill
from agentscope.state import AgentState
from agentscope.tool import (
    BackendBase,
    FunctionTool,
    LocalBackend,
    ToolChunk,
    ToolChoice,
    ToolGroup,
    ToolResponse,
    Toolkit,
)
from agentscope.tool._execution_context import nested_tool_call
from agentscope.workspace import BubblewrapBackend, BubblewrapWorkspace


class Audit(MiddlewareBase):
    """Record permission and acting hooks without modifying results."""

    def __init__(self) -> None:
        self.permissions = []
        self.acting = []

    async def on_check_permission(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., Awaitable[PermissionDecision]],
    ) -> PermissionDecision:
        self.permissions.append(input_kwargs["tool_call"].name)
        return await next_handler(**input_kwargs)

    async def on_acting(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        self.acting.append(input_kwargs["tool_call"].name)
        async for item in next_handler(**input_kwargs):
            yield item


class Answer(BaseModel):
    """Small schema for the forced structured output path."""

    value: int


class CodeModeTest(IsolatedAsyncioTestCase):
    """Verify native tools, permissions and process lifetime end to end."""

    def setUp(self) -> None:
        # pylint: disable=consider-using-with
        self.directory = tempfile.TemporaryDirectory(prefix="code mode ")
        self.addCleanup(self.directory.cleanup)
        self.effects = []
        self.audit = Audit()
        self.native = Toolkit(tools=[self.make_tool("echo")])
        self.toolkit = CodeModeToolkit(
            self.native,
            backend=LocalBackend(),
            cwd=self.directory.name,
            auto_approve=True,
        )
        self.model = MockModel(stream=False, context_size=100000)
        self.agent = Agent(
            name="code",
            system_prompt="Test code orchestration.",
            model=self.model,
            toolkit=self.toolkit,
            middlewares=[self.audit],
            injection_config=InjectionConfig(inject_runtime_state=False),
        )

    def make_tool(
        self,
        name: str,
        permission: PermissionBehavior = PermissionBehavior.ALLOW,
        **kwargs: Any,
    ) -> FunctionTool:
        """Create a strict native function with an observable effect."""

        async def echo(value: int) -> ToolChunk:
            self.effects.append(value)
            return ToolChunk(
                content=[TextBlock(text=f"value={value}")],
                metadata={"large": "x" * 50000},
            )

        return FunctionTool(
            echo,
            name=name,
            permission=PermissionDecision(behavior=permission, message="test"),
            **kwargs,
        )

    async def run_code(self, code: str) -> ToolResponse:
        """Use the same permission and acting path as an Agent call."""
        return await self.agent.invoke_tool("run_code", {"code": code})

    def allow_test_subprocesses(self) -> None:
        """Simulate containment for process-I/O tests with owned children."""
        self.enterContext(
            patch.object(self.toolkit.backend, "isolates_process_tree", True),
        )

    @staticmethod
    def text(response: ToolResponse) -> str:
        """Collect selected text without discarding native media blocks."""
        return "".join(
            block.text
            for block in response.content
            if isinstance(block, TextBlock)
        )

    @staticmethod
    def normalized_blocks(blocks: list[TextBlock | DataBlock]) -> list[dict]:
        """Compare complete output blocks without generated IDs or times."""
        return [
            block.model_dump(exclude={"id", "created_at", "finished_at"})
            for block in blocks
        ]

    def code_response(self, code: str) -> ChatResponse:
        """Build one model-generated run_code action."""
        return ChatResponse(
            is_last=True,
            content=[
                ToolCallBlock(
                    id="script_call",
                    name="run_code",
                    input=json.dumps({"code": code}),
                ),
            ],
        )

    async def test_full_results_filtered_and_native_hooks(self) -> None:
        """Full intermediate data stays in Python; native hooks still run."""
        response = await self.run_code(
            "r = await tools.echo(value=7)\n"
            'text({"size": len(r["metadata"]["large"]), '
            '"state": r["state"]})',
        )
        self.assertEqual(
            json.loads(self.text(response)),
            {"size": 50000, "state": "success"},
        )
        self.assertEqual(self.effects, [7])
        self.assertEqual(self.audit.permissions, ["run_code", "echo"])
        self.assertEqual(self.audit.acting, ["run_code", "echo"])
        self.assertEqual(self.agent.state.context, [])
        self.assertEqual(
            response.metadata,
            {
                "code_mode": {
                    "calls": [
                        {
                            "name": "echo",
                            "state": "success",
                            "id": AnyString(),
                        },
                    ],
                    "truncated": False,
                    "error": None,
                },
            },
        )
        self.assertFalse(nested_tool_call.get())

    async def test_script_requires_authorization_and_respects_deny(
        self,
    ) -> None:
        """The toggle/backend alone cannot authorize host Python execution."""
        default = CodeModeToolkit(
            self.native,
            backend=LocalBackend(),
            cwd=self.directory.name,
        )
        self.assertFalse(default.auto_approve)
        self.toolkit.auto_approve = False
        self.model.set_responses(
            [self.code_response("await tools.echo(value=1)")],
        )
        events = [
            event
            async for event in self.agent.reply_stream(
                UserMsg(name="user", content="Run"),
            )
        ]
        self.assertEqual(
            (
                sum(isinstance(e, RequireUserConfirmEvent) for e in events),
                self.audit.acting,
                self.effects,
            ),
            (1, [], []),
        )
        context = self.agent.state.permission_context
        context.allow_rules["run_code"] = [
            PermissionRule(
                tool_name="run_code",
                rule_content=None,
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        ]
        allowed = await self.run_code("await tools.echo(value=2)")
        self.toolkit.auto_approve = True
        context.deny_rules["run_code"] = [
            PermissionRule(
                tool_name="run_code",
                rule_content=None,
                behavior=PermissionBehavior.DENY,
                source="test",
            ),
        ]
        denied = await self.run_code("await tools.echo(value=3)")
        self.assertEqual(
            (allowed.state, denied.state, self.effects),
            (ToolResultState.SUCCESS, ToolResultState.DENIED, [2]),
        )

    async def test_dont_ask_requires_allow_rule_and_honors_deny(self) -> None:
        """Unattended scripts need authorization and retain deny priority."""
        self.toolkit.auto_approve = False
        context = self.agent.state.permission_context
        context.mode = PermissionMode.DONT_ASK
        code = "await tools.echo(value=7)"
        for rule, expected, effects, acting in (
            (None, PermissionBehavior.DENY, [], []),
            (
                PermissionBehavior.ALLOW,
                PermissionBehavior.ALLOW,
                [7],
                ["run_code", "echo"],
            ),
            (PermissionBehavior.DENY, PermissionBehavior.DENY, [7], []),
        ):
            with self.subTest(rule=rule):
                if rule is not None:
                    rules = (
                        context.allow_rules
                        if rule == PermissionBehavior.ALLOW
                        else context.deny_rules
                    )
                    rules["run_code"] = [
                        PermissionRule(
                            tool_name="run_code",
                            rule_content=None,
                            behavior=rule,
                            source="test",
                        ),
                    ]
                decision = await self.agent._engine.check_permission(
                    self.toolkit.code,
                    {"code": code},
                )
                self.audit.acting.clear()
                result = await self.run_code(code)
                self.assertEqual(
                    (
                        decision.behavior,
                        result.state,
                        self.effects,
                        self.audit.acting,
                    ),
                    (
                        expected,
                        ToolResultState.SUCCESS
                        if expected == PermissionBehavior.ALLOW
                        else ToolResultState.DENIED,
                        effects,
                        acting,
                    ),
                )

    async def test_budget_whitelist_preserves_structured_output(self) -> None:
        """Budget exhaustion permits only the final structured tool."""
        agent = Agent(
            name="budget",
            system_prompt="Return structured output.",
            model=self.model,
            toolkit=CodeModeToolkit(
                self.native,
                backend=LocalBackend(),
                cwd=self.directory.name,
                auto_approve=True,
            ),
            middlewares=[ReplyBudgetControlMiddleware(token_budget=0)],
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        self.model.set_responses(
            [
                self.code_response(
                    "try:\n    await tools.echo(value=99)\n"
                    "except RuntimeError:\n    pass\n"
                    "await tools.GenerateStructuredOutput(value=42)",
                ),
            ],
        )
        result = await agent.reply(
            UserMsg(name="user", content="Answer"),
            structured_schema=Answer,
        )
        self.assertEqual(
            (result.structured_output, self.effects),
            ({"value": 42}, []),
        )

    async def test_whitelist_dispatch_and_next_turn_reset(self) -> None:
        """Restrictions apply to host dispatch and expire on a new choice."""
        for names, expected in ((["echo"], {"echo"}), ([], set())):
            with self.subTest(names=names):
                _, choice = self.toolkit.adapt_tool_choice(
                    [],
                    ToolChoice(mode="auto", tools=names),
                )
                script = Script(self.toolkit, self.agent.state)
                self.assertEqual(set(await script.definitions()), expected)
                self.assertEqual(choice.mode, "auto" if names else "none")
                if not names:
                    with self.assertRaisesRegex(ValueError, "excluded"):
                        await script.invoke("echo", {"value": 1})
                    with self.assertRaisesRegex(ValueError, "excluded"):
                        await self.agent.invoke_tool("echo", {"value": 2})
        self.toolkit.adapt_tool_choice([], None)
        result = await self.run_code("await tools.echo(value=3)")
        self.assertEqual(
            (result.state, self.effects),
            (ToolResultState.SUCCESS, [3]),
        )

    async def test_whitelist_rpc_errors_resolve_names_and_aliases(
        self,
    ) -> None:
        """Distinguish excluded tools from missing and inactive tools."""
        await self.native.add_tool(self.make_tool("odd.name"))
        self.native.tool_groups.append(
            ToolGroup(
                name="extra",
                description="Inactive tools",
                tools=[self.make_tool("hidden.name")],
            ),
        )
        self.toolkit.adapt_tool_choice(
            [],
            ToolChoice(mode="auto", tools=["echo"]),
        )
        script = Script(self.toolkit, self.agent.state)
        worker = Mock(spec=WorkerProcess)
        for kind in ("tool", "describe"):
            for name, message in (
                ("odd.name", "Tool excluded by tool choice: odd.name"),
                (
                    python_name("odd.name"),
                    "Tool excluded by tool choice: odd.name",
                ),
                ("missing", "Unknown or unavailable tool: missing"),
                ("hidden.name", "Unknown or unavailable tool: hidden.name"),
                (
                    python_name("hidden.name"),
                    "Unknown or unavailable tool: "
                    f"{python_name('hidden.name')}",
                ),
            ):
                with self.subTest(kind=kind, name=name):
                    worker.send.reset_mock()
                    await script.respond(
                        worker,
                        {
                            "kind": kind,
                            "id": 1,
                            "name": name,
                            "arguments": {"value": 99},
                        },
                    )
                    worker.send.assert_awaited_once_with(
                        {
                            "id": 1,
                            "error": f"ValueError: {message}",
                        },
                    )
        worker.send.reset_mock()
        await script.respond(worker, {"kind": "search", "id": 2})
        worker.send.assert_awaited_once_with(
            {
                "id": 2,
                "result": script.summaries(await script.definitions()),
            },
        )
        self.assertEqual(
            (set(await script.definitions()), self.effects, script.calls),
            ({"echo"}, [], []),
        )

    async def test_forced_tool_enforced_and_missing_call_rejected(
        self,
    ) -> None:
        """Scripts cannot replace or omit a forced native action."""
        await self.native.add_tool(self.make_tool("other"))
        self.toolkit.adapt_tool_choice([], ToolChoice(mode="echo"))
        missing = "Required native tool did not complete successfully: echo"
        for code, expected_state, expected_error, effects in (
            ("text('done')", ToolResultState.ERROR, missing, []),
            (
                "try:\n    await tools.other(value=99)\n"
                "except RuntimeError:\n    pass",
                ToolResultState.ERROR,
                missing,
                [],
            ),
            (
                "try:\n    await tools.echo()\n"
                "except RuntimeError:\n    pass",
                ToolResultState.ERROR,
                missing,
                [],
            ),
            (
                "await tools.echo(value=42)",
                ToolResultState.SUCCESS,
                None,
                [42],
            ),
        ):
            with self.subTest(code=code):
                result = await self.run_code(code)
                self.assertEqual(
                    (
                        result.state,
                        result.metadata["code_mode"]["error"],
                        self.effects,
                    ),
                    (expected_state, expected_error, effects),
                )
        self.toolkit.adapt_tool_choice([], None)
        result = await self.run_code("await tools.other(value=7)")
        self.assertEqual(
            (result.state, self.toolkit.required_tool, self.effects),
            (ToolResultState.SUCCESS, None, [42, 7]),
        )

    async def test_model_contracts_follow_current_tool_choice(self) -> None:
        """Model-visible contracts match this turn, including resets."""
        await self.native.add_tool(self.make_tool("other"))
        model_call = AsyncMock(
            return_value=ChatResponse(
                is_last=True,
                content=[TextBlock(text="ok")],
            ),
        )
        with patch.object(MockModel, "__call__", model_call):
            for choice, names, expected_choice in (
                (
                    ToolChoice(mode="auto", tools=["echo"]),
                    ["echo"],
                    ToolChoice(mode="auto"),
                ),
                (
                    ToolChoice(mode="other"),
                    ["other"],
                    ToolChoice(mode="run_code"),
                ),
                (ToolChoice(mode="none"), [], ToolChoice(mode="none")),
                (None, ["echo", "other"], None),
            ):
                with self.subTest(choice=choice):
                    async for _ in self.agent._reasoning_impl(choice):
                        pass
                    kwargs = model_call.await_args.kwargs
                    description = kwargs["tools"][0]["function"]["description"]
                    contracts = description.split(
                        "Available contracts (possibly partial):\n",
                    )[1]
                    self.assertEqual(
                        (
                            [
                                json.loads(line)["name"]
                                for line in contracts.splitlines()
                            ],
                            kwargs["tool_choice"],
                        ),
                        (names, expected_choice),
                    )

    async def test_tool_choice_survives_confirmation_and_agent_rebuild(
        self,
    ) -> None:
        """A parked script retains host restrictions after state reload."""

        def build(state: AgentState) -> Agent:
            return Agent(
                name="budget",
                system_prompt="Return structured output.",
                model=self.model,
                state=state,
                toolkit=CodeModeToolkit(
                    self.native,
                    backend=LocalBackend(),
                    cwd=self.directory.name,
                ),
                middlewares=[ReplyBudgetControlMiddleware(token_budget=0)],
                react_config=ReActConfig(max_iters=0),
                injection_config=InjectionConfig(inject_runtime_state=False),
            )

        agent = build(AgentState())
        self.model.set_responses(
            [
                self.code_response(
                    "try:\n    await tools.echo(value=99)\n"
                    "except RuntimeError:\n    pass\n"
                    "await tools.GenerateStructuredOutput(value=42)",
                ),
            ],
        )
        events = [
            event
            async for event in agent.reply_stream(
                UserMsg(name="user", content="Answer"),
                structured_schema=Answer,
            )
        ]
        request = next(
            e for e in events if isinstance(e, RequireUserConfirmEvent)
        )
        restored = build(
            AgentState.model_validate_json(agent.state.model_dump_json()),
        )
        self.assertEqual(
            (
                restored.toolkit.allowed_tools,
                restored.toolkit.required_tool,
            ),
            (
                frozenset(["GenerateStructuredOutput"]),
                "GenerateStructuredOutput",
            ),
        )
        result = await restored.reply(
            UserConfirmResultEvent(
                reply_id=request.reply_id,
                confirm_results=[
                    ConfirmResult(
                        confirmed=True,
                        tool_call=request.tool_calls[0],
                    ),
                ],
            ),
        )
        self.assertEqual(
            (result.structured_output, self.effects),
            ({"value": 42}, []),
        )

    async def test_recursion_guard_tracks_tool_type(self) -> None:
        """Renaming the code entry does not disable recursion protection."""
        self.toolkit.code.name = "execute_python"
        token = nested_tool_call.set(True)
        try:
            with self.assertRaisesRegex(ValueError, "Recursive"):
                await self.agent.invoke_tool(
                    "execute_python",
                    {"code": "pass"},
                )
        finally:
            nested_tool_call.reset(token)

    async def test_waiting_writer_precedes_new_readers(self) -> None:
        """New readers cannot starve a queued writer; cancellation unblocks."""
        reader = self.make_tool("reader", is_concurrency_safe=True)
        writer = self.make_tool("writer", is_concurrency_safe=False)
        for cancel_writer in (False, True):
            order = []

            async def enter(tool: Any, entered: list) -> None:
                async with self.toolkit.execution_slot(tool):
                    entered.append(tool.name)

            async with self.toolkit.execution_slot(reader):
                write = asyncio.create_task(enter(writer, order))
                await asyncio.sleep(0)
                read = asyncio.create_task(enter(reader, order))
                await asyncio.sleep(0)
                self.assertEqual(order, [])
                if cancel_writer:
                    write.cancel()
                    await asyncio.gather(write, return_exceptions=True)
            await asyncio.wait_for(
                asyncio.gather(write, read, return_exceptions=True),
                1,
            )
            self.assertEqual(
                (order, self.toolkit.waiting_writers),
                (["reader"] if cancel_writer else ["writer", "reader"], 0),
            )

    async def test_timeout_retains_cause_when_cleanup_fails(self) -> None:
        """A cleanup failure cannot replace an expired execution deadline."""
        worker = AsyncMock(spec=WorkerProcess)
        worker.__aenter__.return_value = worker
        worker.__aexit__.side_effect = RuntimeError("cleanup timed out")

        async def block() -> None:
            await asyncio.Event().wait()

        worker.receive.side_effect = block
        worker.watch.side_effect = block
        self.toolkit.config.timeout = 0.02
        with patch(
            "agentscope.code_mode._script.WorkerProcess",
            return_value=worker,
        ):
            response = await Script(self.toolkit, self.agent.state).run("pass")
        self.assertEqual(
            (response.state, response.metadata["code_mode"]["error"]),
            (
                ToolResultState.ERROR,
                "Code Mode execution timed out; cleanup: RuntimeError: "
                "cleanup timed out",
            ),
        )

    async def test_monitor_unavailable_has_distinct_error(self) -> None:
        """Permission failures are attributed to monitoring, not Python."""
        worker = WorkerProcess(
            self.toolkit.backend,
            self.directory.name,
            sys.executable,
            self.toolkit.config,
        )
        with patch.object(worker, "_sample", side_effect=psutil.AccessDenied):
            with self.assertRaisesRegex(
                RuntimeError,
                "monitoring unavailable",
            ):
                await worker.watch()

    async def test_static_name_conflict_fails_at_construction(self) -> None:
        """Reserved names fail before the first model request."""
        with self.assertRaisesRegex(ValueError, "reserved"):
            CodeModeToolkit(
                Toolkit(tools=[self.make_tool("run_code")]),
                backend=LocalBackend(),
                cwd=self.directory.name,
            )

    async def test_real_model_example_assembles_offline(self) -> None:
        """Load the environment key and explicit authorization without HTTP."""
        path = Path(__file__).resolve().parents[1] / "examples" / "code_mode"
        spec = importlib.util.spec_from_file_location(
            "code_demo",
            path / "main.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.dict(
            os.environ,
            {"DASHSCOPE_API_KEY": "offline-test"},
        ), patch.object(module, "Agent") as constructor, patch(
            "builtins.print",
        ):
            constructor.return_value.state.context = []
            constructor.return_value.reply = AsyncMock(
                return_value=UserMsg(name="test", content="done"),
            )
            await module.main()
        options = constructor.call_args.kwargs
        self.assertEqual(
            (
                options["toolkit"].auto_approve,
                options["model"].credential.api_key.get_secret_value(),
            ),
            (True, "offline-test"),
        )

    async def test_agent_loop_exposes_one_tool_and_keeps_only_selection(
        self,
    ) -> None:
        """A real reply cycle receives code, executes it, then answers."""
        self.model.set_responses(
            [
                self.code_response(
                    "r = await tools.echo(value=3)\n"
                    'text(r["content"][0]["text"])',
                ),
                ChatResponse(
                    is_last=True,
                    content=[TextBlock(text="Finished")],
                ),
            ],
        )
        result = await self.agent.reply(UserMsg(name="user", content="Run"))
        self.assertEqual(result.get_text_content(), "Finished")
        self.assertEqual(self.effects, [3])
        schemas = await self.toolkit.get_tool_schemas()
        self.assertEqual(
            [s["function"]["name"] for s in schemas],
            ["run_code"],
        )
        blocks = self.agent.state.context[-1].get_content_blocks("tool_result")
        self.assertEqual([block.name for block in blocks], ["run_code"])
        self.assertNotIn("x" * 100, self.agent.state.model_dump_json())

    async def test_denied_ask_external_and_invalid_arguments_do_not_execute(
        self,
    ) -> None:
        """Failed actions are catchable and never acquire side effects."""
        for permission in (PermissionBehavior.ASK, PermissionBehavior.DENY):
            with self.subTest(permission=permission):
                await self.native.add_tool(
                    self.make_tool("blocked", permission),
                )
                result = await self.run_code(
                    "try:\n    await tools.blocked(value=1)\n"
                    'except RuntimeError:\n    text("blocked")',
                )
                self.assertEqual(self.text(result), "blocked")
                self.assertEqual(self.effects, [])
                self.assertEqual(
                    result.metadata["code_mode"]["calls"][0]["state"],
                    "denied",
                )
        external = self.make_tool("external")
        external.is_external_tool = True
        await self.native.add_tool(external)
        for code in (
            "await tools.external(value=1)",
            'await tools.echo(value="wrong")',
            "await tools.missing()",
            'await tools.run_code(code="print(1)")',
        ):
            with self.subTest(code=code):
                result = await self.run_code(code)
                self.assertEqual(result.state, ToolResultState.ERROR)
                self.assertEqual(self.effects, [])

    async def test_dynamic_aliases_add_remove_and_group_activation(
        self,
    ) -> None:
        """A script discovers newly activated tools without worker restart."""
        self.native.tool_groups.append(
            ToolGroup(
                name="extra",
                description="Extra tools",
                tools=[self.make_tool("odd.name")],
            ),
        )
        result = await self.run_code(
            'assert not any(t["name"] == "odd.name" for t in ALL_TOOLS)\n'
            "await tools.reset_tools(extra=True)\n"
            'd = await describe_tool("odd.name")\n'
            'r = await getattr(tools, d["python_name"])(value=8)\n'
            'text(r["content"][0]["text"])',
        )
        self.assertEqual(self.text(result), "value=8")
        self.assertEqual(
            self.agent.state.tool_context.activated_groups,
            ["extra"],
        )
        await self.toolkit.add_tool(self.make_tool("new"))
        self.assertIsNotNone(await self.native.get_tool("new"))
        await self.toolkit.remove_tool("new")
        self.assertIsNone(await self.native.get_tool("new"))
        names = ["echo", "class", "odd.name", "_worker", "tool_x"]
        aliases = [python_name(name) for name in names]
        self.assertEqual(len(set(aliases)), len(names))
        self.assertTrue(all(name.isidentifier() for name in aliases))

    async def test_fresh_variables_cwd_partial_output_and_traceback(
        self,
    ) -> None:
        """Script errors preserve selected evidence without replay."""
        result = await self.run_code(
            "from pathlib import Path\nx = 42\n"
            'Path("result.txt").write_text("saved", encoding="utf-8")\n'
            'text("before")\nraise ValueError("broken")',
        )
        self.assertEqual(result.state, ToolResultState.ERROR)
        self.assertTrue(self.text(result).startswith("before"))
        self.assertIn("broken", self.text(result))
        self.assertEqual(
            Path(self.directory.name, "result.txt").read_text(
                encoding="utf-8",
            ),
            "saved",
        )
        result = await self.run_code('text("x" in globals())')
        self.assertEqual(self.text(result), "false")

    async def test_unicode_aliases_route_to_the_exact_tool(self) -> None:
        """Python source normalization must not change the selected tool."""
        await self.native.add_tool(self.make_tool("K"))
        alias = python_name("K")
        for with_ascii_name in (False, True):
            with self.subTest(with_ascii_name=with_ascii_name):
                if with_ascii_name:
                    await self.native.add_tool(self.make_tool("K"))
                result = await self.run_code(
                    f"r = await tools.{alias}(value=7)\ntext(r['state'])",
                )
                self.assertEqual(
                    (
                        result.state,
                        self.text(result),
                        result.metadata["code_mode"]["calls"],
                    ),
                    (
                        ToolResultState.SUCCESS,
                        "success",
                        [{"name": "K", "state": "success", "id": AnyString()}],
                    ),
                )

    def test_python_aliases_are_ascii_and_distinct(self) -> None:
        """Reserve source-safe aliases for Unicode and special names."""
        names = ["K", "K", "Ｋ", "中文", "class", "_hidden", "tool_test"]
        aliases = [python_name(name) for name in names]
        self.assertEqual(
            (
                aliases[0],
                len(set(aliases)),
                all(
                    alias.isascii() and alias.isidentifier()
                    for alias in aliases
                ),
                all(alias.startswith("tool_") for alias in aliases[1:]),
            ),
            ("K", len(names), True, True),
        )

    async def test_media_order_mime_and_provenance(self) -> None:
        """Native media is selected explicitly and retains ordered blocks."""
        media = DataBlock(
            source=URLSource(
                url="https://example.org/picture.png",
                media_type="image/png",
            ),
        )

        async def picture() -> ToolChunk:
            return ToolChunk(content=[media])

        await self.native.add_tool(
            FunctionTool(
                picture,
                name="picture",
                is_read_only=True,
            ),
        )
        result = await self.run_code(
            'r = await tools.picture()\ntext("before")\n'
            'image(r["content"][0])\ntext("after")',
        )
        self.assertEqual(
            self.normalized_blocks(result.content),
            self.normalized_blocks(
                [TextBlock(text="before"), media, TextBlock(text="after")],
            ),
        )
        result = await self.run_code(
            f"image({media.model_dump(mode='json')!r})",
        )
        self.assertEqual(result.state, ToolResultState.ERROR)
        inline = DataBlock(
            source=Base64Source(
                data="aGVsbG8=",
                media_type="audio/wav",
            ),
        )
        result = await self.run_code(
            f"audio({inline.model_dump(mode='json')!r})",
        )
        self.assertEqual(result.content, [inline])
        result = await self.run_code(
            f"image({inline.model_dump(mode='json')!r})",
        )
        self.assertEqual(result.state, ToolResultState.ERROR)

    async def test_structured_output_forced_choice_and_tool_removal(
        self,
    ) -> None:
        """Forced native output routes through run_code and updates state."""
        self.agent.react_config = ReActConfig(max_iters=0)
        self.model.set_responses(
            [
                self.code_response(
                    "await tools.GenerateStructuredOutput(value=42)",
                ),
            ],
        )
        result = await self.agent.reply(
            UserMsg(name="user", content="Answer"),
            structured_schema=Answer,
        )
        self.assertEqual(result.structured_output, {"value": 42})
        self.agent.react_config = ReActConfig()
        self.model.set_responses(
            [ChatResponse(is_last=True, content=[TextBlock(text="ok")])],
        )
        await self.agent.reply(UserMsg(name="user", content="Next"))
        self.assertIsNone(
            await self.native.get_tool("GenerateStructuredOutput"),
        )
        messages, choice = self.toolkit.adapt_tool_choice(
            [],
            ToolChoice(mode="GenerateStructuredOutput"),
        )
        self.assertEqual(choice, ToolChoice(mode="run_code"))
        self.assertIn(
            "GenerateStructuredOutput",
            messages[0].get_text_content(),
        )
        _, choice = self.toolkit.adapt_tool_choice(
            [],
            ToolChoice(mode="echo", tools=["echo"]),
        )
        self.assertEqual(choice, ToolChoice(mode="run_code"))

    async def test_subprocess_output_does_not_corrupt_rpc(self) -> None:
        """Inherited descriptors carry ordinary output, never RPC packets."""
        self.allow_test_subprocesses()
        schemas = await self.toolkit.get_tool_schemas()
        self.assertIn(
            "Direct subprocesses are enabled by this backend.",
            schemas[0]["function"]["description"],
        )
        child = (
            "import os; os.write(1, b'child-out\\n'); "
            "os.write(2, b'child-err\\n')"
        )
        result = await self.run_code(
            "import os, subprocess, sys\n"
            f"subprocess.run([sys.executable, '-c', {child!r}], "
            "check=True, close_fds=False)\n"
            "os.write(1, b'direct-out\\n')\n"
            "os.write(2, b'direct-err\\n')\n"
            "text('finished\\n')\n"
            "reply = await tools.echo(value=7)\n"
            "text(reply['state'] + '\\n')",
        )
        self.assertEqual(
            {
                "state": result.state,
                "lines": sorted(self.text(result).splitlines()),
                "effects": self.effects,
                "error": result.metadata["code_mode"]["error"],
            },
            {
                "state": ToolResultState.SUCCESS,
                "lines": [
                    "child-err",
                    "child-out",
                    "direct-err",
                    "direct-out",
                    "finished",
                    "success",
                ],
                "effects": [7],
                "error": None,
            },
        )

    async def test_empty_host_output_uses_no_blocks(self) -> None:
        """Empty packets do not allocate objects or consume the text budget."""
        self.toolkit.config = CodeModeConfig(max_output_bytes=256)
        script = Script(self.toolkit, self.agent.state)
        remaining = script.remaining
        for _ in range(100000):
            await script.text("")
        self.assertEqual(
            (script.output, script.remaining, script.truncated),
            ([], remaining, False),
        )

    async def test_empty_worker_output_does_not_emit_packets(self) -> None:
        """Inspect RPC directly so host filtering cannot hide empty packets."""
        async with WorkerProcess(
            LocalBackend(),
            self.directory.name,
            sys.executable,
            CodeModeConfig(),
        ) as worker:
            await worker.send(
                {
                    "code": "for _ in range(100000):\n    text('')\n"
                    "text('finished')",
                    "tools": [],
                },
            )
            packets = [
                await asyncio.wait_for(worker.receive(), 5) for _ in range(2)
            ]
            self.assertEqual(
                packets,
                [{"kind": "text", "value": "finished"}, {"kind": "done"}],
            )

    async def test_output_block_limit_is_shared_with_media(self) -> None:
        """Small packets cannot grow host object count past the block cap."""
        self.toolkit.config = CodeModeConfig(max_output_blocks=4)
        script = Script(self.toolkit, self.agent.state)
        inline = DataBlock(
            source=Base64Source(data="aGVsbG8=", media_type="audio/wav"),
        )
        await script.text("first")
        await script.media("audio", inline.model_dump(mode="json"))
        await script.text("last")
        await script.media("audio", inline.model_dump(mode="json"))
        for _ in range(1000):
            await script.text("ignored")
        self.assertEqual(
            {
                "content": self.normalized_blocks(script.output),
                "remaining": script.remaining,
                "truncated": script.truncated,
            },
            {
                "content": self.normalized_blocks(
                    [
                        TextBlock(text="first"),
                        inline,
                        TextBlock(text="last"),
                        TextBlock(text="\n[Code output truncated]"),
                    ],
                ),
                "remaining": 0,
                "truncated": True,
            },
        )
        result = await self.run_code(
            "for _ in range(1000):\n    text('x')",
        )
        self.assertEqual(
            [block.text for block in result.content],
            ["xxx\n[Code output truncated]"],
        )

    async def test_concurrent_output_shares_one_budget(self) -> None:
        """RPC and ordinary-output readers cannot overspend shared limits."""
        self.toolkit.config = CodeModeConfig(
            max_output_bytes=256,
            max_output_blocks=4,
        )
        script = Script(self.toolkit, self.agent.state)
        await asyncio.gather(*(script.text("x" * 100) for _ in range(20)))
        self.assertEqual(
            {
                "blocks": [block.text for block in script.output],
                "remaining": script.remaining,
                "truncated": script.truncated,
            },
            {
                "blocks": [
                    "x" * 100,
                    "x" * 100,
                    "x" * 32 + "\n[Code output truncated]",
                ],
                "remaining": 0,
                "truncated": True,
            },
        )

    async def test_large_child_output_is_drained_after_truncation(
        self,
    ) -> None:
        """Output beyond pipe capacity stays bounded and cannot deadlock."""
        self.allow_test_subprocesses()
        self.toolkit.config = CodeModeConfig(
            max_output_bytes=256,
            max_message_bytes=4096,
            timeout=10,
        )
        child = (
            "import os; "
            "os.write(1, b'x' * 200000); os.write(2, b'y' * 200000)"
        )
        result = await self.run_code(
            "import subprocess, sys\n"
            f"subprocess.run([sys.executable, '-c', {child!r}], check=True)\n"
            "text('finished')",
        )
        self.assertEqual(
            (result.state, result.metadata["code_mode"]),
            (
                ToolResultState.SUCCESS,
                {"calls": [], "truncated": True, "error": None},
            ),
        )
        self.assertLessEqual(len(self.text(result).encode("utf-8")), 256)

    async def test_child_cannot_read_or_inject_rpc_packets(self) -> None:
        """Child stdin is EOF and JSON-looking output remains plain text."""
        self.allow_test_subprocesses()
        child = (
            "import sys; "
            "print('empty-input=' + str(sys.stdin.read() == '')); "
            'print(\'{"kind": "done", "error": "injected"}\')'
        )
        result = await self.run_code(
            "import subprocess, sys\n"
            f"subprocess.run([sys.executable, '-c', {child!r}], check=True)\n"
            "text('finished\\n')",
        )
        self.assertEqual(
            (result.state, sorted(self.text(result).splitlines())),
            (
                ToolResultState.SUCCESS,
                [
                    "empty-input=True",
                    "finished",
                    '{"kind": "done", "error": "injected"}',
                ],
            ),
        )

    async def test_raw_output_drains_on_timeout_and_cancel(self) -> None:
        """A full ordinary-output pipe cannot prevent worker cleanup."""
        for ending in ("timeout", "cancel"):
            with self.subTest(ending=ending):
                self.toolkit.config = CodeModeConfig(
                    max_output_bytes=256,
                    timeout=0.8 if ending == "timeout" else 10,
                )
                marker = Path(self.directory.name) / f"output-{ending}.pid"
                code = (
                    "import os\nfrom pathlib import Path\n"
                    f"Path({str(marker)!r}).write_text(str(os.getpid()))\n"
                    "while True:\n    os.write(1, b'x' * 4096)"
                )
                task = asyncio.create_task(self.run_code(code))
                try:
                    pid = await self.wait_for_pid(marker)
                    if ending == "cancel":
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    else:
                        result = await task
                        self.assertEqual(
                            (result.state, result.metadata["code_mode"]),
                            (
                                ToolResultState.ERROR,
                                {
                                    "calls": [],
                                    "truncated": True,
                                    "error": "Code Mode execution timed out",
                                },
                            ),
                        )
                    self.assertFalse(psutil.pid_exists(pid))
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_ordinary_output_decodes_split_and_invalid_utf8(
        self,
    ) -> None:
        """Force read boundaries and flush an incomplete character at EOF."""
        output = AsyncMock()
        worker = WorkerProcess(
            LocalBackend(),
            self.directory.name,
            sys.executable,
            CodeModeConfig(),
            on_output=output,
        )
        stream = AsyncMock(spec=asyncio.StreamReader)
        stream.read.side_effect = [b"\xe4", b"\xbd\xa0\xff", b"\xe4", b""]
        worker.process = Mock(spec=asyncio.subprocess.Process, stderr=stream)
        await worker._capture_output()
        self.assertEqual(
            (output.await_args_list, worker._output_error),
            (
                [
                    mock_call(""),
                    mock_call("你\ufffd"),
                    mock_call(""),
                    mock_call("\ufffd"),
                ],
                None,
            ),
        )

    async def test_output_and_rpc_limits(self) -> None:
        """Limits bound selected output and the count of host operations."""
        self.toolkit.config = CodeModeConfig(max_output_bytes=256, max_calls=1)
        result = await self.run_code('text("é" * 1000)')
        self.assertLessEqual(len(self.text(result).encode("utf-8")), 256)
        self.assertTrue(result.metadata["code_mode"]["truncated"])
        result = await self.run_code('text("x" * 230)\ntext("y" * 100)')
        self.assertLessEqual(len(self.text(result).encode("utf-8")), 256)
        self.assertTrue(self.text(result).endswith("[Code output truncated]"))
        result = await self.run_code(
            "await tools.echo(value=1)\nawait tools.echo(value=2)",
        )
        self.assertEqual(result.state, ToolResultState.ERROR)
        self.assertEqual(self.effects, [1])

    async def test_message_limit_returns_catchable_error(self) -> None:
        """Oversized intermediate results unblock the awaiting worker."""
        self.toolkit.config = CodeModeConfig(max_message_bytes=4096)
        result = await self.run_code(
            "try:\n    await tools.echo(value=1)\n"
            "except RuntimeError as e:\n    text(str(e))",
        )
        self.assertEqual(self.text(result), "Code Mode message limit exceeded")

    async def test_timeout_kills_worker_and_preserves_evidence(self) -> None:
        """A blocked worker cannot outlive the configured execution budget."""
        self.toolkit.config = CodeModeConfig(timeout=0.8)
        result = await self.run_code(
            "import os, time\ntext(os.getpid())\ntime.sleep(60)",
        )
        self.assertEqual(result.state, ToolResultState.ERROR)
        pid = int(self.text(result).split("\n", maxsplit=1)[0])
        self.assertFalse(psutil.pid_exists(pid))
        self.assertEqual(
            result.metadata["code_mode"]["error"],
            "Code Mode execution timed out",
        )

    async def test_cancel_propagates_to_pending_native_call(self) -> None:
        """Cancellation stops both the worker and its in-flight host call."""
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def wait() -> None:
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                await asyncio.sleep(0.01)
                cancelled.set()

        await self.native.add_tool(FunctionTool(wait, is_read_only=True))
        task = asyncio.create_task(self.run_code("await tools.wait()"))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())
        self.assertFalse(nested_tool_call.get())

    async def test_host_cleanup_is_bounded_and_survives_agent_rebuild(
        self,
    ) -> None:
        """Uncooperative calls stay tracked without blocking script exit."""
        for ending in ("timeout", "cancel", "success", "rpc_timeout"):
            with self.subTest(ending=ending):
                entered = asyncio.Event()
                release = asyncio.Event()
                cancelled = asyncio.Event()

                async def stubborn() -> None:
                    # pylint: disable=cell-var-from-loop
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()
                        while not release.is_set():
                            try:
                                await release.wait()
                            except asyncio.CancelledError:
                                continue
                        raise RuntimeError("late cleanup failure")

                async def ready() -> None:
                    # pylint: disable=cell-var-from-loop
                    await entered.wait()

                await self.native.add_tool(
                    [
                        FunctionTool(stubborn, is_read_only=True),
                        FunctionTool(ready, is_read_only=True),
                    ],
                )
                self.toolkit.config.timeout = 2
                self.toolkit.config.tool_cleanup_timeout = 0.03
                code = "await tools.stubborn()"
                if ending == "success":
                    code = (
                        "import asyncio\n"
                        "task = asyncio.create_task(tools.stubborn())\n"
                        "await tools.ready()"
                    )
                elif ending == "rpc_timeout":
                    code = (
                        "import asyncio\n"
                        "await asyncio.wait_for(tools.stubborn(), 0.01)"
                    )
                workers = []

                def worker_factory(*args: Any, **kwargs: Any) -> WorkerProcess:
                    # pylint: disable=cell-var-from-loop
                    worker = WorkerProcess(*args, **kwargs)
                    workers.append(worker)
                    return worker

                with patch(
                    "agentscope.code_mode._script.WorkerProcess",
                    side_effect=worker_factory,
                ):
                    execution = asyncio.create_task(self.run_code(code))
                    try:
                        await asyncio.wait_for(entered.wait(), 5)
                        if ending == "cancel":
                            execution.cancel()
                        done, _ = await asyncio.wait({execution}, timeout=5)
                        self.assertEqual(done, {execution})
                        if ending == "cancel":
                            with self.assertRaises(asyncio.CancelledError):
                                await execution
                        else:
                            result = execution.result()
                            details = result.metadata["code_mode"]
                            self.assertEqual(
                                (
                                    result.state,
                                    bool(details["cleanup_pending"]),
                                    "Host tool cleanup" in details["error"],
                                    "execution timed out" in details["error"],
                                ),
                                (
                                    ToolResultState.ERROR,
                                    True,
                                    True,
                                    ending in ("timeout", "rpc_timeout"),
                                ),
                            )
                        self.assertTrue(cancelled.is_set())
                        self.assertIsNotNone(workers[0].process.returncode)
                        session_id = self.agent.state.session_id
                        tracked = set(_CLEANING_TASKS[session_id])
                        self.assertTrue(any(not t.done() for t in tracked))
                        rebuilt = Agent(
                            name="rebuilt",
                            system_prompt="Test cleanup tracking.",
                            model=self.model,
                            state=AgentState.model_validate_json(
                                self.agent.state.model_dump_json(),
                            ),
                            toolkit=CodeModeToolkit(
                                self.native,
                                backend=LocalBackend(),
                                cwd=self.directory.name,
                                auto_approve=True,
                            ),
                        )
                        blocked = await rebuilt.invoke_tool(
                            "run_code",
                            {"code": "await tools.echo(value=99)"},
                        )
                        self.assertEqual(
                            (blocked.state, self.effects, len(workers)),
                            (ToolResultState.ERROR, [], 1),
                        )
                        self.assertIn("still cleaning up", self.text(blocked))
                    finally:
                        release.set()
                        if not execution.done():
                            execution.cancel()
                        await asyncio.gather(
                            execution,
                            *_CLEANING_TASKS.get(
                                self.agent.state.session_id,
                                (),
                            ),
                            return_exceptions=True,
                        )
                self.assertNotIn(session_id, _CLEANING_TASKS)
                if ending != "cancel":
                    # Late cleanup must not rewrite an already returned log.
                    self.assertEqual(
                        [
                            call
                            for call in details["calls"]
                            if call["name"] == "stubborn"
                        ],
                        [{"name": "stubborn", "state": "calling"}],
                    )
                resumed = await rebuilt.invoke_tool(
                    "run_code",
                    {"code": "text('resumed')"},
                )
                self.assertEqual(
                    (resumed.state, self.text(resumed)),
                    (ToolResultState.SUCCESS, "resumed"),
                )

    async def test_cancel_after_streamed_error_propagates(self) -> None:
        """An earlier error chunk must not hide cancellation of the task."""
        started = asyncio.Event()

        async def stream_error() -> AsyncGenerator[ToolChunk, None]:
            yield ToolChunk(content=[], state=ToolResultState.ERROR)
            started.set()
            await asyncio.Event().wait()

        await self.native.add_tool(
            FunctionTool(stream_error, is_read_only=True),
        )
        task = asyncio.create_task(self.agent.invoke_tool("stream_error", {}))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(
            (task.cancelling(), task.cancelled(), nested_tool_call.get()),
            (1, True, False),
        )

    async def test_worker_call_cancellation_waits_for_ack(self) -> None:
        """Late results and repeated cancellation cannot bypass the ack."""
        for cancellation in (
            "await asyncio.wait_for(task, 0.05)",
            "task.cancel()\n    await asyncio.sleep(0)\n"
            "    task.cancel()\n    await task",
        ):
            with self.subTest(cancellation=cancellation):
                async with WorkerProcess(
                    LocalBackend(),
                    self.directory.name,
                    sys.executable,
                    CodeModeConfig(),
                ) as worker:
                    await worker.send(
                        {
                            "code": "import asyncio\n"
                            "task = asyncio.create_task(tools.slow_write())\n"
                            "await asyncio.sleep(0)\ntry:\n"
                            f"    {cancellation}\n"
                            "except (TimeoutError, asyncio.CancelledError):"
                            "\n    text('continued')",
                            "tools": [],
                        },
                    )
                    self.assertEqual(
                        [
                            await asyncio.wait_for(worker.receive(), 5)
                            for _ in range(2)
                        ],
                        [
                            {
                                "kind": "tool",
                                "id": 1,
                                "name": "slow_write",
                                "arguments": {},
                            },
                            {"kind": "cancel", "id": 1},
                        ],
                    )
                    await worker.send({"id": 1, "result": "late result"})
                    with self.assertRaises(TimeoutError):
                        await asyncio.wait_for(worker.receive(), 0.05)
                    await worker.send({"id": 1, "cancel_ack": True})
                    self.assertEqual(
                        [
                            await asyncio.wait_for(worker.receive(), 5)
                            for _ in range(2)
                        ],
                        [
                            {"kind": "text", "value": "continued"},
                            {"kind": "done"},
                        ],
                    )

    async def test_call_timeout_cleans_up_before_continuation_and_retry(
        self,
    ) -> None:
        """A timed-out exclusive write stops before the script proceeds."""
        for retry in (False, True):
            with self.subTest(retry=retry):
                attempts, cleaned, committed = [], [], []
                marker = Path(self.directory.name) / f"started-{retry}"

                async def slow_write() -> str:
                    # pylint: disable=cell-var-from-loop
                    attempt = len(attempts) + 1
                    attempts.append(attempt)
                    if attempt == 1:
                        marker.touch()
                        try:
                            await asyncio.sleep(1)
                        finally:
                            await asyncio.sleep(0.02)
                            cleaned.append(attempt)
                    committed.append(attempt)
                    return "saved"

                async def snapshot() -> ToolChunk:
                    # pylint: disable=cell-var-from-loop
                    return ToolChunk(
                        content=[],
                        metadata={
                            "attempts": attempts.copy(),
                            "cleaned": cleaned.copy(),
                            "committed": committed.copy(),
                        },
                    )

                await self.native.add_tool(
                    [
                        FunctionTool(
                            slow_write,
                            is_concurrency_safe=False,
                            permission=PermissionDecision(
                                behavior=PermissionBehavior.ALLOW,
                                message="test write",
                            ),
                        ),
                        FunctionTool(snapshot, is_read_only=True),
                    ],
                )
                code = (
                    "import asyncio\nfrom pathlib import Path\n"
                    "task = asyncio.create_task(tools.slow_write())\n"
                    f"while not Path({marker.name!r}).exists():\n"
                    "    await asyncio.sleep(0.01)\n"
                    "try:\n    await asyncio.wait_for(task, 0.05)\n"
                    "except TimeoutError:\n    pass\n"
                    "r = await tools.snapshot()\ntext(r['metadata'])\n"
                )
                if retry:
                    code += "saved = await tools.slow_write()\n"
                result = await asyncio.wait_for(self.run_code(code), 5)
                output = self.text(result)
                self.assertEqual(
                    (
                        result.state,
                        json.loads(output),
                        attempts,
                        cleaned,
                        committed,
                        [
                            item["state"]
                            for item in result.metadata["code_mode"]["calls"]
                        ],
                    ),
                    (
                        ToolResultState.SUCCESS,
                        {"attempts": [1], "cleaned": [1], "committed": []},
                        [1, 2] if retry else [1],
                        [1],
                        [2] if retry else [],
                        ["interrupted", "success"]
                        + (["success"] if retry else []),
                    ),
                )

    async def test_cancel_queued_call_does_not_execute_it(self) -> None:
        """Cancelling one lock waiter leaves no write queued for later."""
        marker = Path(self.directory.name) / "holding"
        stopped = asyncio.Event()

        async def hold() -> None:
            marker.touch()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        await self.native.add_tool(
            FunctionTool(
                hold,
                is_concurrency_safe=False,
                permission=PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message="test hold",
                ),
            ),
        )
        self.toolkit.config = CodeModeConfig(max_calls=3)
        result = await asyncio.wait_for(
            self.run_code(
                "import asyncio\nfrom pathlib import Path\n"
                "holder = asyncio.create_task(tools.hold())\n"
                "while not Path('holding').exists():\n"
                "    await asyncio.sleep(0.01)\n"
                "try:\n"
                "    await asyncio.wait_for(tools.echo(value=1), 0.05)\n"
                "except TimeoutError:\n    pass\n"
                "holder.cancel()\n"
                "try:\n    await holder\n"
                "except asyncio.CancelledError:\n    pass\n"
                "r = await tools.echo(value=2)\ntext(r['state'])",
            ),
            5,
        )
        self.assertEqual(
            (
                result.state,
                self.text(result),
                self.effects,
                stopped.is_set(),
                self.toolkit.readers,
                self.toolkit.writer,
            ),
            (ToolResultState.SUCCESS, "success", [2], True, 0, False),
        )

    async def test_cancel_before_start_or_after_completion(self) -> None:
        """Acknowledge each known ID once, including task boundary races."""
        for completed in (False, True):
            with self.subTest(completed=completed):
                script = Script(self.toolkit, self.agent.state)
                worker = Mock(spec=WorkerProcess)
                request = {"kind": "tool", "id": 1}
                cancel = {"kind": "cancel", "id": 1}
                done = {"kind": "done"}
                with patch.object(
                    script,
                    "respond",
                    new_callable=AsyncMock,
                ) as respond:
                    if completed:
                        worker.receive.side_effect = [request, done]
                        await script.receive(worker)
                        await asyncio.gather(*script.pending)
                    worker.receive.side_effect = (
                        [] if completed else [request]
                    ) + [cancel, cancel, {"kind": "cancel", "id": 999}, done]
                    await script.receive(worker)
                    await asyncio.gather(
                        *script.pending,
                        return_exceptions=True,
                    )
                    self.assertEqual(
                        (respond.await_count, worker.send.await_args_list),
                        (
                            int(completed),
                            [
                                mock_call(
                                    {
                                        "id": 1,
                                        "cancel_ack": True,
                                    },
                                ),
                            ],
                        ),
                    )
                    await asyncio.sleep(0)
                    self.assertEqual(
                        (
                            script.pending,
                            script.requests,
                            script.completed_requests,
                        ),
                        (set(), {}, set()),
                    )

    @staticmethod
    def detached_child_code() -> str:
        """Start a portable detached child with no inherited RPC pipes."""
        return (
            "import subprocess, sys\n"
            "child = subprocess.Popen(\n"
            "    [sys.executable, '-I', '-S', '-c', "
            "'import time; time.sleep(60)'],\n"
            "    start_new_session=True,\n"
            "    stdin=subprocess.DEVNULL,\n"
            "    stdout=subprocess.DEVNULL,\n"
            "    stderr=subprocess.DEVNULL,\n"
            ")\n"
        )

    @staticmethod
    def reap_test_child(pid: int) -> None:
        """Clean up a leaked test child even when the regression fails."""
        try:
            child = psutil.Process(pid)
            child.kill()
            psutil.wait_procs([child], timeout=3)
        except psutil.NoSuchProcess:
            pass

    def assert_child_stopped(self, pid: int) -> None:
        """A reparented zombie is stopped even before the OS reaps it."""
        try:
            child = psutil.Process(pid)
            self.assertEqual(child.status(), psutil.STATUS_ZOMBIE)
        except psutil.NoSuchProcess:
            pass

    async def wait_for_pid(self, marker: Path) -> int:
        """Wait for a published PID and register cleanup before assertions."""
        async with asyncio.timeout(5):
            while not marker.exists() or not marker.read_text():
                await asyncio.sleep(0.01)
        pid = int(marker.read_text())
        self.addCleanup(self.reap_test_child, pid)
        return pid

    async def test_detached_child_cleanup_on_script_exit(self) -> None:
        """Detached children stop on completion, error, timeout and cancel."""
        self.allow_test_subprocesses()
        for ending in ("success", "error", "timeout", "cancel"):
            with self.subTest(ending=ending):
                self.toolkit.config = CodeModeConfig(
                    timeout=1 if ending == "timeout" else 10,
                )
                marker = Path(self.directory.name) / f"{ending}.pid"
                code = (
                    self.detached_child_code()
                    + "from pathlib import Path\n"
                    + f"Path({str(marker)!r}).write_text(str(child.pid))\n"
                )
                if ending == "error":
                    code += "raise ValueError('test failure')\n"
                elif ending in ("timeout", "cancel"):
                    code += "import time\ntime.sleep(60)\n"
                task = asyncio.create_task(self.run_code(code))
                try:
                    pid = await self.wait_for_pid(marker)
                    if ending == "cancel":
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    else:
                        result = await task
                        self.assertEqual(
                            result.state,
                            ToolResultState.SUCCESS
                            if ending == "success"
                            else ToolResultState.ERROR,
                        )
                    self.assert_child_stopped(pid)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_tracked_child_cleanup_after_worker_exits(self) -> None:
        """Retain a discovered child identity after its parent disappears."""
        self.allow_test_subprocesses()
        async with WorkerProcess(
            self.toolkit.backend,
            self.directory.name,
            sys.executable,
            CodeModeConfig(),
        ) as worker:
            await worker.send(
                {
                    "code": self.detached_child_code()
                    + "text(child.pid)\n"
                    + "await search_tools()\n"
                    + "import os\nos._exit(0)\n",
                    "tools": [],
                },
            )
            packet = await asyncio.wait_for(worker.receive(), 5)
            pid = packet["value"]
            self.addCleanup(self.reap_test_child, pid)
            request = await asyncio.wait_for(worker.receive(), 5)
            await asyncio.to_thread(worker._sample)
            await worker.send({"id": request["id"], "result": []})
            await asyncio.wait_for(worker.process.wait(), 5)
        self.assert_child_stopped(pid)

    async def test_local_detached_spawn_is_rejected_without_sampling(
        self,
    ) -> None:
        """Reject the orphan-producing launch before a child can exist."""

        async def no_sampling(_worker: WorkerProcess) -> None:
            await asyncio.Event().wait()

        for inherit_stderr in (False, True):
            with self.subTest(inherit_stderr=inherit_stderr):
                marker = Path(self.directory.name) / f"child-{inherit_stderr}"
                code = self.detached_child_code()
                if inherit_stderr:
                    code = code.replace(
                        "stderr=subprocess.DEVNULL",
                        "stderr=None",
                    )
                code += (
                    "from pathlib import Path\nimport os\n"
                    f"Path({marker.name!r}).write_text(str(child.pid))\n"
                    "os._exit(0)\n"
                )
                try:
                    with patch.object(WorkerProcess, "watch", no_sampling):
                        result = await asyncio.wait_for(self.run_code(code), 7)
                    self.assertEqual(
                        (
                            result.state,
                            marker.exists(),
                            "PermissionError" in self.text(result),
                            "native tool" in self.text(result),
                        ),
                        (ToolResultState.ERROR, False, True, True),
                    )
                finally:
                    if marker.exists():
                        self.reap_test_child(int(marker.read_text()))

    async def test_local_process_launch_apis_are_blocked(self) -> None:
        """Check public synchronous, asynchronous and low-level launches."""
        cases = [
            "subprocess.run([sys.executable, '-c', 'pass'])",
            "p = await asyncio.create_subprocess_exec("
            "sys.executable, '-c', 'pass'); await p.wait()",
            "os.system('exit 0')",
            "os.spawnv(os.P_WAIT, sys.executable, "
            "[sys.executable, '-c', 'pass'])",
            "os.execv(sys.executable, [sys.executable, '-c', 'pass'])",
        ]
        if os.name == "posix":
            cases.extend(
                [
                    "pid = os.fork()\n"
                    "if pid == 0: os._exit(0)\n"
                    "os.waitpid(pid, 0)",
                    "pid = os.posix_spawn(sys.executable, "
                    "[sys.executable, '-c', 'pass'], os.environ)\n"
                    "os.waitpid(pid, 0)",
                ],
            )
        for code in cases:
            with self.subTest(code=code):
                result = await self.run_code(
                    "import asyncio, os, subprocess, sys\n" + code,
                )
                self.assertEqual(
                    (
                        result.state,
                        "PermissionError" in self.text(result),
                        "native tool" in self.text(result),
                    ),
                    (ToolResultState.ERROR, True, True),
                )

    async def test_local_native_command_tool_still_runs(self) -> None:
        """The worker policy does not restrict authorized host tools."""
        schemas = await self.toolkit.get_tool_schemas()
        self.assertIn(
            "Standard process-launch APIs are guarded, not isolated.",
            schemas[0]["function"]["description"],
        )

        async def command() -> str:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "print('hello')",
                stdout=asyncio.subprocess.PIPE,
            )
            output, _ = await process.communicate()
            return output.decode().strip()

        await self.native.add_tool(FunctionTool(command, is_read_only=True))
        result = await self.run_code(
            "r = await tools.command()\ntext(r['content'][0]['text'])",
        )
        self.assertEqual(
            (
                result.state,
                self.text(result),
                [
                    call["name"]
                    for call in result.metadata["code_mode"]["calls"]
                ],
            ),
            (ToolResultState.SUCCESS, "hello", ["command"]),
        )

    @skipUnless(
        sys.platform == "linux" and shutil.which("bwrap"),
        "Requires Linux and Bubblewrap",
    )
    async def test_bubblewrap_owns_detached_child_before_sampling(
        self,
    ) -> None:
        """PID namespace ownership survives an unsampled worker exit."""
        try:
            await BubblewrapWorkspace._probe_bubblewrap()
        except RuntimeError as exc:
            self.skipTest(str(exc))
        # pylint: disable=consider-using-with
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.toolkit.backend = BubblewrapBackend(
            host_workdir=self.directory.name,
            host_tmpdir=temporary,
        )
        self.toolkit.cwd = "/workspace"
        self.toolkit.python = "/usr/bin/python3"
        token = uuid.uuid4().hex
        child = (
            "from pathlib import Path; import time; "
            "Path('started').touch(); time.sleep(30)"
        )

        async def no_sampling(_worker: WorkerProcess) -> None:
            await asyncio.Event().wait()

        def survivors() -> list:
            return [
                process
                for process in psutil.process_iter(["cmdline"])
                if token in (process.info["cmdline"] or [])
            ]

        try:
            with patch.object(WorkerProcess, "watch", no_sampling):
                result = await asyncio.wait_for(
                    self.run_code(
                        "import asyncio, os, subprocess, sys\n"
                        "from pathlib import Path\n"
                        f"child = subprocess.Popen([sys.executable, '-c', "
                        f"{child!r}, {token!r}], start_new_session=True)\n"
                        "while not Path('started').exists():\n"
                        "    await asyncio.sleep(0.01)\n"
                        "os._exit(0)",
                    ),
                    7,
                )
            self.assertEqual(
                (
                    result.state,
                    Path(self.directory.name, "started").exists(),
                    [process.pid for process in survivors()],
                ),
                (ToolResultState.ERROR, True, []),
            )
        finally:
            for process in survivors():
                self.reap_test_child(process.pid)

    async def test_cleanup_bounds_inherited_pipe_after_parent_exit(
        self,
    ) -> None:
        """An untracked orphan holding stderr cannot block host cleanup."""
        backend = LocalBackend()
        start = backend.start_process
        child_source = "import time; time.sleep(30)"
        source = (
            "import json, os, subprocess, sys\n"
            "sys.stdin.readline()\n"
            "child = subprocess.Popen(\n"
            f"    [sys.executable, '-c', {child_source!r}],\n"
            "    start_new_session=True, stdin=subprocess.DEVNULL,\n"
            "    stdout=subprocess.DEVNULL)\n"
            "print(json.dumps({'pid': child.pid}), flush=True)\n"
            "os._exit(0)\n"
        )

        async def start_leaking_worker(
            _command: list[str],
            **kwargs: Any,
        ) -> asyncio.subprocess.Process:
            return await start([sys.executable, "-c", source], **kwargs)

        worker = WorkerProcess(
            backend,
            self.directory.name,
            sys.executable,
            CodeModeConfig(),
        )
        with patch.object(backend, "start_process", start_leaking_worker):
            await self.enterAsyncContext(worker)
        await worker.send({"start": True})
        packet = await asyncio.wait_for(worker.receive(), 3)
        pid = packet["pid"]
        self.addCleanup(self.reap_test_child, pid)
        async with asyncio.timeout(3):
            while worker.process.returncode is None:
                await asyncio.sleep(0.01)
        self.assertEqual(worker._children, {})
        closing = asyncio.create_task(worker.close())
        done, _ = await asyncio.wait({closing}, timeout=5)
        if not done:
            self.reap_test_child(pid)
            await closing
            self.fail("Cleanup waited for the orphan to close stderr")
        with self.assertRaisesRegex(RuntimeError, "cleanup timed out"):
            await closing
        self.assertTrue(worker._closed)

    async def test_startup_cancellation_reaps_owned_process(self) -> None:
        """Cancellation during process creation still transfers ownership."""
        backend = LocalBackend()
        created = asyncio.Event()
        release = asyncio.Event()
        start = backend.start_process
        processes = []

        async def delayed(
            *args: Any,
            **kwargs: Any,
        ) -> asyncio.subprocess.Process:
            process = await start(*args, **kwargs)
            processes.append(process)
            created.set()
            await release.wait()
            return process

        async def open_worker() -> None:
            async with WorkerProcess(
                backend,
                self.directory.name,
                sys.executable,
                CodeModeConfig(),
            ):
                self.fail("Cancelled startup must not enter the body")

        with patch.object(backend, "start_process", delayed):
            task = asyncio.create_task(open_worker())
            await asyncio.wait_for(created.wait(), 5)
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(psutil.pid_exists(processes[0].pid))

    async def test_safe_calls_overlap_and_stateful_calls_are_exclusive(
        self,
    ) -> None:
        """Concurrency follows native tool safety and state injection."""
        for safe, stateful, expected_peak in (
            (True, False, 2),
            (False, False, 1),
            (True, True, 1),
        ):
            with self.subTest(safe=safe, stateful=stateful):
                active = 0
                peak = 0
                both_started = asyncio.Event()

                async def operation(
                    _agent_state: AgentState | None = None,
                ) -> str:
                    # pylint: disable=cell-var-from-loop
                    nonlocal active, peak
                    active += 1
                    peak = max(peak, active)
                    try:
                        if expected_peak == 2:
                            if active == 2:
                                both_started.set()
                            await asyncio.wait_for(both_started.wait(), 2)
                        else:
                            await asyncio.sleep(0.03)
                        return "done"
                    finally:
                        active -= 1

                await self.native.add_tool(
                    FunctionTool(
                        operation,
                        name="operation",
                        is_read_only=True,
                        is_concurrency_safe=safe,
                        is_state_injected=stateful,
                        input_schema={"type": "object", "properties": {}},
                    ),
                )
                result = await self.run_code(
                    "import asyncio\n"
                    "r = await asyncio.gather(tools.operation(), "
                    'tools.operation())\ntext([v["state"] for v in r])',
                )
                self.assertEqual(
                    json.loads(self.text(result)),
                    ["success", "success"],
                )
                self.assertEqual((active, peak), (0, expected_peak))

    async def test_nested_interrupt_unblocks_worker(self) -> None:
        """An interrupted native tool cancels the worker's await promptly."""

        async def interrupt() -> None:
            raise asyncio.CancelledError()

        await self.native.add_tool(FunctionTool(interrupt, is_read_only=True))
        result = await asyncio.wait_for(
            self.run_code("await tools.interrupt()"),
            5,
        )
        self.assertEqual(result.state, ToolResultState.INTERRUPTED)

    async def test_native_offload_middleware_returns_actual_result(
        self,
    ) -> None:
        """Code awaits the completed tool, even past an offload deadline."""
        manager = AsyncMock()
        self.agent._acting_middlewares.append(
            ToolOffloadMiddleware(
                bg_manager=manager,
                message_bus=AsyncMock(),
                user_id="user",
                agent_id="code",
                timeout_secs=0.001,
            ),
        )

        async def slow() -> str:
            await asyncio.sleep(0.03)
            return "complete"

        await self.native.add_tool(FunctionTool(slow, is_read_only=True))
        result = await self.run_code(
            'r = await tools.slow()\ntext(r["content"][0]["text"])',
        )
        self.assertEqual(self.text(result), "complete")
        self.assertEqual(manager.mock_calls, [])

    async def test_skills_are_discoverable_and_callable(self) -> None:
        """The original skill loader and Skill tool remain the authority."""
        self.native.tool_groups[0].skills_or_loaders.append(
            Skill(
                name="sample",
                description="Sample skill",
                dir=self.directory.name,
                markdown="Read this evidence.",
                updated_at=0,
            ),
        )
        instructions = await self.toolkit.get_skill_instructions()
        self.assertIn("Sample skill", instructions)
        result = await self.run_code(
            'r = await tools.Skill(skill="sample")\n'
            'text(r["content"][0]["text"])',
        )
        self.assertEqual(result.state, ToolResultState.SUCCESS)
        self.assertIn("Read this evidence.", self.text(result))

    async def test_resource_limits_stop_worker(self) -> None:
        """Memory and CPU sampling stop real workers at configured limits."""
        for config, code, message in (
            (
                CodeModeConfig(max_memory_bytes=1),
                "import time\ntime.sleep(60)",
                "memory limit",
            ),
            (
                CodeModeConfig(max_cpu_seconds=0.05),
                "while True:\n    pass",
                "CPU limit",
            ),
        ):
            with self.subTest(message=message):
                self.toolkit.config = config
                result = await asyncio.wait_for(self.run_code(code), 5)
                self.assertEqual(result.state, ToolResultState.ERROR)
                self.assertIn(message, result.metadata["code_mode"]["error"])

    async def test_cleanup_survives_process_enumeration_failure(self) -> None:
        """Owned process cleanup still runs on a restricted host."""
        async with WorkerProcess(
            LocalBackend(),
            self.directory.name,
            sys.executable,
            CodeModeConfig(),
        ) as worker:
            process = worker.process
            with patch.object(
                psutil.Process,
                "children",
                side_effect=PermissionError,
            ):
                await worker.close()
        self.assertIsNotNone(process.returncode)

    async def test_unsupported_backend_and_bubblewrap_contract(self) -> None:
        """Unsupported backends never fall back to local execution."""
        backend = AsyncMock(spec=BackendBase)
        with self.assertRaises(NotImplementedError):
            await BackendBase.start_process(backend, ["python3"])
        with patch.object(
            LocalBackend,
            "start_process",
            BackendBase.start_process,
        ):
            with self.assertRaises(NotImplementedError):
                CodeModeToolkit(
                    Toolkit(),
                    backend=LocalBackend(),
                    cwd=self.directory.name,
                )
        with tempfile.TemporaryDirectory() as temporary:
            bubblewrap = BubblewrapBackend(
                host_workdir=self.directory.name,
                host_tmpdir=temporary,
            )
            self.assertEqual(
                (
                    BackendBase.isolates_process_tree,
                    LocalBackend().isolates_process_tree,
                    bubblewrap.isolates_process_tree,
                ),
                (False, False, True),
            )
            with patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as spawn:
                await bubblewrap.start_process(
                    ["python3", "-c", "pass"],
                    stdin=asyncio.subprocess.PIPE,
                    limit=4096,
                )
                self.assertIn("--unshare-all", spawn.call_args.args)
                self.assertEqual(
                    spawn.call_args.kwargs["stdin"],
                    asyncio.subprocess.PIPE,
                )
                self.assertEqual(spawn.call_args.kwargs["limit"], 4096)
                self.assertEqual(
                    "start_new_session" in spawn.call_args.kwargs,
                    os.name != "nt",
                )

    async def test_mcp_catalog_and_compression_registration(self) -> None:
        """MCP and dynamic context tools retain native registration."""
        client = AsyncMock()
        client.name = "sample"
        client.list_tools.return_value = [self.make_tool("remote_echo")]
        self.native.tool_groups[0].mcps.append(client)
        self.agent.context_config.compression_tool_enabled = True
        model_input = await self.agent._prepare_model_input()
        self.assertEqual(
            [item["function"]["name"] for item in model_input["tools"]],
            ["run_code"],
        )
        result = await self.run_code(
            'd = await describe_tool("CompressContext")\n'
            "r = await tools.remote_echo(value=9)\n"
            'text({"compression": d["name"], "result": '
            'r["content"][0]["text"]})',
        )
        self.assertEqual(
            json.loads(self.text(result)),
            {"compression": "CompressContext", "result": "value=9"},
        )
        self.assertEqual(self.effects, [9])

    async def test_permission_middleware_denial_is_enforced(self) -> None:
        """A middleware rejection reaches code before any native effect."""
        original = self.agent._check_permission

        async def deny(call: ToolCallBlock, tool: Any, arguments: dict) -> Any:
            if call.name == "echo":
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message="policy denial",
                )
            return await original(call, tool, arguments)

        with patch.object(self.agent, "_check_permission", side_effect=deny):
            result = await self.run_code("await tools.echo(value=1)")
        self.assertEqual(result.state, ToolResultState.ERROR)
        self.assertEqual(self.effects, [])
        self.assertEqual(self.audit.acting, ["run_code"])
        self.assertIn("policy denial", self.text(result))

    async def test_partial_failure_preserves_success_without_replay(
        self,
    ) -> None:
        """Successful branches survive a failing concurrent branch."""
        result = await self.run_code(
            "import asyncio\n"
            "results = await asyncio.gather(tools.echo(value=6), "
            'tools.echo(value="invalid"), return_exceptions=True)\n'
            "text([isinstance(r, Exception) for r in results])",
        )
        self.assertEqual(json.loads(self.text(result)), [False, True])
        self.assertEqual(self.effects, [6])

    async def test_unfinished_rpc_is_cancelled_when_script_exits(self) -> None:
        """Cooperative fire-and-forget work stops before script exit."""
        entered = asyncio.Event()
        stopped = asyncio.Event()

        async def wait() -> None:
            entered.set()
            try:
                await asyncio.sleep(60)
            finally:
                stopped.set()

        async def ready() -> str:
            await entered.wait()
            return "ready"

        await self.native.add_tool(
            [
                FunctionTool(wait, is_read_only=True),
                FunctionTool(ready, is_read_only=True),
            ],
        )
        result = await asyncio.wait_for(
            self.run_code(
                "import asyncio\n"
                "task = asyncio.create_task(tools.wait())\n"
                'await tools.ready()\ntext("done")',
            ),
            5,
        )
        self.assertEqual(self.text(result), "done")
        self.assertTrue(stopped.is_set())
