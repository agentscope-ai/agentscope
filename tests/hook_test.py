# -*- coding: utf-8 -*-
# pylint: disable=unused-argument, protected-access
"""Unittests for agent hooks."""
import unittest
from typing import Optional, Union, Tuple, Any, Dict
from unittest.mock import patch, MagicMock

from agentscope.agent import AgentBase
from agentscope.memory import TemporaryMemory
from agentscope.message import Msg

# 全局变量用于 post_observe 钩子测试
cnt_post = 0


class _TestAgent(AgentBase):
    """A test agent."""

    def __init__(self) -> None:
        """Initialize the test agent."""
        super().__init__()
        self.name = "Friday"
        self.memory = TemporaryMemory()
        self.cnt = 0

    async def reply(self, *args: Any, **kwargs: Any) -> Msg:
        """异步 reply 方法"""
        x = kwargs.get("x")
        if x is not None:
            await self.print(x)
        return x

    async def observe(self, msg: Union[Msg, list[Msg], None]) -> None:
        """异步 observe 方法"""
        if msg is not None:
            if self._disable_console_output:
                return
            await self.print(msg)


class AgentHooksTest(unittest.IsolatedAsyncioTestCase):
    """Unittests for agent hooks."""

    async def asyncSetUp(self) -> None:
        """Set up the test."""
        await super().asyncSetUp()
        self.agent = _TestAgent()
        self.agent2 = _TestAgent()

    async def asyncTearDown(self) -> None:
        """异步清理"""
        self.agent.clear_instance_hooks("pre_reply")
        self.agent.clear_instance_hooks("post_reply")
        self.agent.clear_instance_hooks("pre_observe")
        self.agent.clear_instance_hooks("post_observe")
        self.agent.clear_instance_hooks("pre_print")
        self.agent.clear_instance_hooks("post_print")

        self.agent2.clear_instance_hooks("pre_reply")
        self.agent2.clear_instance_hooks("post_reply")
        self.agent2.clear_instance_hooks("pre_observe")
        self.agent2.clear_instance_hooks("post_observe")
        self.agent2.clear_instance_hooks("pre_print")
        self.agent2.clear_instance_hooks("post_print")

        AgentBase.clear_class_hooks("pre_reply")
        AgentBase.clear_class_hooks("post_reply")
        AgentBase.clear_class_hooks("pre_observe")
        AgentBase.clear_class_hooks("post_observe")
        AgentBase.clear_class_hooks("pre_print")
        AgentBase.clear_class_hooks("post_print")

        # 清空 memory（如果需要）
        if hasattr(self.agent, "memory"):
            self.agent.memory.clear()
        if hasattr(self.agent2, "memory"):
            self.agent2.memory.clear()

    async def test_reply_hook(self) -> None:
        """Test the reply hook."""

        async def pre_reply_hook(
            self: AgentBase,
            kwargs: Dict[str, Any],
        ) -> Optional[Dict[str, Any]]:
            """Pre-reply hook."""
            if "x" in kwargs and isinstance(kwargs["x"], Msg):
                kwargs["x"].content = "-1, " + kwargs["x"].content
                return kwargs
            return None

        def pre_reply_hook_without_change(
            self: AgentBase,
            kwargs: Dict[str, Any],
        ) -> None:
            """Pre-reply hook without returning, so that the message is not
            changed."""
            if "x" in kwargs and isinstance(kwargs["x"], Msg):
                kwargs["x"].content = "-2, " + kwargs["x"].content

        async def post_reply_hook(
            self: AgentBase,
            kwargs: Dict[str, Any],
            output: Msg,
        ) -> Optional[Msg]:
            """Post-reply hook."""
            output.content += ", 1"
            return output

        msg_test = Msg("user", "0", "user")

        # Test without hooks
        x = await self.agent(x=msg_test)
        self.assertEqual("0", x.content)

        # Test with one pre hook
        self.agent.register_instance_hook(
            "pre_reply",
            "first_pre_hook",
            pre_reply_hook,
        )
        x = await self.agent(msg_test)
        self.assertEqual("-1, 0", x.content)

        # Test with one pre and one post hook
        self.agent.register_instance_hook(
            "post_reply",
            "first_post_hook",
            post_reply_hook,
        )
        x = await self.agent(msg_test)
        self.assertEqual("-1, 0, 1", x.content)

        # Test with two pre hooks and one post hook
        self.agent.register_instance_hook(
            "pre_reply",
            "second_pre_hook",
            pre_reply_hook,
        )
        x = await self.agent(msg_test)
        self.assertEqual("-1, -1, 0, 1", x.content)

        # Test removing one pre hook
        self.agent.remove_instance_hook("pre_reply", "first_pre_hook")
        x = await self.agent(msg_test)
        self.assertEqual("-1, 0, 1", x.content)

        # Test removing one post hook
        self.agent.remove_instance_hook("post_reply", "first_post_hook")
        x = await self.agent(msg_test)
        self.assertEqual("-1, 0", x.content)

        # Test clearing all pre hooks
        self.agent.clear_instance_hooks("pre_reply")
        x = await self.agent(msg_test)
        self.assertEqual("0", x.content)

        # Test with three pre hooks, change -> not change -> change
        self.agent.register_hook("pre_reply", "first_pre_hook", pre_reply_hook)
        self.agent.register_hook(
            "pre_reply",
            "second_pre_hook",
            pre_reply_hook_without_change,
        )
        self.agent.register_hook("pre_reply", "third_pre_hook", pre_reply_hook)
        x = await self.agent(msg_test)
        self.assertEqual("-1, -1, 0", x.content)

        # Test with no input
        await self.agent()

    @patch("agentscope.agent._agent.log_msg")
    async def test_speak_hook(self, mock_log_msg: MagicMock) -> None:
        """Test the speak hook."""

        async def pre_print_hook_change(
            self: AgentBase,
            kwargs: Dict[str, Any],
        ) -> Optional[Dict[str, Any]]:
            """Pre-print hook."""
            msg = kwargs.get("msg")
            if msg and isinstance(msg, Msg):
                msg.content = "-1, " + msg.content
                return kwargs
            return None

        async def post_print_hook(
            self: AgentBase,
            kwargs: Dict[str, Any],
            output: Any,
        ) -> None:
            """Post-print hook."""
            if not hasattr(self, "cnt"):
                self.cnt = 0
            self.cnt += 1

        def pre_print_hook_change2(
            self: AgentBase,
            msg: Msg,
            stream: bool,
            last: bool,
        ) -> Msg:
            """Pre-speak hook."""
            msg.content = "-2, " + msg.content
            return msg

        def pre_speak_hook_without_change(
            self: AgentBase,
            msg: Msg,
            stream: bool,
            last: bool,
        ) -> None:
            """Pre-speak hook."""
            return None

        def post_speak_hook(self: AgentBase) -> None:
            """Post-speak hook."""
            if not hasattr(self, "cnt"):
                self.cnt = 0
            self.cnt += 1

        self.agent.register_instance_hook(
            "pre_speak",
            "first_pre_hook",
            pre_print_hook_change,
        )
        self.agent.register_instance_hook(
            "pre_speak",
            "second_pre_hook",
            pre_print_hook_change2,
        )
        self.agent.register_instance_hook(
            "pre_speak",
            "third_pre_hook",
            pre_speak_hook_without_change,
        )
        self.agent.register_instance_hook(
            "post_speak",
            "first_post_hook",
            post_speak_hook,
        )
        self.agent.register_instance_hook(
            "post_speak",
            "second_post_hook",
            post_speak_hook,
        )

        test_msg = Msg("user", "0", "user")

        x = await self.agent(test_msg)
        # The speak function shouldn't affect the reply message
        self.assertEqual(x.content, "0")

        self.assertEqual("-2, -1, 0", mock_log_msg.call_args[0][0].content)
        self.assertEqual(2, self.agent.cnt)

    async def test_observe_hook(self) -> None:
        """Test the observe hook."""

        def pre_observe_hook_change(self: AgentBase, msg: Msg) -> Msg:
            """Pre-observe hook with returning, where the message will be
            changed."""
            msg.content = "-1, " + msg.content
            return msg

        def pre_observe_hook_not_change(self: AgentBase, msg: Msg) -> None:
            """Pre-observe hook without returning, so that the message is not
            changed."""
            msg.content = "-2, " + msg.content

        global cnt_post
        cnt_post = 0

        async def post_observe_hook(
            self: AgentBase,
            kwargs: Dict[str, Any],
            output: Any,
        ) -> None:
            """Post-observe hook."""
            global cnt_post
            cnt_post += 1

        msg_test = Msg("user", "0", "user")
        msg_test2 = Msg("user", "0", "user")

        await self.agent.observe(msg_test)
        memory = self.agent.memory.get_memory()
        self.assertEqual("0", self.agent.memory.get_memory()[0].content)

        self.agent.register_instance_hook(
            "pre_observe",
            "first_pre_hook",
            pre_observe_hook_change,
        )
        self.agent.register_instance_hook(
            "pre_observe",
            "second_pre_hook",
            pre_observe_hook_not_change,
        )
        self.agent.register_instance_hook(
            "post_observe",
            "first_post_hook",
            post_observe_hook,
        )

        await self.agent.observe(msg_test2)
        # The reply should not be affected due to deep copy
        # The memory should be affected
        print(self.agent.memory.get_memory())
        self.assertEqual(
            "-1, 0",
            self.agent.memory.get_memory()[1].content,
        )
        self.assertEqual(1, cnt_post)

    async def test_class_and_object_pre_reply_hook(self) -> None:
        """Test the class and object hook."""

        def pre_reply_hook_1(
            self: AgentBase,
            args: Tuple[Any, ...],
            kwargs: Dict[str, Any],
        ) -> Union[Tuple[Tuple[Msg, ...], Dict[str, Any]], None]:
            """Pre-reply hook."""
            args[0].content = "-1, " + args[0].content
            return args, kwargs

        def pre_reply_hook_2(
            self: AgentBase,
            args: Tuple[Any, ...],
            kwargs: Dict[str, Any],
        ) -> Union[Tuple[Tuple[Msg, ...], Dict[str, Any]], None]:
            """Pre-reply hook."""
            args[0].content = "-2, " + args[0].content
            return args, kwargs

        AgentBase.register_class_hook(
            "pre_reply",
            "first_hook",
            pre_reply_hook_1,
        )

        self.assertListEqual(
            list(self.agent._class_pre_reply_hooks.keys()),
            ["first_hook"],
        )
        self.assertListEqual(
            list(self.agent2._class_pre_reply_hooks.keys()),
            ["first_hook"],
        )
        AgentBase.clear_class_hooks()

        self.agent.register_hook(
            "pre_reply",
            "second_hook",
            pre_reply_hook_1,
        )
        self.assertListEqual(
            list(self.agent._instance_pre_reply_hooks.keys()),
            ["second_hook"],
        )
        self.assertListEqual(
            list(self.agent2._class_pre_reply_hooks.keys()),
            [],
        )

        AgentBase.register_class_hook(
            "pre_reply",
            "third_hook",
            pre_reply_hook_2,
        )

        msg_test = Msg("user", "0", "user")

        res = await self.agent(msg_test)
        self.assertEqual(res.content, "-2, -1, 0")

        res = await self.agent2(msg_test)
        self.assertEqual(res.content, "-2, 0")

    async def test_class_and_object_post_reply_hook(self) -> None:
        """Test the class and object hook."""

        def post_reply_hook_1(
            self: AgentBase,
            args: Tuple[Any, ...],
            kwargs: Dict[str, Any],
            output: Msg,
        ) -> Union[None, Msg]:
            """Post-reply hook."""
            return Msg("assistant", output.content + ", 1", "assistant")

        def post_reply_hook_2(
            self: AgentBase,
            args: Tuple[Any, ...],
            kwargs: Dict[str, Any],
            output: Msg,
        ) -> Union[None, Msg]:
            """Post-reply hook."""
            return Msg("assistant", output.content + ", 2", "assistant")

        AgentBase.register_class_hook(
            "post_reply",
            "first_hook",
            post_reply_hook_1,
        )

        self.assertListEqual(
            list(self.agent._class_post_reply_hooks.keys()),
            ["first_hook"],
        )
        self.assertListEqual(
            list(self.agent2._class_post_reply_hooks.keys()),
            ["first_hook"],
        )
        AgentBase.clear_class_hooks()

        self.agent.register_hook(
            "post_reply",
            "second_hook",
            post_reply_hook_1,
        )
        self.assertListEqual(
            list(self.agent._instance_post_reply_hooks.keys()),
            ["second_hook"],
        )
        self.assertListEqual(
            list(self.agent2._class_post_reply_hooks.keys()),
            [],
        )

        AgentBase.register_class_hook(
            "post_reply",
            "third_hook",
            post_reply_hook_2,
        )

        msg_test = Msg("user", "0", "user")

        res = await self.agent(msg_test)
        self.assertEqual(res.content, "0, 1, 2")

        res = await self.agent2(msg_test)
        self.assertEqual(res.content, "0, 2")

    async def test_class_and_object_pre_observe_hook(self) -> None:
        """Test the class and object hook."""

        def pre_observe_hook_1(self: AgentBase, x: Msg) -> Msg:
            """Pre-observe hook."""
            return Msg("assistant", "-1, " + x.content, "assistant")

        def pre_observe_hook_2(self: AgentBase, x: Msg) -> Msg:
            """Pre-observe hook."""
            return Msg("assistant", "-2, " + x.content, "assistant")

        AgentBase.register_class_hook(
            "pre_observe",
            "first_hook",
            pre_observe_hook_1,
        )

        self.assertListEqual(
            list(self.agent._class_pre_observe_hooks.keys()),
            ["first_hook"],
        )
        self.assertListEqual(
            list(self.agent2._class_pre_observe_hooks.keys()),
            ["first_hook"],
        )
        AgentBase.clear_class_hooks()

        self.agent.register_hook(
            "pre_observe",
            "second_hook",
            pre_observe_hook_1,
        )
        self.assertListEqual(
            list(self.agent._instance_pre_observe_hooks.keys()),
            ["second_hook"],
        )
        self.assertListEqual(
            list(self.agent2._class_pre_observe_hooks.keys()),
            [],
        )

        AgentBase.register_class_hook(
            "pre_observe",
            "third_hook",
            pre_observe_hook_2,
        )

        msg_test = Msg("user", "0", "user")

        await self.agent.observe(msg_test)
        self.assertEqual(
            "-2, -1, 0",
            self.agent.memory.get_memory()[0].content,
        )

        await self.agent2.observe(msg_test)
        self.assertEqual(
            "-2, 0",
            self.agent2.memory.get_memory()[0].content,
        )

    @patch("agentscope.agent._agent.log_msg")
    def test_class_and_object_pre_speak_hook(
        self,
        mock_log_msg: MagicMock,
    ) -> None:
        """Test the class and object hook."""

        def pre_speak_hook_change(
            self: AgentBase,
            msg: Msg,
            stream: bool,
            last: bool,
        ) -> Msg:
            """Pre-speak hook."""
            msg.content = "-1, " + msg.content
            return msg

        def pre_speak_hook_change2(
            self: AgentBase,
            msg: Msg,
            stream: bool,
            last: bool,
        ) -> Msg:
            """Pre-speak hook."""
            msg.content = "-2, " + msg.content
            return msg

        AgentBase.register_class_hook(
            "pre_speak",
            "first_hook",
            pre_speak_hook_change,
        )

        self.assertListEqual(
            list(self.agent._class_pre_print_hooks.keys()),
            ["first_hook"],
        )
        self.assertListEqual(
            list(self.agent2._class_pre_print_hooks.keys()),
            ["first_hook"],
        )

        self.agent.register_hook(
            "pre_speak",
            "first_obj_hook",
            pre_speak_hook_change2,
        )

        msg_test = Msg("user", "0", "user")

        self.agent(msg_test)
        self.assertEqual("-1, -2, 0", mock_log_msg.call_args[0][0].content)

        self.agent2(msg_test)
        self.assertEqual("-1, 0", mock_log_msg.call_args[0][0].content)

    def tearDown(self) -> None:
        """Tear down the test."""
        self.agent.clear_all_obj_hooks()
        self.agent2.clear_all_obj_hooks()

        AgentBase.clear_class_hooks()
        self.agent.memory.clear()
