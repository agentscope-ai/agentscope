# -*- coding: utf-8 -*-
"""The manager of monitor module."""
import os
from typing import Any, Optional, List, Union
from pathlib import Path

from loguru import logger
from sqlalchemy import Column, Integer, String, create_engine, text
from sqlalchemy.ext.declarative import declarative_base, DeclarativeMeta
from sqlalchemy.orm import sessionmaker

from ._file import FileManager
from ..utils.common import _is_windows
from ..constants import (
    _DEFAULT_SQLITE_DB_NAME,
    _DEFAULT_TABLE_NAME_FOR_CHAT_AND_EMBEDDING,
    _DEFAULT_TABLE_NAME_FOR_IMAGE,
)

_Base: DeclarativeMeta = declarative_base()


class _ModelTable(_Base):  # mypy: ignore
    """The table for invocation records of chat and embedding model."""

    __tablename__ = _DEFAULT_TABLE_NAME_FOR_CHAT_AND_EMBEDDING

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_name = Column(String(50))
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)


class _ImageModelTable(_Base):
    """The table for invocation records of image model."""

    __tablename__ = _DEFAULT_TABLE_NAME_FOR_IMAGE

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_name = Column(String(50))
    resolution = Column(String(59))
    image_count = Column(Integer, default=0)


class MonitorManager:
    """The manager of monitor module."""

    _instance = None

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        """Create a singleton instance."""
        if cls._instance is None:
            cls._instance = super(MonitorManager, cls).__new__(
                cls,
            )
        else:
            raise RuntimeError(
                "The monitor manager has been initialized. Try to use "
                "MonitorManager.get_instance() to get the instance.",
            )
        return cls._instance

    @property
    def path_db(self) -> Union[str, None]:
        """The path to the database"""
        run_dir = FileManager.get_instance().run_dir
        if run_dir is None:
            return None
        return os.path.abspath(os.path.join(run_dir, _DEFAULT_SQLITE_DB_NAME))

    def __init__(self) -> None:
        """Initialize the monitor manager."""
        self.use_monitor = False
        self.session = None
        self.engine = None

        # The name of the views
        self.view_chat_and_embedding = "view_chat_and_embedding"
        self.view_image = "view_image"

    def initialize(self, use_monitor: bool) -> None:
        """Initialize the monitor manager.

        Args:
            use_monitor (`bool`):
                Whether to use the monitor.
        """

        self.use_monitor = use_monitor

        if use_monitor:
            self._create_monitor_db()

    @classmethod
    def get_instance(cls) -> "MonitorManager":
        """Get the instance of the singleton class."""
        if cls._instance is None:
            raise ValueError(
                "AgentScope hasn't been initialized. Please call "
                "`agentscope.init` function first.",
            )
        return cls._instance

    def _print_table(self, title: str, usage: List) -> None:
        """Print the table data."""
        # TODO: use a better way to display the table data
        if len(usage) == 1:
            print_usage = usage + [["-" for _ in range(len(usage[0]))]]
        else:
            print_usage = usage

        max_len_col = []
        for i in range(len(print_usage[0])):
            max_len_col.append(max(len(str(_[i])) for _ in print_usage))

        logger.info(title)
        for row in print_usage:
            line = "|".join(
                [""]
                + [
                    str(_).center(max_len + 2, " ")
                    for _, max_len in zip(row, max_len_col)
                ]
                + [""],
            )
            logger.info(line)

    def _create_monitor_db(self) -> None:
        """Create the database."""
        # To avoid path error in windows
        if self.path_db is None:
            raise RuntimeError(
                "The run_dir in file manager is not initialized.",
            )

        path = Path(os.path.abspath(self.path_db))

        if _is_windows():
            self.engine = create_engine(f"sqlite:///{str(path)}")
        else:
            self.engine = create_engine(f"sqlite:////{str(path)}")

        # Create tables
        _Base.metadata.create_all(self.engine)

        with self.engine.connect() as connection:
            # Create view for chat and embedding model
            create_view_sql = text(
                f"""
                CREATE VIEW IF NOT EXISTS {self.view_chat_and_embedding} AS
                SELECT
                    model_name,
                    COUNT(*) AS times,
                    SUM(prompt_tokens) AS prompt_tokens,
                    SUM(completion_tokens) AS completion_tokens,
                    SUM(total_tokens) AS total_tokens
                FROM
                    {_ModelTable.__tablename__}
                GROUP BY
                    model_name;
                """,
            )
            connection.execute(create_view_sql)

            # Create new for text-to-image model
            create_view_sql = text(
                f"""
                CREATE VIEW IF NOT EXISTS {self.view_image} AS
                SELECT
                    model_name,
                    resolution,
                    COUNT(*) AS times,
                    SUM(image_count) AS image_count
                FROM
                    {_ImageModelTable.__tablename__}
                GROUP BY
                    model_name, resolution;
                """,
            )
            connection.execute(create_view_sql)

        self.session = sessionmaker(bind=self.engine)

    def _close_monitor_db(self) -> None:
        """Close the monitor database to avoid file occupation error in
        windows."""
        if self.session is not None:
            self.session.close_all()

        if self.engine is not None:
            self.engine.dispose()

    def update_image_tokens(
        self,
        model_name: str,
        resolution: str,
        image_count: int,
    ) -> None:
        """Update the record of the image model."""
        if not self.use_monitor:
            return

        if self.session is None:
            raise RuntimeError("The DB session in monitor is not initialized.")

        with self.session() as sess:
            new_record = _ImageModelTable(
                model_name=model_name,
                resolution=resolution,
                image_count=image_count,
            )

            sess.add(new_record)
            sess.commit()

    def update_text_and_embedding_tokens(
        self,
        model_name: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: Optional[int] = None,
    ) -> None:
        """Update the tokens of a given model."""
        if not self.use_monitor:
            return

        if self.session is None:
            raise RuntimeError("The DB session in monitor is not initialized.")

        if total_tokens is not None:
            assert total_tokens == prompt_tokens + completion_tokens

        with self.session() as sess:
            new_record = _ModelTable(
                model_name=model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens
                or (prompt_tokens + completion_tokens),
            )

            sess.add(new_record)
            sess.commit()

    def print_llm_usage(self) -> dict:
        """Print the usage of all different model APIs."""
        text_and_embedding = self.show_text_and_embedding_tokens()

        image = self.show_image_tokens()

        return {
            "text_and_embedding": text_and_embedding,
            "image": image,
        }

    def show_image_tokens(self) -> List[dict]:
        """Show the tokens of all image model."""
        usage = []

        if self.use_monitor:
            with self.engine.connect() as connection:
                usage = connection.execute(
                    text(f"SELECT * FROM {self.view_image}"),
                ).fetchall()

        headers = [
            "MODEL NAME",
            "RESOLUTION",
            "TIMES",
            "IMAGE COUNT",
        ]

        usage.insert(0, headers)

        self._print_table("Image Model:", usage)

        return [
            {
                "model_name": _[0],
                "resolution": _[1],
                "times": _[2],
                "image_count": _[3],
            }
            for _ in usage[1:]
        ]

    def show_text_and_embedding_tokens(self) -> List[dict]:
        """Show the tokens of all model."""
        usage = []

        if self.use_monitor:
            with self.engine.connect() as connection:
                usage = connection.execute(
                    text(f"SELECT * FROM {self.view_chat_and_embedding}"),
                ).fetchall()

        headers = [
            "MODEL NAME",
            "TIMES",
            "PROMPT TOKENS",
            "COMPLETION TOKENS",
            "TOTAL TOKENS",
        ]

        usage.insert(0, headers)

        self._print_table("Text & Embedding Model:", usage)

        return [
            {
                "model_name": _[0],
                "times": _[1],
                "prompt_tokens": _[2],
                "completion_tokens": _[3],
                "total_tokens": _[4],
            }
            for _ in usage[1:]
        ]

    def rm_database(self) -> None:
        """Remove the database."""
        if self.path_db is not None and os.path.exists(self.path_db):
            os.remove(self.path_db)

    def state_dict(self) -> dict:
        """Serialize the monitor manager into a dict."""
        return {
            "use_monitor": self.use_monitor,
            "path_db": self.path_db,
        }

    def load_dict(self, data: dict) -> None:
        """Load the monitor manager from a dict."""
        assert "use_monitor" in data, "Key 'use_monitor' not found in data."

        self.initialize(data["use_monitor"])

    def flush(self) -> None:
        """Flush the monitor manager."""
        # Close the database before flushing
        self._close_monitor_db()

        self.use_monitor = False
        self.session = None
        self.engine = None

        # The name of the views
        self.view_chat_and_embedding = "view_chat_and_embedding"
        self.view_image = "view_image"
