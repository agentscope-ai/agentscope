from typing import Any, Optional, List

import beanie
from beanie.odm.operators.find.comparison import In
from pydantic import Field

from ._base import MemoryBase
from ...message import Msg
from beanie import Document


class MongoDbMemory(MemoryBase):
    _initialized: bool = False

    class MemoryDocument(Document):
        session_id: Optional[str] = Field(default=None)
        user_id: Optional[str] = Field(default=None)
        marks: List[str] = Field(default_factory=list)
        data: dict = Field(default_factory=dict)

        class Settings:
            name = 'memory'

    def __init__(self, database_name: str = 'memory',
                 host: str = "localhost",
                 port: int = 27017,
                 user: Optional[str] = None,
                 password: Optional[str] = None,
                 session_id: Optional[str] = None,
                 app_name: Optional[str] = None,
                 user_id: Optional[str] = None):
        super().__init__()
        if user and password:
            self._need_auth = True
        else:
            self._need_auth = False
        self._session_id = session_id or "default_session"
        self._user_id = user_id or "default_user"
        self._database_name = database_name
        self._user = user
        self._password = password
        self._port = port
        self._host = host
        self._app_name = app_name

    async def _ensure_initialize(self):
        try:
            if not self._initialized:
                await beanie.init_beanie(connection_string=self._fetch_connection_string(),
                                         document_models=[self.MemoryDocument])
                self._initialized = True
        except Exception as e:
            raise RuntimeError(
                "Failed to initialize MongoDB connection. "
                "Please make sure MongoDB is running and accessible.",
            ) from e

    def _fetch_connection_string(self):
        if self._need_auth:
            uri = f'mongodb://{self._user}:{self._password}@{self._host}:{self._host}/{self._database_name}'
            if self._app_name:
                uri += f'?appName={self._app_name}'
        else:
            uri = f'mongodb://{self._host}:{self._port}/{self._database_name}'
        return uri

    async def add(self, memories: Msg | list[Msg] | None = None, marks: str | list[str] | None = None,
                  overwrite: bool = True,
                  **kwargs: Any) -> None:
        await self._ensure_initialize()
        if memories is None:
            return
        if isinstance(memories, Msg):
            memories = [memories]
        if marks is None:
            mark_list = []
        elif isinstance(marks, str):
            mark_list = [marks]
        else:
            mark_list = marks
        if memories:
            for memory in memories:
                existing = await self.MemoryDocument.find_one(
                    self.MemoryDocument.session_id == self._session_id,
                    self.MemoryDocument.user_id == self._user_id,
                    self.MemoryDocument.data["id"] == memory.id
                )
                if existing:
                    if overwrite:
                        existing.session_id = self._session_id
                        existing.user_id = self._user_id
                        existing.marks = mark_list
                        existing.data = memory.to_dict()
                        await existing.save()
                else:
                    doc = self.MemoryDocument(
                        session_id=self._session_id,
                        user_id=self._user_id,
                        marks=mark_list,
                        data=memory.to_dict()
                    )
                    await doc.insert()

    async def delete(self, msg_ids: list[str], **kwargs: Any) -> int:
        await self._ensure_initialize()
        if not msg_ids:
            return 0
        res = await self.MemoryDocument.find(
            self.MemoryDocument.user_id == self._user_id,
            self.MemoryDocument.session_id == self._session_id,
            In(self.MemoryDocument.data["id"], msg_ids)
        ).delete_many()
        if not res:
            return 0
        return res.deleted_count

    async def delete_by_mark(
            self,
            mark: str | list[str],
            *args: Any,
            **kwargs: Any,
    ) -> int:
        if not mark:
            return 0
        if isinstance(mark, str):
            mark = [mark]
        res = await self.MemoryDocument.find(
            self.MemoryDocument.session_id == self._session_id,
            self.MemoryDocument.user_id == self._user_id,
            In(self.MemoryDocument.marks, mark)
        ).delete_many()
        if not res:
            return 0
        return res.deleted_count

    async def size(self) -> int:
        # count documents that bind to this instance
        await self._ensure_initialize()
        res = await self.MemoryDocument.find_many(
            self.MemoryDocument.session_id == self._session_id,
            self.MemoryDocument.user_id == self._user_id
        ).to_list()
        return len(res)

    async def clear(self) -> None:
        await self._ensure_initialize()
        await self.MemoryDocument.find_many(
            self.MemoryDocument.session_id == self._session_id,
            self.MemoryDocument.user_id == self._user_id
        ).delete_many()

    async def get_memory(self, mark: str | None = None, exclude_mark: str | None = None, prepend_summary: bool = True,
                         **kwargs: Any) -> list[Msg]:
        await self._ensure_initialize()
        if not (mark is None or isinstance(mark, str)):
            raise TypeError(
                f"The mark should be a string or None, but got {type(mark)}.",
            )

        if not (exclude_mark is None or isinstance(exclude_mark, str)):
            raise TypeError(
                f"The exclude_mark should be a string or None, but got "
                f"{type(exclude_mark)}.",
            )
        _messages = await self.MemoryDocument.find_many(
            self.MemoryDocument.session_id == self._session_id,
            self.MemoryDocument.user_id == self._user_id,
        ).sort("-id").to_list()
        # condition too complex to set in query, filter after query..
        if mark:
            _messages = [
                message for message in _messages
                if mark in message.marks
            ]
        if exclude_mark:
            _messages = [
                message for message in _messages
                if exclude_mark not in message.marks
            ]
        messages = [
            Msg.from_dict(message.data) for message in _messages
        ]
        if prepend_summary and self._compressed_summary:
            return [
                Msg(
                    "user",
                    self._compressed_summary,
                    "user",
                ),
                *messages,
            ]
        return messages

    async def __aenter__(self):
        # ensure Document Instance bind to mongodb connection....
        await self._ensure_initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # clear data when exit
        await self.clear()
        ...

    async def update_messages_mark(
            self,
            new_mark: str | None,
            old_mark: str | None = None,
            msg_ids: list[str] | None = None,
    ) -> int:
        """A unified method to update marks of messages in the storage (add,
        remove, or change marks).

        - If `msg_ids` is provided, the update will be applied to the messages
         with the specified IDs.
        - If `old_mark` is provided, the update will be applied to the
         messages with the specified old mark. Otherwise, the `new_mark` will
         be added to all messages (or those filtered by `msg_ids`).
        - If `new_mark` is `None`, the mark will be removed from the messages.

        Args:
            new_mark (`str | None`, optional):
                The new mark to set for the messages. If `None`, the mark
                will be removed.
            old_mark (`str | None`, optional):
                The old mark to filter messages. If `None`, this constraint
                is ignored.
            msg_ids (`list[str] | None`, optional):
                The list of message IDs to be updated. If `None`, this
                constraint is ignored.

        Returns:
            `int`:
                The number of messages updated.
        """
        if new_mark is not None and not isinstance(new_mark, str):
            raise ValueError(
                f"The 'new_mark' parameter must be a string or None, "
                f"but got {type(new_mark)}.",
            )

        if old_mark is not None and not isinstance(old_mark, str):
            raise ValueError(
                f"The 'old_mark' parameter must be a string or None, "
                f"but got {type(old_mark)}.",
            )

        if msg_ids is not None and not (
                isinstance(msg_ids, list)
                and all(isinstance(_, str) for _ in msg_ids)
        ):
            raise ValueError(
                f"The 'msg_ids' parameter must be a list of strings or None, "
                f"but got {type(msg_ids)}.",
            )
        # bind to session and user_id
        if msg_ids:
            if old_mark is not None:
                messages = await self.MemoryDocument.find_many(
                    self.MemoryDocument.session_id == self._session_id,
                    self.MemoryDocument.user_id == self._user_id,
                    self.MemoryDocument.marks == old_mark,
                    In(self.MemoryDocument.data['id'], msg_ids)
                ).to_list()
            else:
                messages = await self.MemoryDocument.find_many(
                    self.MemoryDocument.session_id == self._session_id,
                    self.MemoryDocument.user_id == self._user_id,
                    In(self.MemoryDocument.data['id'], msg_ids)
                ).to_list()
        else:
            if old_mark is not None:
                messages = await self.MemoryDocument.find_many(
                    self.MemoryDocument.session_id == self._session_id,
                    self.MemoryDocument.user_id == self._user_id,
                    self.MemoryDocument.marks == old_mark
                ).to_list()
            else:
                messages = await self.MemoryDocument.find_many(
                    self.MemoryDocument.session_id == self._session_id,
                    self.MemoryDocument.user_id == self._user_id
                ).to_list()
        if new_mark is None:
            for message in messages:
                message.session_id = self._session_id
                message.user_id = self._user_id
                message.marks.remove(old_mark)
                await message.save()
        else:
            for message in messages:
                if old_mark:
                    message.marks.remove(old_mark)
                message.session_id = self._session_id
                message.user_id = self._user_id
                message.marks.append(new_mark)
                await message.save()
        return len(messages)
