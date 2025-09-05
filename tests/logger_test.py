# -*- coding: utf-8 -*-
""" Unit test for logger chat"""
import json
import os
import shutil
import time
import unittest
import datetime
import uuid

from unittest.mock import patch
from loguru import logger

from agentscope.logging import setup_logger
from agentscope.manager import ASManager
from agentscope.message import Msg


class LoggerTest(unittest.TestCase):
    """
    Unit test for logger.
    """

    def setUp(self) -> None:
        """Setup for unit test."""
        self.run_dir = "./logger_runs/"

    def test_logger_chat(self) -> None:
        """Logger chat."""

        setup_logger(self.run_dir, level="INFO")

        msg1 = Msg("abc", "def", "assistant")
        msg1.id = 1
        msg1.timestamp = 1

        # url
        msg2 = Msg("abc", "def", "assistant", url="https://xxx.png")
        msg2.id = 2
        msg2.timestamp = 2

        # urls
        msg3 = Msg(
            "abc",
            "def",
            "assistant",
            url=["https://yyy.png", "https://xxx.png"],
        )
        msg3.id = 3
        msg3.timestamp = 3

        # html labels
        msg4 = Msg("Bob", "<red>abc</div", "system")
        msg4.id = 4
        msg4.timestamp = 4

        # cover test msg init print logger.info and logger.chat
        fix_uuid_str = "4ebf0fd2-6790-4f99-98e4-5438bf9ca4ae"
        fix_uuid_obj = uuid.UUID(fix_uuid_str)
        fix_uuid_hex = fix_uuid_obj.hex
        fixed_time = datetime.datetime(2025, 9, 1)  # 固定时间
        with patch("datetime.datetime") as mock_datetime, patch(
            "uuid.uuid4",
        ) as mock_uuid:
            mock_datetime.now.return_value = fixed_time
            mock_uuid.return_value = fix_uuid_obj
            msg5 = Msg(
                "Villege",
                "test for msg init echo logger.chat",
                "system",
                echo=True,
            )
            msg6 = Msg(
                "Villege",
                "test for msg init echo logger.chat",
                "system",
                echo=False,
            )

        self.assertEqual(msg5.id, fix_uuid_hex)
        self.assertEqual(
            msg5.timestamp,
            fixed_time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        self.assertEqual(msg6.id, fix_uuid_hex)
        self.assertEqual(
            msg6.timestamp,
            fixed_time.strftime("%Y-%m-%d %H:%M:%S"),
        )

        logger.chat(msg1)
        logger.chat(msg2)
        logger.chat(msg3)
        logger.chat(msg4)
        logger.chat(msg5)
        logger.chat(msg6)

        # To avoid that logging is not finished before the file is read
        time.sleep(2)

        with open(
            os.path.join(self.run_dir, "logging.chat"),
            "r",
            encoding="utf-8",
        ) as file:
            lines = file.readlines()
        print(lines)
        for line, ground_truth in zip(
            lines,
            [
                '{"__module__": "agentscope.message.msg", '
                '"__name__": "Msg", "id": "4ebf0fd267904f9998e45438bf9ca4ae", '
                '"name": "Villege", "role": "system", '
                '"content": "test for msg init echo logger.chat", '
                '"metadata": null, "timestamp": "2025-09-01 00:00:00"}\n',
                '{"__module__": "agentscope.message.msg", '
                '"__name__": "Msg", "id": 1, "name": "abc", '
                '"role": "assistant", '
                '"content": "def", '
                '"metadata": null, "timestamp": 1}\n',
                '{"__module__": "agentscope.message.msg", '
                '"__name__": "Msg", "id": 2, "name": "abc", '
                '"role": "assistant", '
                '"content": [{"type": "text", "text": "def"}, '
                '{"type": "image", "url": "https://xxx.png"}], '
                '"metadata": null, "timestamp": 2}\n',
                '{"__module__": "agentscope.message.msg", '
                '"__name__": "Msg", "id": 3, "name": "abc", '
                '"role": "assistant", '
                '"content": [{"type": "text", "text": "def"}, '
                '{"type": "image", "url": "https://yyy.png"}, '
                '{"type": "image", "url": "https://xxx.png"}], '
                '"metadata": null, "timestamp": 3}\n',
                '{"__module__": "agentscope.message.msg", '
                '"__name__": "Msg", "id": 4, "name": "Bob", '
                '"role": "system", '
                '"content": "<red>abc</div", "metadata": null, '
                '"timestamp": 4}\n',
                '{"__module__": "agentscope.message.msg", '
                '"__name__": "Msg", "id": "4ebf0fd267904f9998e45438bf9ca4ae", '
                '"name": "Villege", "role": "system", '
                '"content": "test for msg init echo logger.chat", '
                '"metadata": null, "timestamp": "2025-09-01 00:00:00"}\n',
                '{"__module__": "agentscope.message.msg", '
                '"__name__": "Msg", "id": "4ebf0fd267904f9998e45438bf9ca4ae", '
                '"name": "Villege", "role": "system", '
                '"content": "test for msg init echo logger.chat", '
                '"metadata": null, "timestamp": "2025-09-01 00:00:00"}\n',
            ],
        ):
            self.assertDictEqual(json.loads(line), json.loads(ground_truth))

    def tearDown(self) -> None:
        """Tear down for LoggerTest."""
        ASManager.get_instance().flush()
        if os.path.exists(self.run_dir):
            shutil.rmtree(self.run_dir)


if __name__ == "__main__":
    unittest.main()
