# -*- coding: utf-8 -*-
# pylint: disable=C0301, C0411
"""The MemRecord class for ReActMemory"""

import json
import uuid
from typing import Any, Dict, List, Optional, Union

from agentscope.message import Msg
from pydantic import BaseModel, Field
from pydantic.types import StrictFloat, StrictStr


class SparseVector(BaseModel):
    """
    Sparse vector structure
    """

    indices: List[int] = Field(..., description="Indices must be unique")
    values: List[float] = Field(
        ...,
        description="Values and indices must be the same length",
    )


class Document(BaseModel):
    """WARN: Work-in-progress, unimplemented  Text document for embedding. Requires inference infrastructure, unimplemented."""  # noqa: E501

    text: str = Field(
        ...,
        description="Text of the document This field will be used as input for the embedding model",  # noqa: E501
    )
    model: str = Field(
        ...,
        description="Name of the model used to generate the vector List of available models depends on a provider",  # noqa: E501
    )
    options: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Parameters for the model Values of the parameters are model-specific",  # noqa: E501
    )


class Image(BaseModel):
    """WARN: Work-in-progress, unimplemented  Image object for embedding. Requires inference infrastructure, unimplemented."""  # noqa: E501

    image: Any = Field(
        ...,
        description="Image data: base64 encoded image or an URL",
    )
    model: str = Field(
        ...,
        description="Name of the model used to generate the vector List of available models depends on a provider",  # noqa: E501
    )
    options: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Parameters for the model Values of the parameters are model-specific",  # noqa: E501
    )


class InferenceObject(BaseModel):
    """WARN: Work-in-progress, unimplemented  Custom object for embedding. Requires inference infrastructure, unimplemented."""  # noqa: E501

    object: Any = Field(
        ...,
        description="Arbitrary data, used as input for the embedding model Used if the model requires more than one input or a custom input",  # noqa: E501
    )
    model: str = Field(
        ...,
        description="Name of the model used to generate the vector List of available models depends on a provider",  # noqa: E501
    )
    options: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Parameters for the model Values of the parameters are model-specific",  # noqa: E501
    )


Vector = Union[
    List[StrictFloat],
    SparseVector,
    List[List[StrictFloat]],
    Document,
    Image,
    InferenceObject,
]
VectorStruct = Union[
    List[StrictFloat],
    List[List[StrictFloat]],
    Dict[StrictStr, Vector],
    Document,
    Image,
    InferenceObject,
]


class MemRecord(BaseModel):
    """The memory message class."""

    model_config = {
        "arbitrary_types_allowed": True,
    }
    metadata: dict = Field(default=None)
    """ Additional metadata stored in the memory message."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    """The unique identity of the memory message."""
    embedding: Optional[VectorStruct] = Field(default=None)
    """The embedding of the memory message, if available."""

    def to_dict(self) -> dict:
        """Convert MemRecord to dictionary for serialization."""
        return {
            "__module__": self.__module__,
            "__name__": self.__class__.__name__,
            "metadata": self.metadata,
            "id": self.id,
            "mem_type": self.mem_type,
        }

    def to_json(self) -> str:
        """Serialize MemRecord to JSON string."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            default=self._json_default,
        )

    @staticmethod
    def _json_default(obj: Any) -> Any:
        """Default JSON encoder for handling non-serializable objects."""
        if hasattr(obj, "tolist"):  # numpy array
            return obj.tolist()
        if hasattr(obj, "__dict__"):  # objects with __dict__
            return obj.__dict__
        return str(obj)

    @classmethod
    def from_dict(cls, data: dict) -> "MemRecord":
        """Create MemRecord from dictionary."""
        # Remove the special keys used for serialization
        mem_data = {
            k: v
            for k, v in data.items()
            if k not in ["__module__", "__name__"]
        }
        return cls(**mem_data)

    @classmethod
    def from_msg(
        cls,
        msg: Msg,
        embedding: VectorStruct = None,
        record_id: Optional[Union[uuid.UUID, str]] = None,
    ) -> "MemRecord":
        """
        Create a MemRecord from a Msg.
        Args:
            msg (Msg): The original message.
            embedding (Embedding, optional): The embedding of the message.
            record_id (Optional[Union[uuid.UUID, str]], optional):
                The unique identity of the memory message.
        Returns:
            MemRecord: The created memory message.
        """
        new_metadata = {
            "name": msg.name,
            "role": msg.role,
            "timestamp": msg.timestamp,
        }
        if msg.metadata is not None:
            new_metadata.update(msg.metadata)
        if isinstance(msg.content, str):
            data = msg.content
        elif isinstance(msg.content, list):
            data = json.dumps(msg.content)
        new_metadata["data"] = data
        return cls.from_data(
            metadata=new_metadata,
            embedding=embedding,
            record_id=record_id or new_metadata.get("id") or uuid.uuid4().hex,
        )

    @classmethod
    def from_data(
        cls,
        metadata: dict = None,
        embedding: VectorStruct = None,
        record_id: Optional[Union[uuid.UUID, str]] = None,
    ) -> "MemRecord":
        """
        Create a MemRecord from a Msg.
        Args:
            metadata (dict, optional): Additional metadata.
            embedding (Embedding, optional): The embedding of the message.
            record_id (Optional[Union[uuid.UUID, str]], optional):
                The unique identity of the memory message.
        Returns:
            MemRecord: The created memory message.
        """
        if record_id is not None:
            record_id = str(record_id)
        return cls(metadata=metadata, embedding=embedding, id=record_id)

    def update_data(
        self,
        metadata: dict = None,
        embedding: VectorStruct = None,
    ) -> None:
        """Update the data of the memory message."""
        if metadata is not None:
            self.metadata = metadata
        if embedding is not None:
            self.embedding = embedding

    def to_msg(self) -> Msg:
        """Convert MemRecord to Msg."""
        msg = Msg(
            name=self.metadata.get("name", ""),
            role=self.metadata.get("role", ""),
            content=[],
        )
        content = self.metadata.get("data", "")
        if isinstance(content, str):
            msg.content.append({"type": "text", "text": content})
        elif isinstance(content, list):
            msg.content.extend(content)
        return msg


def serialize_list(mem_records: List[Union[MemRecord, Msg]]) -> str:
    """Serialize a list of MemRecord objects to JSON string."""
    return json.dumps(
        [mem.to_dict() for mem in mem_records],
        ensure_ascii=False,
    )


def deserialize_memrecord_list(json_string: str) -> List[MemRecord]:
    """Deserialize a JSON string back to a list of MemRecord objects."""
    data_list = json.loads(json_string)
    return [MemRecord.from_dict(data) for data in data_list]


def deserialize_msg_list(json_string: str) -> List[Msg]:
    """Deserialize a JSON string back to a list of Msg objects."""
    data_list = json.loads(json_string)
    return [Msg.from_dict(data) for data in data_list]
