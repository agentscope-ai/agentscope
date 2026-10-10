# -*- coding: utf-8 -*-
"""In-process checkpoints for incremental ReMe conversation write-back."""
from dataclasses import dataclass, field

from ....message import Msg, TextBlock


@dataclass
class _ReplyWriteback:
    """Keep the original context boundary and acknowledged reply content.

    Checkpoints belong to one live agent/session/reply. They are not a
    persistent delivery log or a backend exactly-once guarantee.
    """

    session_id: str | None
    pre_ids: set[str]
    reply_id: str | None = None
    acknowledged: list[Msg] = field(default_factory=list)

    def snapshot(
        self,
        context: list[Msg],
        excluded_name: str,
    ) -> list[Msg]:
        """Copy this logical reply's messages before awaiting the backend.

        Args:
            context (`list[Msg]`):
                The current agent context.
            excluded_name (`str`):
                The reserved name of injected memory hints.

        Returns:
            `list[Msg]`:
                Independent message snapshots, in context order.
        """
        # Memory hints can split one assistant reply into several context
        # entries with the same ID. Keep every entry instead of overwriting it.
        return [
            msg.model_copy(deep=True)
            for msg in context
            if isinstance(msg, Msg)
            and msg.id not in self.pre_ids
            and msg.name != excluded_name
        ]

    def increment(self, snapshot: list[Msg]) -> list[Msg]:
        """Select content not included in an acknowledged submission.

        Args:
            snapshot (`list[Msg]`):
                The current logical reply's independent message copies.

        Returns:
            `list[Msg]`:
                New messages, new blocks and append-only text suffixes.
                State/metadata changes to previously written blocks do
                not replay their content.
        """
        # Match blocks across every entry for a message ID, including entries
        # separated by injected hints. Context entry positions need not match.
        message_ids = {msg.id for msg in self.acknowledged}
        blocks = {
            (msg.id, block.type, block.id): block
            for msg in self.acknowledged
            for block in msg.content
        }
        increment = []
        for msg in snapshot:
            if msg.id not in message_ids:
                increment.append(msg)
                continue
            unseen = []
            for block in msg.content:
                # Calls and results share an ID, but are distinct blocks.
                old = blocks.get((msg.id, block.type, block.id))
                if old is None:
                    unseen.append(block)
                elif (
                    isinstance(block, TextBlock)
                    and isinstance(old, TextBlock)
                    and block.text.startswith(old.text)
                ):
                    suffix = block.text[len(old.text) :]
                    if suffix:
                        unseen.append(
                            block.model_copy(update={"text": suffix}),
                        )
            if unseen:
                increment.append(msg.model_copy(update={"content": unseen}))
        return increment
