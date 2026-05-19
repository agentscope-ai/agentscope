# -*- coding: utf-8 -*-
"""Topic filter for message routing in MsgHub."""

from functools import lru_cache
from fnmatch import fnmatch
from typing import List, Optional, Tuple


class TopicFilter:
    """Topic filter for message routing based on fnmatch pattern matching.

    This filter supports:
    - Exact matching: "task.create" matches "task.create"
    - Wildcard matching: "task.*" matches "task.create", "task.finish"
    - Multiple patterns: ["task.*", "notify"] matches either pattern

    Performance: Uses lru_cache to avoid repeated fnmatch calls on the same
    topic combinations. Cache key is based on sorted tuples for stability.

    Matching rules:
    - If subscriber has no topics (None or empty), receives all messages
    - If message has no topics (None or empty), received by all subscribers
    - Otherwise, check if any message topic matches any subscriber topic pattern
    """

    @staticmethod
    @lru_cache(maxsize=1024)
    def _matches_cached(
        message_topics_tuple: Optional[Tuple[str, ...]],
        subscriber_topics_tuple: Optional[Tuple[str, ...]],
    ) -> bool:
        """Internal cached matching function.

        Uses tuples instead of lists for hashability.
        """
        if subscriber_topics_tuple is None or len(subscriber_topics_tuple) == 0:
            return True

        if message_topics_tuple is None or len(message_topics_tuple) == 0:
            return True

        for msg_topic in message_topics_tuple:
            for sub_pattern in subscriber_topics_tuple:
                if fnmatch(msg_topic, sub_pattern):
                    return True

        return False

    @staticmethod
    def matches(
        message_topics: Optional[List[str]],
        subscriber_topics: Optional[List[str]],
    ) -> bool:
        """Check if a message should be delivered to a subscriber based on topics.

        Args:
            message_topics: Topics associated with the message.
                None or empty list means the message has no topic filter.
            subscriber_topics: Topics that the subscriber is interested in.
                None or empty list means the subscriber receives all messages.

        Returns:
            True if the message should be delivered, False otherwise.
        """
        msg_tuple = tuple(sorted(message_topics)) if message_topics else None
        sub_tuple = tuple(sorted(subscriber_topics)) if subscriber_topics else None
        return TopicFilter._matches_cached(msg_tuple, sub_tuple)
