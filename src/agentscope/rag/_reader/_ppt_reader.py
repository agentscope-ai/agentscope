# -*- coding: utf-8 -*-
"""The PowerPoint reader to read and chunk PowerPoint presentations."""
import base64
import hashlib
from typing import Any, Literal, Optional

from ._reader_base import ReaderBase
from ._text_reader import TextReader
from .._document import Document, DocMetadata
from ...message import ImageBlock, Base64Source
from ..._logging import logger


class PowerPointReader(ReaderBase):
    """The PowerPoint reader that splits text into chunks by a fixed chunk
    size."""

    def __init__(
        self,
        chunk_size: int = 512,
        split_by: Literal["char", "sentence", "paragraph"] = "sentence",
        include_image: bool = False,
        separate_slide: bool = False,
    ) -> None:
        """Initialize the PowerPoint reader.

        Args:
            chunk_size (`int`, default to 512):
                The size of each chunk, in number of characters.
            split_by (`Literal["char", "sentence", "paragraph"]`, default to \
            "sentence"):
                The unit to split the text, can be "char", "sentence", or
                "paragraph". The "sentence" option is implemented using the
                "nltk" library, which only supports English text.
            include_image (`bool`, default to False):
                Whether to include image content in the document. If True,
                images will be extracted and included as base64-encoded images.
            separate_slide (`bool`, default to False):
                Whether to treat each slide as a separate document. If True,
                each slide will be extracted as a separate Document object
                instead of being merged together.
        """
        self._validate_init_params(chunk_size, split_by)

        self.chunk_size = chunk_size
        self.split_by = split_by
        self.include_image = include_image
        self.separate_slide = separate_slide

        # Use TextReader to do the chunking
        self._text_reader = TextReader(self.chunk_size, self.split_by)

    def _validate_init_params(self, chunk_size: int, split_by: str) -> None:
        """Validate initialization parameters.

        Args:
            chunk_size (`int`):
                The chunk size to validate.
            split_by (`str`):
                The split mode to validate.
        """
        if chunk_size <= 0:
            raise ValueError(
                f"The chunk_size must be positive, got {chunk_size}",
            )

        if split_by not in ["char", "sentence", "paragraph"]:
            raise ValueError(
                "The split_by must be one of 'char', 'sentence' or "
                f"'paragraph', got {split_by}",
            )

    async def __call__(
        self,
        ppt_path: str,
    ) -> list[Document]:
        """Read a PowerPoint file, split it into chunks, and return a list of
        Document objects.

        Args:
            ppt_path (`str`):
                The input PowerPoint file path (.pptx file).

        Returns:
            `list[Document]`:
                A list of Document objects, where the metadata contains the
                chunked text, doc id and chunk id.
        """
        # Generate document ID
        doc_id = self.get_doc_id(ppt_path)

        # Load PowerPoint presentation
        try:
            from pptx import Presentation

            prs = Presentation(ppt_path)
        except ImportError as e:
            raise ImportError(
                "Please install python-pptx to use the PowerPoint reader. "
                "You can install it by `pip install python-pptx`.",
            ) from e
        except Exception as e:
            raise ValueError(
                f"Failed to read PowerPoint file {ppt_path}: {e}",
            ) from e

        # Process slides
        try:
            if self.separate_slide:
                return await self._process_slides_separately(prs, doc_id)
            else:
                return await self._process_slides_merged(prs, doc_id)
        finally:
            # Cleanup if needed
            pass

    async def _process_slides_merged(
        self,
        prs: Any,
        doc_id: str,
    ) -> list[Document]:
        """Process all slides as a merged document.

        Args:
            prs (`Any`):
                The python-pptx Presentation object.
            doc_id (`str`):
                The document ID.

        Returns:
            `list[Document]`:
                A list of Document objects from all slides merged together.
        """
        all_docs = []
        all_texts = []

        # Collect all images and texts from all slides
        for slide_idx, slide in enumerate(prs.slides):
            # Extract images from slide if requested
            if self.include_image:
                slide_images = self._extract_slide_images(
                    slide,
                    doc_id,
                    slide_idx,
                )
                all_docs.extend(slide_images)

            # Collect text content from slide
            slide_text = self._extract_slide_text(slide, slide_idx)
            if slide_text:
                all_texts.append(slide_text)

        # Merge all text content and process as single document
        if all_texts:
            merged_text = "\n\n".join(all_texts)
            text_docs = await self._create_documents_from_text(
                merged_text,
                doc_id,
            )
            all_docs.extend(text_docs)

        return all_docs

    async def _process_slides_separately(
        self,
        prs: Any,
        doc_id: str,
    ) -> list[Document]:
        """Process each slide as separate documents.

        Args:
            prs (`Any`):
                The python-pptx Presentation object.
            doc_id (`str`):
                The document ID.

        Returns:
            `list[Document]`:
                A list of Document objects with each slide processed
                separately.
        """
        all_docs = []

        for slide_idx, slide in enumerate(prs.slides):
            # Extract images from slide if requested
            slide_images = []
            if self.include_image:
                slide_images = self._extract_slide_images(
                    slide,
                    doc_id,
                    slide_idx,
                )

            # Process text content from slide
            slide_text = self._extract_slide_text(slide, slide_idx)

            # Add images first (if any), then text for this slide
            if slide_images:
                all_docs.extend(slide_images)

            if slide_text:
                slide_docs = await self._create_documents_from_text(
                    slide_text,
                    doc_id,
                )
                all_docs.extend(slide_docs)

        return all_docs

    def _extract_slide_text(self, slide: Any, slide_idx: int) -> Optional[str]:
        """Extract text content from a slide.

        Args:
            slide (`Any`):
                The slide object from python-pptx.
            slide_idx (`int`):
                The index of the slide.

        Returns:
            `Optional[str]`:
                The extracted text content, or None if empty.
        """
        slide_texts = [f"Slide {slide_idx + 1}"]

        # Add slide number if multiple slides

        # Extract text from all shapes
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue

            try:
                text_frame = shape.text_frame

                # Extract text from all paragraphs in the text frame
                for paragraph in text_frame.paragraphs:
                    para_text = paragraph.text.strip()
                    if para_text:
                        slide_texts.append(para_text)
            except Exception as e:
                logger.warning("Failed to extract text from shape: %s", e)
                continue

        return "\n".join(slide_texts) if slide_texts else None

    async def _create_documents_from_text(
        self,
        text: str,
        doc_id: str,
    ) -> list[Document]:
        """Create Document objects from text using TextReader.

        Args:
            text (`str`):
                The text to convert to documents.
            doc_id (`str`):
                The document ID.

        Returns:
            `list[Document]`:
                A list of Document objects.
        """
        try:
            docs = await self._text_reader(text)
            # TextReader already returns properly formatted documents,
            # just update doc_id
            for doc in docs:
                doc.id = doc_id
                doc.metadata.doc_id = doc_id
            return docs
        except Exception as e:
            logger.error("Failed to create documents from text: %s", e)
            return []

    def _extract_slide_images(
        self,
        slide: Any,
        doc_id: str,
        slide_idx: int,
    ) -> list[Document]:
        """Extract images from a slide.

        Args:
            slide (`Any`):
                The slide object from python-pptx.
            doc_id (`str`):
                The document ID.
            slide_idx (`int`):
                The index of the slide.

        Returns:
            `list[Document]`:
                A list of Document objects containing images.
        """
        images = []

        # Determine picture type once
        try:
            from pptx.enum.shapes import MSO_SHAPE_TYPE

            picture_type = MSO_SHAPE_TYPE.PICTURE
        except ImportError:
            picture_type = 13  # MSO_SHAPE_TYPE.PICTURE fallback

        for shape in slide.shapes:
            if shape.shape_type != picture_type:
                continue

            try:
                image_doc = self._create_image_document_from_shape(
                    shape,
                    doc_id,
                    slide_idx,
                )
                if image_doc:
                    images.append(image_doc)
            except Exception as e:
                logger.warning(
                    "Failed to extract image from slide %d: %s",
                    slide_idx + 1,
                    e,
                )
                continue

        return images

    def _create_image_document_from_shape(
        self,
        shape: Any,
        doc_id: str,
        slide_idx: int,
    ) -> Optional[Document]:
        """Create Document object from image shape.

        Args:
            shape (`Any`):
                The image shape from python-pptx.
            doc_id (`str`):
                The document ID.
            slide_idx (`int`):
                The index of the slide.

        Returns:
            `Optional[Document]`:
                A Document object containing the image, or None on error.
        """
        try:
            # Get image data
            image_data = shape.image.blob

            # Determine media type
            media_type = self._get_media_type_from_data(image_data)

            # Convert to base64
            base64_data = base64.b64encode(image_data).decode("utf-8")

            # Create ImageBlock
            image_block = ImageBlock(
                type="image",
                source=Base64Source(
                    type="base64",
                    media_type=media_type,
                    data=base64_data,
                ),
            )

            # Create Document - each image is an independent document
            metadata = DocMetadata(
                content=image_block,
                doc_id=doc_id,
                chunk_id=0,
                total_chunks=1,
            )
            # Add slide index to metadata
            metadata["slide_idx"] = slide_idx

            return Document(metadata=metadata)
        except Exception as e:
            logger.error("Failed to create image document: %s", e)
            return None

    def _get_media_type_from_data(self, data: bytes) -> str:
        """Determine media type from image data.

        Args:
            data (`bytes`):
                The raw image data.

        Returns:
            `str`:
                The MIME type of the image (e.g., "image/png", "image/jpeg").
        """
        # Image signature mapping
        signatures = {
            b"\x89PNG\r\n\x1a\n": "image/png",
            b"\xff\xd8": "image/jpeg",
            b"GIF87a": "image/gif",
            b"GIF89a": "image/gif",
            b"BM": "image/bmp",
        }

        # Check signatures
        for signature, media_type in signatures.items():
            if data.startswith(signature):
                return media_type

        # Check WebP (RIFF at start + WEBP at offset 8)
        if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"

        # Default to JPEG
        return "image/jpeg"

    def get_doc_id(self, ppt_path: str) -> str:
        """Generate unique document ID from file path.

        Args:
            ppt_path (`str`):
                The path to the PowerPoint file.

        Returns:
            `str`:
                The document ID (SHA256 hash of the file path).
        """
        return hashlib.sha256(ppt_path.encode("utf-8")).hexdigest()
