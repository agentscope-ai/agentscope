# -*- coding: utf-8 -*-
"""The knowledge related model"""
from enum import Enum
import uuid
import datetime
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel, JSON, Column, TEXT


def name_field() -> Field:
    """The knowledge name field"""
    return Field(min_length=1, max_length=255)


def description_field() -> Field:
    """The knowledge description field"""
    return Field(default="", min_length=0, max_length=255)


def time_field() -> Field:
    """The knowledge create time field"""
    return Field(
        default_factory=lambda: datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S",
        ),
    )


class QueryTypeEnum(str, Enum):
    """The query type enum"""

    TEXT = "string"
    VECTOR = "vector"


class PermissionLevelEnum(str, Enum):
    """The permission level enum"""

    ME = "me"
    TEAM = "team"
    PART = "part"


class DataSourceTypeEnum(str, Enum):
    """The data source type enum"""

    DATABASE = "database"
    UPLOAD_FILE = "upload_file"
    NOTION_IMPORT = "notion_import"
    WEBSITE_CRAWL = "website_crawl"


class IndexingTechnologyEnum(str, Enum):
    """The indexing technology enum"""

    HIGH_QUALITY = "high_quality"
    ECONOMY = "economy"


class ChunkTypeEnum(str, Enum):
    """The chunk type enum"""

    PARAGRAPH = "paragraph"
    FULL_TEXT = "full-text"
    PARENT_CHILD = "parent-child"
    LENGTH = "length"
    PAGE = "page"
    TITLE = "title"
    REGEX = "regex"


class RetrievalMethodEnum(Enum):
    """The retrieval method enum"""

    SEMANTIC_SEARCH = "semantic_search"
    FULL_TEXT_SEARCH = "full_text_search"
    HYBRID_SEARCH = "hybrid_search"


class KnowledgeBaseTypeEnum(str, Enum):
    """The knowledge base type enum"""

    STRUCTURED = "structured"
    UNSTRUCTURED = "unstructured"


class KnowledgeBase(SQLModel, table=True):  # type: ignore
    """The knowledge base model used to create database table"""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    workspace_id: str = Field(default="1")
    type: KnowledgeBaseTypeEnum = Field(
        default=KnowledgeBaseTypeEnum.UNSTRUCTURED,
    )
    status: int = Field(default=1)
    name: str = name_field()
    description: str = description_field()
    permission_level: PermissionLevelEnum = Field(
        default=PermissionLevelEnum.ME,
    )
    data_source_type: DataSourceTypeEnum = Field(
        default=DataSourceTypeEnum.UPLOAD_FILE,
    )

    rewrite_enabled: bool = Field(default=False)

    process_config: dict = Field(default={}, sa_type=JSON)
    index_config: dict = Field(default={}, sa_type=JSON)
    search_config: dict = Field(default={}, sa_type=JSON)

    creator_id: str = Field(
        # foreign_key="account.account_id",
        nullable=True,
        # ondelete="CASCADE",
    )
    gmt_create: str = time_field()
    updater_id: str = Field(
        # foreign_key="account.account_id",
        nullable=True,
        # ondelete="CASCADE",
    )
    gmt_modified: str = time_field()
    deleted: bool = Field(default=False)
    deleter_id: str = Field(
        # foreign_key="account.account_id",
        nullable=True,
        # ondelete="CASCADE",
    )
    gmt_deleted: str = Field(default="")
    documents: list["Document"] = Relationship(
        back_populates="knowledge_base",
        cascade_delete=True,
    )

    @property
    def kb_id(self) -> uuid.UUID:
        """get the knowledge base id"""
        return self.id

    @property
    def total_docs(self) -> int:
        """get the total number of documents"""
        if not hasattr(self, "documents") or self.documents is None:
            return 0
        try:
            return len(self.documents)
        except (TypeError, ValueError):
            return 0

    @property
    def word_count(self) -> int:
        """get the total words count"""
        return (
            sum(document.word_count for document in self.documents)
            if self.documents
            else 0
        )

    @property
    def get_embedding_model_dict(self) -> dict:
        """get embedding model info"""
        return {
            "embedding_model": self.index_config.get("embedding_model"),
            "embedding_provider": self.index_config.get(
                "embedding_provider",
            ),
        }

    @property
    def retrieval_model_dict(self) -> dict:
        """get retrieval model info"""
        return self.retrieval_model if self.retrieval_model else {}


class KnowledgeBasePermission(SQLModel, table=True):  # type: ignore
    """The knowledge base permission model used to create database table"""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    knowledge_base_id: uuid.UUID = Field(
        foreign_key="knowledgebase.id",
        nullable=False,
        ondelete="CASCADE",
    )
    account_id: str = Field(
        foreign_key="account.account_id",
    )
    team_id: str = Field(
        foreign_key="account.account_id",
        nullable=True,
    )
    has_permission: bool = Field(default=True)
    gmt_create: str = time_field()


class Document(SQLModel, table=True):  # type: ignore
    """The document model used to create database table"""

    __tablename__ = "document_"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    type: str = Field(default="file")
    name: str = name_field()
    description: str = description_field()
    status: int = Field(default=1)
    path: str = Field(default="")
    parsed_path: str = Field(default="")
    source: str = Field(default="")
    extension: str = Field(default="")
    content_type: str = Field(default="")
    size: int = Field(default=0)
    process_config: dict = Field(default={}, sa_type=JSON)
    tabs: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    data_source_type: DataSourceTypeEnum = Field(
        default=DataSourceTypeEnum.UPLOAD_FILE,
    )
    content: str = Field(sa_column=Column(TEXT), default="")
    chunks: list["Chunk"] = Relationship(
        back_populates="document",
        cascade_delete=True,
    )
    creator_id: str = Field(
        # foreign_key="account.account_id",
        nullable=True,
        # ondelete="CASCADE",
    )
    gmt_create: str = time_field()
    updater_id: str = Field(
        # foreign_key="account.account_id",
        nullable=True,
        # ondelete="CASCADE",
    )
    gmt_modified: str = time_field()
    knowledge_base_id: uuid.UUID = Field(
        foreign_key="knowledgebase.id",
        nullable=False,
        ondelete="CASCADE",
    )
    disabler_id: Optional[uuid.UUID] = Field(
        # foreign_key="account.account_id",
        nullable=True,
        # ondelete="CASCADE",
        # default=None,
    )
    gmt_disabled: str = time_field()
    tokens: int = Field(default=0)
    indexing_status: str = Field(default="")
    error: str = Field(default="")
    enabled: bool = Field(default=True)

    knowledge_base: KnowledgeBase = Relationship(back_populates="documents")
    deleter_id: str = Field(
        # foreign_key="account.account_id",
        nullable=True,
        # ondelete="CASCADE",
    )
    gmt_deleted: str = Field(default="")

    @property
    def format(self) -> str:
        """format"""
        return (
            self.extension.split(".")[-1]
            if "." in self.extension
            else self.extension
        )

    @property
    def kb_id(self) -> uuid.UUID:
        """get the knowledge base id"""
        return self.knowledge_base_id

    @property
    def doc_id(self) -> uuid.UUID:
        """get the document id"""
        return self.id

    @property
    def doc_metadata(self) -> dict:
        """get document metadata"""
        return {
            "content_type": self.content_type,
            "size": self.size,
        }

    @property
    def index_status(self) -> Optional[str]:
        """i.e. indexing status"""
        return self.indexing_status

    @property
    def creator(self) -> Optional[str]:
        """i.e. creator_id"""
        return self.creator_id

    @property
    def modifier(self) -> Optional[str]:
        """i.e. updater_id"""
        return self.updater_id

    @property
    def show_status(self) -> Optional[str]:
        """show status"""
        status = None
        if self.indexing_status == "error":
            status = "error"
        return status

    @property
    def hit_count(self) -> int:
        """get the hint num"""
        return (
            sum(chunk.hit_count for chunk in self.chunks) if self.chunks else 0
        )

    @property
    def word_count(self) -> int:
        """get the total words count"""
        return (
            sum(chunk.word_count for chunk in self.chunks)
            if self.chunks
            else 0
        )

    @property
    def chunk_count(self) -> int:
        """Get the number of chunks"""
        return len(self.chunks) if self.chunks else 0

    @property
    def average_chunk_length(self) -> int:
        """Get the average"""
        if (
            self.word_count
            and self.word_count != 0
            and self.chunk_count
            and self.chunk_count != 0
        ):
            return self.word_count // self.chunk_count
        return 0


class Chunk(SQLModel, table=True):  # type: ignore
    """The chunk model used to create database table"""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    position: int = Field(default=0)
    content: str = Field(sa_column=Column(TEXT), default="")
    document_name: str = Field(default="")
    title: str = Field(default="")
    page_number: int = Field(default=0)
    word_count: int = Field(default=0)
    tokens: int = Field(default=0)
    keywords: list[str] = Field(default={}, sa_type=JSON)

    hit_count: int = Field(default=0)

    creator_id: str = Field(
        # foreign_key="account.account_id",
        nullable=True,
        # ondelete="CASCADE",
    )
    gmt_create: str = time_field()
    enabled: bool = Field(default=True)
    disabler_id: str = Field(
        # foreign_key="account.account_id",
        nullable=True,
        # ondelete="CASCADE",
    )
    gmt_disabled: str = time_field()
    status: str = Field(default="")
    updater_id: str = Field(
        # foreign_key="account.account_id",
        nullable=True,
        # ondelete="CASCADE",
    )
    gmt_modified: str = time_field()
    gmt_indexing: str = time_field()
    gmt_completed: str = time_field()
    error: str = Field(default="")
    gmt_stop: str = time_field()

    document_id: uuid.UUID = Field(
        foreign_key="document_.id",
        nullable=False,
        ondelete="CASCADE",
    )
    document: Document = Relationship(back_populates="chunks")
    knowledge_base_id: uuid.UUID = Field(
        foreign_key="knowledgebase.id",
        nullable=False,
        ondelete="CASCADE",
    )
    child_chunks: list["ChildChunk"] = Relationship(
        back_populates="chunk",
        cascade_delete=True,
    )

    @property
    def text(self) -> str:
        """i.e content"""
        return self.content

    @property
    def chunk_id(self) -> uuid.UUID:
        """i.e. chunk_id"""
        return self.id

    @property
    def doc_id(self) -> uuid.UUID:
        """i.e. doc_id"""
        return self.document_id

    @property
    def doc_name(self) -> str:
        """i.e. doc_name"""
        return self.document_name


class ChildChunk(SQLModel, table=True):  # type: ignore
    """The child chunk model used to create database table"""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    content: str = Field(sa_column=Column(TEXT), default="")
    position: int = Field(default=0)
    word_count: int = Field(default=0)
    creator_id: str = Field(
        # foreign_key="account.account_id",
        nullable=True,
        # ondelete="CASCADE",
    )
    gmt_create: str = time_field()
    updater_id: str = Field(
        # foreign_key="account.account_id",
        nullable=True,
        # ondelete="CASCADE",
    )
    gmt_modified: str = time_field()
    chunk_id: uuid.UUID = Field(
        foreign_key="chunk.id",
        nullable=False,
        ondelete="CASCADE",
    )
    chunk: Chunk = Relationship(back_populates="child_chunks")
    knowledge_base_id: uuid.UUID = Field(
        foreign_key="knowledgebase.id",
        nullable=False,
        ondelete="CASCADE",
    )
    document_id: uuid.UUID = Field(
        foreign_key="document_.id",
        nullable=False,
        ondelete="CASCADE",
    )
