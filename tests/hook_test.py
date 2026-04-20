# -*- coding: utf-8 -*-
# pylint: disable=too-many-lines
"""Hook related tests in agentscope."""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from pydantic import BaseModel, Field

from agentscope.agent import AgentBase, ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg, TextBlock, ToolUseBlock
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.tool import Toolkit


class MyAgent(AgentBase):
    """Test agent class for testing hooks."""

    def __init__(self) -> None:
        """Initialize the test agent."""
        super().__init__()
        self.records: list[str] = []
        self.memory: list[Msg] = []

    async def reply(self, msg: Msg) -> Msg:
        """Reply to the message."""
        await self.print(msg)
        if isinstance(msg.content, list):
            msg.content.append(
                TextBlock(
                    type="text",
                    text="mark",
                ),
            )
        return msg

    async def observe(self, msg: Msg) -> None:
        """Observe the message without generating a reply."""
        self.memory.append(msg)

    async def handle_interrupt(self, *_args: Any, **_kwargs: Any) -> Msg:
        """Handle the interrupt signal."""
        # This is a placeholder for handling interrupts.
        return Msg("test", "Interrupt handled", "assistant")


class ChildAgent(MyAgent):
    """Child agent for testing hook isolation."""


class GrandChildAgent(ChildAgent):
    """Grandchild agent for testing deeper inheritance."""


class ChildAgentWithReplyOverride(MyAgent):
    """Child agent that overrides reply and calls super().reply(),
    triggering double wrapping by the metaclass. Used to test
    that hook_guard_attr prevents duplicate hook execution."""

    async def reply(self, msg: Msg) -> Msg:
        """Override reply, delegating to parent via super()."""
        return await super().reply(msg)


class ChildAgentWithObserveOverride(MyAgent):
    """Child agent that overrides observe and calls super().observe()."""

    async def observe(self, msg: Msg) -> None:
        """Override observe, delegating to parent via super()."""
        await super().observe(msg)


class GrandChildAgentWithReplyOverride(ChildAgentWithReplyOverride):
    """Three-level inheritance chain with each level overriding reply."""

    async def reply(self, msg: Msg) -> Msg:
        """Override reply again, delegating to parent via super()."""
        return await super().reply(msg)


class AgentA(MyAgent):
    """First parent class."""


class AgentB(MyAgent):
    """Second parent class."""


class AgentC(AgentA, AgentB):
    """Multiple inheritance class."""


class MockModel(ChatModelBase):
    """Mock model that returns text-only on the first call and
    text + tool_use on subsequent calls."""

    def __init__(self) -> None:
        """Initialize the mock model."""
        super().__init__("mock_model", stream=False)
        self.cnt = 1
        self.fake_content_text = [
            TextBlock(type="text", text="text_response"),
        ]
        self.fake_content_tool = [
            TextBlock(type="text", text="tool_response"),
            ToolUseBlock(
                type="tool_use",
                name="generate_response",
                id="mock_id",
                input={"result": "structured_value"},
            ),
        ]

    async def __call__(
        self,
        _messages: list[dict],
        **kwargs: Any,
    ) -> ChatResponse:
        """Mock model call."""
        self.cnt += 1
        if self.cnt == 2:
            return ChatResponse(content=self.fake_content_text)
        else:
            return ChatResponse(content=self.fake_content_tool)


class MyReActAgent(ReActAgent):
    """Subclass that overrides reply, _reasoning and _acting, each calling
    super(). Used to test that hook_guard_attr prevents duplicate hook
    execution when the metaclass wraps both the child's and parent's methods
    independently."""

    async def reply(
        self,
        msg: Msg | list[Msg] | None = None,
        structured_model: Any = None,
    ) -> Msg:
        """Override reply, delegating to parent via super()."""
        return await super().reply(msg, structured_model=structured_model)

    async def _reasoning(
        self,
        tool_choice: Any = None,
    ) -> Msg:
        """Override _reasoning, delegating to parent via super()."""
        return await super()._reasoning(tool_choice=tool_choice)

    async def _acting(self, tool_call: Any) -> dict | None:
        """Override _acting, delegating to parent via super()."""
        return await super()._acting(tool_call)


async def async_pre_func_w_modifying(
    self: MyAgent,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """A pre-hook function that modifies the keyword arguments."""

    if isinstance(kwargs.get("msg"), Msg):
        kwargs["msg"].content.append(
            TextBlock(
                type="text",
                text="pre_1",
            ),
        )
    self.records.append("pre_1")
    return kwargs


async def async_pre_func_wo_modifying(
    self: MyAgent,
    kwargs: dict[str, Any],
) -> None:
    """A pre-hook function that does not modify the keyword arguments."""
    if isinstance(kwargs.get("msg"), Msg):
        kwargs["msg"].content.append(
            TextBlock(
                type="text",
                text="pre_2",
            ),
        )
    self.records.append("pre_2")


def sync_pre_func_w_modifying(
    self: MyAgent,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """A synchronous pre-hook function that does not modify the keyword
    arguments."""
    if isinstance(kwargs.get("msg"), Msg):
        kwargs["msg"].content.append(
            TextBlock(
                type="text",
                text="pre_3",
            ),
        )
    self.records.append("pre_3")
    return kwargs


def sync_pre_func_wo_modifying(
    self: MyAgent,
    kwargs: dict[str, Any],
) -> None:
    """A synchronous pre-hook function that does not modify the keyword
    arguments."""
    if isinstance(kwargs.get("msg"), Msg):
        kwargs["msg"].content.append(
            TextBlock(
                type="text",
                text="pre_4",
            ),
        )
    self.records.append("pre_4")


async def async_post_func_w_modifying(
    self: MyAgent,
    _kwargs: dict[str, Any],
    output: Any,
) -> Any:
    """A post-hook function that modifies the output."""
    if isinstance(output, Msg):
        output.content.append(
            TextBlock(
                type="text",
                text="post_1",
            ),
        )
    self.records.append("post_1")
    return output


async def async_post_func_wo_modifying(
    self: MyAgent,
    _kwargs: dict[str, Any],
    output: Any,
) -> None:
    """A post-hook function that does not modify the output."""
    if isinstance(output, Msg):
        output.content.append(
            TextBlock(
                type="text",
                text="post_2",
            ),
        )
    self.records.append("post_2")


def sync_post_func_w_modifying(
    self: MyAgent,
    _kwargs: dict[str, Any],
    output: Any,
) -> Any:
    """A synchronous post-hook function that modifies the output."""
    if isinstance(output, Msg):
        output.content.append(
            TextBlock(
                type="text",
                text="post_3",
            ),
        )
    self.records.append("post_3")
    return output


def sync_post_func_wo_modifying(
    self: MyAgent,
    _kwargs: dict[str, Any],
    output: Any,
) -> None:
    """A synchronous post-hook function that does not modify the output."""
    if isinstance(output, Msg):
        output.content.append(
            TextBlock(
                type="text",
                text="post_4",
            ),
        )
    self.records.append("post_4")


class HookTest(IsolatedAsyncioTestCase):
    """The hook test class."""

    async def asyncSetUp(self) -> None:
        """Set up the test environment."""
        self.agent = MyAgent()

    @property
    def msg(self) -> Msg:
        """Get the test message."""
        return Msg(
            "user",
            [TextBlock(type="text", text="0")],
            "user",
        )

    async def test_reply_hooks(self) -> None:
        """Test the reply hooks."""
        res = await self.agent(self.msg)
        self.assertListEqual(
            res.content,
            [
                TextBlock(type="text", text="0"),
                TextBlock(type="text", text="mark"),
            ],
        )

        # Add pre 1
        self.agent.register_instance_hook(
            "pre_reply",
            "pre_1",
            async_pre_func_w_modifying,
        )
        res = await self.agent(self.msg)
        self.assertListEqual(
            res.content,
            [
                TextBlock(type="text", text="0"),
                TextBlock(type="text", text="pre_1"),
                TextBlock(type="text", text="mark"),
            ],
        )
        self.assertListEqual(
            self.agent.records,
            ["pre_1"],
        )

        # Add pre 2
        self.agent.register_instance_hook(
            "pre_reply",
            "pre_2",
            async_pre_func_wo_modifying,
        )
        res = await self.agent(self.msg)
        self.assertListEqual(
            res.content,
            [
                TextBlock(type="text", text="0"),
                TextBlock(type="text", text="pre_1"),
                TextBlock(type="text", text="mark"),
            ],
        )
        self.assertListEqual(
            self.agent.records,
            ["pre_1", "pre_1", "pre_2"],
        )

        # Add sync pre 3
        self.agent.register_instance_hook(
            "pre_reply",
            "pre_3",
            sync_pre_func_w_modifying,
        )
        res = await self.agent(self.msg)
        self.assertListEqual(
            res.content,
            [
                TextBlock(type="text", text="0"),
                TextBlock(type="text", text="pre_1"),
                TextBlock(type="text", text="pre_3"),
                TextBlock(type="text", text="mark"),
            ],
        )
        self.assertListEqual(
            self.agent.records,
            [
                "pre_1",
                "pre_1",
                "pre_2",
                "pre_1",
                "pre_2",
                "pre_3",
            ],
        )

        # Add sync pre 4
        self.agent.register_instance_hook(
            "pre_reply",
            "pre_4",
            sync_pre_func_wo_modifying,
        )
        res = await self.agent(self.msg)
        self.assertListEqual(
            res.content,
            [
                TextBlock(type="text", text="0"),
                TextBlock(type="text", text="pre_1"),
                TextBlock(type="text", text="pre_3"),
                TextBlock(type="text", text="mark"),
            ],
        )
        self.assertListEqual(
            self.agent.records,
            [
                "pre_1",
                "pre_1",
                "pre_2",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_4",
            ],
        )

        # Add post 1
        self.agent.register_instance_hook(
            "post_reply",
            "post_1",
            async_post_func_w_modifying,
        )
        res = await self.agent(self.msg)
        self.assertListEqual(
            res.content,
            [
                TextBlock(type="text", text="0"),
                TextBlock(type="text", text="pre_1"),
                TextBlock(type="text", text="pre_3"),
                TextBlock(type="text", text="mark"),
                TextBlock(type="text", text="post_1"),
            ],
        )
        self.assertListEqual(
            self.agent.records,
            [
                "pre_1",
                "pre_1",
                "pre_2",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_4",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_4",
                "post_1",
            ],
        )

        # Add post 2
        self.agent.register_instance_hook(
            "post_reply",
            "post_2",
            async_post_func_wo_modifying,
        )
        res = await self.agent(self.msg)
        self.assertListEqual(
            res.content,
            [
                TextBlock(type="text", text="0"),
                TextBlock(type="text", text="pre_1"),
                TextBlock(type="text", text="pre_3"),
                TextBlock(type="text", text="mark"),
                TextBlock(type="text", text="post_1"),
            ],
        )
        self.assertListEqual(
            self.agent.records,
            [
                "pre_1",
                "pre_1",
                "pre_2",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_4",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_4",
                "post_1",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_4",
                "post_1",
                "post_2",
            ],
        )

        # Add sync post 3
        self.agent.register_instance_hook(
            "post_reply",
            "post_3",
            sync_post_func_w_modifying,
        )
        res = await self.agent(self.msg)
        self.assertListEqual(
            res.content,
            [
                TextBlock(type="text", text="0"),
                TextBlock(type="text", text="pre_1"),
                TextBlock(type="text", text="pre_3"),
                TextBlock(type="text", text="mark"),
                TextBlock(type="text", text="post_1"),
                TextBlock(type="text", text="post_3"),
            ],
        )
        self.assertListEqual(
            self.agent.records,
            [
                "pre_1",
                "pre_1",
                "pre_2",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_4",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_4",
                "post_1",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_4",
                "post_1",
                "post_2",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_4",
                "post_1",
                "post_2",
                "post_3",
            ],
        )

        # Add sync post 4
        self.agent.register_instance_hook(
            "post_reply",
            "post_4",
            sync_post_func_wo_modifying,
        )
        res = await self.agent(self.msg)
        self.assertListEqual(
            res.content,
            [
                TextBlock(type="text", text="0"),
                TextBlock(type="text", text="pre_1"),
                TextBlock(type="text", text="pre_3"),
                TextBlock(type="text", text="mark"),
                TextBlock(type="text", text="post_1"),
                TextBlock(type="text", text="post_3"),
            ],
        )
        self.assertListEqual(
            self.agent.records,
            [
                "pre_1",
                "pre_1",
                "pre_2",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_4",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_4",
                "post_1",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_4",
                "post_1",
                "post_2",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_4",
                "post_1",
                "post_2",
                "post_3",
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_4",
                "post_1",
                "post_2",
                "post_3",
                "post_4",
            ],
        )

        self.agent.clear_instance_hooks()
        self.agent.records.clear()
        res = await self.agent(self.msg)
        self.assertListEqual(
            res.content,
            [
                TextBlock(type="text", text="0"),
                TextBlock(type="text", text="mark"),
            ],
        )
        self.assertListEqual(
            self.agent.records,
            [],
        )

    async def test_print_hooks(self) -> None:
        """Test the print hooks."""
        self.agent.register_instance_hook(
            "pre_print",
            "pre_1",
            async_pre_func_w_modifying,
        )
        self.agent.register_instance_hook(
            "pre_print",
            "pre_2",
            async_pre_func_wo_modifying,
        )
        self.agent.register_instance_hook(
            "pre_print",
            "pre_3",
            sync_pre_func_w_modifying,
        )
        self.agent.register_instance_hook(
            "pre_print",
            "pre_4",
            sync_pre_func_wo_modifying,
        )
        await self.agent(self.msg)
        self.assertListEqual(
            self.agent.records,
            [
                "pre_1",
                "pre_2",
                "pre_3",
                "pre_4",
            ],
        )

    async def test_observe_hooks(self) -> None:
        """Test the observe hooks."""
        self.agent.register_instance_hook(
            "pre_observe",
            "pre_1",
            async_pre_func_w_modifying,
        )
        self.agent.register_instance_hook(
            "pre_observe",
            "pre_2",
            async_pre_func_wo_modifying,
        )
        await self.agent.observe(self.msg)
        self.assertEqual(len(self.agent.memory), 1)
        self.assertListEqual(
            self.agent.records,
            [
                "pre_1",
                "pre_2",
            ],
        )
        self.assertListEqual(
            self.agent.memory[0].content,
            [
                TextBlock(type="text", text="0"),
                TextBlock(type="text", text="pre_1"),
            ],
        )

        self.agent.register_instance_hook(
            "post_observe",
            "post_1",
            async_post_func_w_modifying,
        )
        self.agent.register_instance_hook(
            "post_observe",
            "post_2",
            async_post_func_wo_modifying,
        )
        await self.agent.observe(self.msg)
        self.assertEqual(
            len(self.agent.memory),
            2,
        )
        self.assertListEqual(
            self.agent.records,
            ["pre_1", "pre_2", "pre_1", "pre_2", "post_1", "post_2"],
        )
        self.assertListEqual(
            self.agent.memory[1].content,
            [
                TextBlock(type="text", text="0"),
                TextBlock(type="text", text="pre_1"),
            ],
        )

    # TODO: The studio requires the hook inherited from AgentBase, we will
    #  solving this problem later.
    # async def test_instance_and_class_hooks(self) -> None:
    #     """Test instance and class hooks."""
    #     AgentBase.register_class_hook(
    #         "pre_reply",
    #         "pre_3",
    #         sync_pre_func_w_modifying,
    #     )
    #     self.agent.register_instance_hook(
    #         "pre_reply",
    #         "pre_1",
    #         async_pre_func_w_modifying,
    #     )
    #     res = await self.agent(self.msg)
    #     self.assertListEqual(
    #         res.content,
    #         [
    #             TextBlock(type="text", text="0"),
    #             TextBlock(type="text", text="pre_1"),
    #             TextBlock(type="text", text="mark"),
    #         ],
    #     )
    #
    #     # remove hook
    #     AgentBase.remove_class_hook("pre_reply", "pre_3")
    #     res = await self.agent(self.msg)
    #     self.assertListEqual(
    #         res.content,
    #         [
    #             TextBlock(type="text", text="0"),
    #             TextBlock(type="text", text="pre_1"),
    #             TextBlock(type="text", text="mark"),
    #         ],
    #     )
    #
    # async def test_class_hook_inheritance_isolation(self) -> None:
    #     """Test that class hooks are isolated between parent and child
    #     classes."""
    #
    #     # Register different hooks on different classes
    #     MyAgent.register_class_hook(
    #         "pre_reply",
    #         "parent_hook",
    #         sync_pre_func_w_modifying,  # adds "pre_3" to content
    #     )
    #
    #     ChildAgent.register_class_hook(
    #         "pre_reply",
    #         "child_hook",
    #         async_pre_func_w_modifying,  # adds "pre_1" to content
    #     )
    #
    #     GrandChildAgent.register_class_hook(
    #         "pre_reply",
    #         "grandchild_hook",
    #         sync_pre_func_wo_modifying,  # adds "pre_4" to content
    #     )
    #
    #     # Create instances of each class
    #     parent_agent = MyAgent()
    #     child_agent = ChildAgent()
    #     grandchild_agent = GrandChildAgent()
    #
    #     # Test parent agent - should only execute parent hook
    #     res = await parent_agent(self.msg)
    #     self.assertListEqual(
    #         res.content,
    #         [
    #             TextBlock(type="text", text="0"),
    #             TextBlock(type="text", text="pre_3"),  # only parent hook
    #             TextBlock(type="text", text="mark"),
    #         ],
    #     )
    #     self.assertListEqual(parent_agent.records, ["pre_3"])
    #
    #     # Test child agent - should only execute child hook
    #     res = await child_agent(self.msg)
    #     self.assertListEqual(
    #         res.content,
    #         [
    #             TextBlock(type="text", text="0"),
    #             TextBlock(type="text", text="pre_1"),  # only child hook
    #             TextBlock(type="text", text="mark"),
    #         ],
    #     )
    #     self.assertListEqual(child_agent.records, ["pre_1"])
    #
    #     # Test grandchild agent - should only execute grandchild hook
    #     res = await grandchild_agent(self.msg)
    #     self.assertListEqual(
    #         res.content,
    #         [
    #             TextBlock(type="text", text="0"),
    #             TextBlock(type="text", text="mark"),
    #             # pre_4 doesn't modify content
    #         ],
    #     )
    #     self.assertListEqual(grandchild_agent.records, ["pre_4"])
    #
    # async def test_multiple_inheritance_hook_isolation(self) -> None:
    #     """Test hook isolation in multiple inheritance scenarios."""
    #
    #     # Register hooks on different classes
    #     AgentA.register_class_hook(
    #         "pre_reply",
    #         "hook_a",
    #         sync_pre_func_w_modifying,  # adds "pre_3"
    #     )
    #
    #     AgentB.register_class_hook(
    #         "pre_reply",
    #         "hook_b",
    #         async_pre_func_w_modifying,  # adds "pre_1"
    #     )
    #
    #     AgentC.register_class_hook(
    #         "pre_reply",
    #         "hook_c",
    #         sync_pre_func_wo_modifying,  # adds "pre_4" (no content change)
    #     )  # Create instances
    #     agent_a = AgentA()
    #     agent_b = AgentB()
    #     agent_c = AgentC()
    #
    #     # Test AgentA - should only execute hook_a
    #     res = await agent_a(self.msg)
    #     self.assertListEqual(
    #         res.content,
    #         [
    #             TextBlock(type="text", text="0"),
    #             TextBlock(type="text", text="pre_3"),
    #             TextBlock(type="text", text="mark"),
    #         ],
    #     )
    #     self.assertListEqual(agent_a.records, ["pre_3"])
    #
    #     # Test AgentB - should only execute hook_b
    #     res = await agent_b(self.msg)
    #     self.assertListEqual(
    #         res.content,
    #         [
    #             TextBlock(type="text", text="0"),
    #             TextBlock(type="text", text="pre_1"),
    #             TextBlock(type="text", text="mark"),
    #         ],
    #     )
    #     self.assertListEqual(agent_b.records, ["pre_1"])
    #
    #     # Test AgentC - should only execute hook_c
    #     res = await agent_c(self.msg)
    #     self.assertListEqual(
    #         res.content,
    #         [
    #             TextBlock(type="text", text="0"),
    #             TextBlock(type="text", text="mark"),
    #             # pre_4 doesn't modify content
    #         ],
    #     )
    #     self.assertListEqual(agent_c.records, ["pre_4"])

    async def asyncTearDown(self) -> None:
        """Tear down the test environment."""
        self.agent.clear_instance_hooks()
        MyAgent.clear_class_hooks()

        ChildAgent.clear_class_hooks()
        GrandChildAgent.clear_class_hooks()

        AgentA.clear_class_hooks()
        AgentB.clear_class_hooks()
        AgentC.clear_class_hooks()


class HookGuardTest(IsolatedAsyncioTestCase):
    """Tests for the hook_guard_attr re-entrancy prevention mechanism.

    When a child class overrides a hook-wrapped method (reply, observe,
    _reasoning, _acting, etc.) and calls super().method(), the metaclass
    wraps both the child's and the parent's method independently. Without
    the guard, hooks would fire once per wrapper in the call chain. The
    hook_guard_attr ensures hooks only execute in the outermost wrapper.

    Covers both AgentBase-level (reply, observe) and ReActAgent-level
    (reply, _reasoning, _acting) scenarios.
    """

    @property
    def msg(self) -> Msg:
        """Get the test message."""
        return Msg(
            "user",
            [TextBlock(type="text", text="0")],
            "user",
        )

    def _make_react_agent(self) -> MyReActAgent:
        """Create a MyReActAgent with a fresh mock model."""
        return MyReActAgent(
            name="TestAgent",
            sys_prompt="You are a helpful assistant.",
            model=MockModel(),
            formatter=DashScopeChatFormatter(),
            memory=InMemoryMemory(),
            toolkit=Toolkit(),
        )

    # ---- AgentBase-level tests ----

    async def test_reply_hooks_execute_once_with_override(self) -> None:
        """Pre and post reply hooks should each execute exactly once when
        a child class overrides reply() and calls super().reply()."""
        agent = ChildAgentWithReplyOverride()
        pre_count = 0
        post_count = 0

        async def counting_pre_hook(
            _self: Any,
            _kwargs: dict[str, Any],
        ) -> None:
            nonlocal pre_count
            pre_count += 1

        async def counting_post_hook(
            _self: Any,
            _kwargs: dict[str, Any],
            _output: Any,
        ) -> None:
            nonlocal post_count
            post_count += 1

        agent.register_instance_hook(
            "pre_reply",
            "counter_pre",
            counting_pre_hook,
        )
        agent.register_instance_hook(
            "post_reply",
            "counter_post",
            counting_post_hook,
        )

        await agent(self.msg)
        self.assertEqual(pre_count, 1)
        self.assertEqual(post_count, 1)

    async def test_observe_hooks_execute_once_with_override(self) -> None:
        """Observe hooks should execute exactly once when a child class
        overrides observe() and calls super().observe()."""
        agent = ChildAgentWithObserveOverride()
        pre_count = 0

        async def counting_pre_hook(
            _self: Any,
            _kwargs: dict[str, Any],
        ) -> None:
            nonlocal pre_count
            pre_count += 1

        agent.register_instance_hook(
            "pre_observe",
            "counter",
            counting_pre_hook,
        )

        await agent.observe(self.msg)
        self.assertEqual(pre_count, 1)

    async def test_deep_inheritance_hooks_execute_once(self) -> None:
        """Hooks should execute exactly once even with a 3-level override
        chain (GrandChild -> Child -> MyAgent), each overriding reply and
        calling super()."""
        agent = GrandChildAgentWithReplyOverride()
        pre_count = 0

        async def counting_pre_hook(
            _self: Any,
            _kwargs: dict[str, Any],
        ) -> None:
            nonlocal pre_count
            pre_count += 1

        agent.register_instance_hook(
            "pre_reply",
            "counter",
            counting_pre_hook,
        )

        await agent(self.msg)
        self.assertEqual(pre_count, 1)

    async def test_hook_guard_cleared_after_exception(self) -> None:
        """The guard flag should be properly cleaned up when the wrapped
        method raises an exception, allowing hooks to work on retry."""

        class FailingAgent(MyAgent):
            """Agent whose reply always raises."""

            async def reply(self, msg: Msg) -> Msg:
                raise RuntimeError("intentional failure")

        class ChildOfFailing(FailingAgent):
            """Child that overrides reply and calls super()."""

            async def reply(self, msg: Msg) -> Msg:
                return await super().reply(msg)

        agent = ChildOfFailing()
        pre_count = 0

        async def counting_pre_hook(
            _self: Any,
            _kwargs: dict[str, Any],
        ) -> None:
            nonlocal pre_count
            pre_count += 1

        agent.register_instance_hook(
            "pre_reply",
            "counter",
            counting_pre_hook,
        )

        with self.assertRaises(RuntimeError):
            await agent(self.msg)
        self.assertEqual(pre_count, 1)
        self.assertFalse(
            getattr(agent, "_hook_running_reply", False),
            "Guard flag should be cleared after exception",
        )

        # Hooks should still work on subsequent calls
        pre_count = 0
        with self.assertRaises(RuntimeError):
            await agent(self.msg)
        self.assertEqual(pre_count, 1)

    # ---- ReActAgent-level tests ----

    async def test_react_reply_hooks_execute_once_with_override(
        self,
    ) -> None:
        """ReActAgent reply hooks should execute exactly once when
        a subclass overrides reply() and calls super().reply()."""
        agent = self._make_react_agent()
        pre_count = 0
        post_count = 0

        async def counting_pre(_self: Any, _kwargs: Any) -> None:
            nonlocal pre_count
            pre_count += 1

        async def counting_post(
            _self: Any,
            _kwargs: Any,
            _output: Any,
        ) -> None:
            nonlocal post_count
            post_count += 1

        agent.register_instance_hook("pre_reply", "counter", counting_pre)
        agent.register_instance_hook("post_reply", "counter", counting_post)

        await agent()
        self.assertEqual(pre_count, 1)
        self.assertEqual(post_count, 1)

    async def test_react_reasoning_hooks_execute_once_with_override(
        self,
    ) -> None:
        """ReActAgent reasoning hooks should execute exactly once when
        a subclass overrides _reasoning() and calls
        super()._reasoning()."""
        agent = self._make_react_agent()
        pre_count = 0
        post_count = 0

        async def counting_pre(_self: Any, _kwargs: Any) -> None:
            nonlocal pre_count
            pre_count += 1

        async def counting_post(
            _self: Any,
            _kwargs: Any,
            _output: Any,
        ) -> None:
            nonlocal post_count
            post_count += 1

        agent.register_instance_hook(
            "pre_reasoning",
            "counter",
            counting_pre,
        )
        agent.register_instance_hook(
            "post_reasoning",
            "counter",
            counting_post,
        )

        await agent()
        self.assertEqual(pre_count, 1)
        self.assertEqual(post_count, 1)

    async def test_react_acting_hooks_execute_once_with_override(
        self,
    ) -> None:
        """ReActAgent acting hooks should execute exactly once when
        a subclass overrides _acting() and calls super()._acting()."""
        agent = self._make_react_agent()
        pre_count = 0
        post_count = 0

        async def counting_pre(_self: Any, _kwargs: Any) -> None:
            nonlocal pre_count
            pre_count += 1

        async def counting_post(
            _self: Any,
            _kwargs: Any,
            _output: Any,
        ) -> None:
            nonlocal post_count
            post_count += 1

        agent.register_instance_hook(
            "pre_acting",
            "counter",
            counting_pre,
        )
        agent.register_instance_hook(
            "post_acting",
            "counter",
            counting_post,
        )

        class TestStructuredModel(BaseModel):
            """Test structured model."""

            result: str = Field(description="Test result field.")

        await agent(structured_model=TestStructuredModel)
        self.assertEqual(pre_count, 1)
        self.assertEqual(post_count, 1)

    async def asyncTearDown(self) -> None:
        """Tear down the test environment."""
        ChildAgentWithReplyOverride.clear_class_hooks()
        ChildAgentWithObserveOverride.clear_class_hooks()
        GrandChildAgentWithReplyOverride.clear_class_hooks()
        MyReActAgent.clear_class_hooks()
