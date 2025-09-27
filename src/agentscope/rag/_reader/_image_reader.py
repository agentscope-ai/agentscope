# -*- coding: utf-8 -*-
"""The Image reader modules"""
import hashlib

from .. import DocMetadata
from ...message import ImageBlock, URLSource
from .._reader import ReaderBase, Document


class ImageReader(ReaderBase):
    """A simple image reader that wraps the image into a Document object.

    This class is only a simple implementation to support multimodal RAG.
    """

    async def __call__(self, image_url: str | list[str]) -> list[Document]:
        """Read an image and return the wrapped Document object.

        Args:
            image_url (`str | list[str]`):
                The image URL(s) or path(s).

        Returns:
            `list[Document]`:
                A list of Document objects containing the image data.
        """
        # Read the image data and wrap it into a Document object.
        if isinstance(image_url, str):
            image_url = [image_url]

        image_blocks: list[ImageBlock] = [
            ImageBlock(
                type="image",
                source=URLSource(
                    type="url",
                    url=_,
                ),
            )
            for _ in image_url
        ]

        doc_idx = [self.get_doc_id(_) for _ in image_url]

        return [
            Document(
                metadata=DocMetadata(
                    content=image_block,
                    doc_id=doc_id,
                    chunk_id=0,
                    total_chunks=1,
                ),
            )
            for doc_id, image_block in zip(doc_idx, image_blocks)
        ]

    def get_doc_id(self, image_path: str) -> str:
        """Generate a document ID based on the image path.

        Args:
            image_path (`str`):
                The image path or URL.

        Returns:
            `str`:
                The generated document ID.
        """
        return hashlib.md5(image_path.encode("utf-8")).hexdigest()
