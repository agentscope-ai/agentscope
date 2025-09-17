# -*- coding: utf-8 -*-
"""The dashscope multimodal embedding model in agentscope."""
from datetime import datetime
from typing import Any

from ._cache_base import EmbeddingCacheBase
from ._embedding_response import EmbeddingResponse
from ._embedding_usage import EmbeddingUsage
from ._embedding_base import EmbeddingModelBase
from ..message import (
    VideoBlock,
    ImageBlock,
    TextBlock,
)


class DashScopeMultiModalEmbedding(EmbeddingModelBase):
    """The DashScope multimodal embedding API, supporting text, image and
    video embedding."""

    supported_modalities: list[str] = ["text", "image", "video"]
    """This class supports text, image and video input."""

    def __init__(
        self,
        api_key: str,
        model_name: str,
        embedding_cache: EmbeddingCacheBase | None = None,
    ) -> None:
        """Initialize the DashScope multimodal embedding model class.

        Args:
            api_key (`str`):
                The dashscope API key.
            model_name (`str`):
                The name of the embedding model, e.g. "multimodal-embedding-v1"
            embedding_cache (`EmbeddingCacheBase`):
                The embedding cache class instance, used to cache the
                embedding results to avoid repeated API calls.
        """
        super().__init__(model_name)

        self.api_key = api_key
        self.embedding_cache = embedding_cache

    async def __call__(
        self,
        input: list[TextBlock | ImageBlock | VideoBlock],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the DashScope multimodal embedding API, which accepts text,
        image, and video data.

        Args:
            input (`list[TextBlock | ImageBlock | VideoBlock]`):
                The input data to be embedded. It can be a list of text,
                image, and video blocks.

        Returns:
            `EmbeddingResponse`:
                The embedding response object, which contains the embeddings
                and usage information.
        """
        # check data type
        formatted_data = []
        for _ in input:
            if (
                not isinstance(_, dict)
                or "type" not in _
                or _["type"]
                not in [
                    "text",
                    "image",
                    "video",
                ]
            ):
                raise ValueError(
                    f"Invalid data : {_}. It should be a list of "
                    "TextBlock, ImageBlock, or VideoBlock.",
                )
            if (
                _["type"] == "video"
                and _.get("source", {}).get("type") != "url"
            ):
                raise ValueError(
                    f"The multimodal embedding API only supports URL input "
                    f"for video data, but got {_}.",
                )

            if _["type"] == "text":
                if "text" not in _:
                    raise ValueError(
                        f"Invalid text block: {_}. It should contain a "
                        f"'text' field.",
                    )
                formatted_data.append({"text": _["text"]})

            elif _["type"] == "video":
                formatted_data.append({"video": _["source"]["url"]})

            elif (
                _["type"] == "image"
                and "source" in _
                and _["source"].get("type") in ["base64", "url"]
            ):
                typ = _["source"]["type"]
                if typ == "base64":
                    formatted_data.append(
                        {
                            "image": f'data:{_["source"]["media_type"]};'
                                     f'base64,{_["source"]["data"]}'
                        },
                    )
                elif typ == "url":
                    formatted_data.append(
                        {"image": _["source"]["url"]},
                    )
            else:
                raise ValueError(
                    f"Invalid block {_}. It should be a valid TextBlock, "
                    f"ImageBlock, or VideoBlock.",
                )

        kwargs = {
            "model": self.model_name,
            "input": formatted_data,
            **kwargs,
        }

        # Search in cache first
        if self.embedding_cache:
            cached_embeddings = await self.embedding_cache.retrieve(
                identifier=kwargs,
            )
            if cached_embeddings:
                return EmbeddingResponse(
                    embeddings=cached_embeddings,
                    usage=EmbeddingUsage(
                        tokens=0,
                        time=0,
                    ),
                    source="cache",
                )

        import dashscope

        start_time = datetime.now()
        res = dashscope.MultiModalEmbedding.call(**kwargs)
        time = (datetime.now() - start_time).total_seconds()

        if res.status_code != 200:
            raise RuntimeError(
                f"Failed to get embedding from DashScope API: {res}",
            )

        return EmbeddingResponse(
            embeddings=[_["embedding"] for _ in res.output["embeddings"]],
            usage=EmbeddingUsage(
                tokens=res.usage.get(
                    "image_tokens",
                    0,
                )
                + res.usage.get(
                    "input_tokens",
                    0,
                ),
                time=time,
            ),
            source="api",
        )
