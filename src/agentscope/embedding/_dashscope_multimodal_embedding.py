# -*- coding: utf-8 -*-
"""The dashscope multimodal embedding model in agentscope."""
from datetime import datetime
from typing import Any, Literal

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
        dimensions: int | None = None,
        embedding_cache: EmbeddingCacheBase | None = None,
    ) -> None:
        """Initialize the DashScope multimodal embedding model class.

        Args:
            api_key (`str`):
                The dashscope API key.
            model_name (`str`):
                The name of the embedding model, e.g. "multimodal-embedding-
                v1", "tongyi-embedding-vision-plus".
            dimensions (`int`, defaults to 1024):
                The dimension of the embedding vector, refer to the
                `official documentation
                <https://bailian.console.aliyun.com/?tab=api#/api/?type=model&url=2712517>`_
                for more details.
            embedding_cache (`EmbeddingCacheBase`):
                The embedding cache class instance, used to cache the
                embedding results to avoid repeated API calls.
        """
        path_doc = (
            "https://bailian.console.aliyun.com/?tab=api#/api/?type=model&"
            "url=2712517"
        )
        self.batch_size_limit = 1

        if model_name.startswith("tongyi-embedding-vision-plus"):
            self.batch_size_limit = 8
            if dimensions is None:
                dimensions = 1152
            elif dimensions != 1152:
                raise ValueError(
                    f"The dimension of model {model_name} must be  1152, "
                    "refer to the official documentation for more details: "
                    f"{path_doc}",
                )
        if model_name.startswith("tongyi-embedding-vision-flash"):
            self.batch_size_limit = 8
            if dimensions is None:
                dimensions = 768
            elif dimensions != 768:
                raise ValueError(
                    f"The dimension of model {model_name} must be  768, "
                    "refer to the official documentation for more details: "
                    f"{path_doc}",
                )
        if model_name.startswith("multimodal-embedding-v"):
            if dimensions is None:
                dimensions = 1024
            elif dimensions != 1024:
                raise ValueError(
                    f"The dimension of model {model_name} must be  1024, "
                    "refer to the official documentation for more details: "
                    f"{path_doc}",
                )
        refined_dimensions: int = 1024
        if dimensions is not None:
            refined_dimensions = dimensions
        super().__init__(model_name, refined_dimensions)

        self.api_key = api_key
        self.embedding_cache = embedding_cache

    async def __call__(
        self,
        inputs: list[TextBlock | ImageBlock | VideoBlock],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the DashScope multimodal embedding API, which accepts text,
        image, and video data.

        Args:
            inputs (`list[TextBlock | ImageBlock | VideoBlock]`):
                The input data to be embedded. It can be a list of text,
                image, and video blocks.

        Returns:
            `EmbeddingResponse`:
                The embedding response object, which contains the embeddings
                and usage information.
        """
        # check data type
        formatted_data = []
        for _ in inputs:
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
                assert "text" in _, (
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
                            f'base64,{_["source"]["data"]}',
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

        # Handle the batch size limit of the DashScope multimodal embedding API
        collected_embeddings = []
        collected_time = 0.0
        collected_tokens = 0
        collected_source: Literal["cache", "api"] = "cache"
        for _ in range(0, len(formatted_data), self.batch_size_limit):
            batch_data = formatted_data[_ : _ + self.batch_size_limit]
            batch_kwargs = {
                "input": batch_data,
                "model": self.model_name,
                **kwargs,
            }
            res = await self._call_api(batch_kwargs)

            collected_embeddings.extend(res.embeddings)
            collected_time += res.usage.time
            if res.usage.tokens:
                collected_tokens += res.usage.tokens
            if res.source == "api":
                collected_source = "api"

        return EmbeddingResponse(
            embeddings=collected_embeddings,
            usage=EmbeddingUsage(
                tokens=collected_tokens,
                time=collected_time,
            ),
            source=collected_source,
        )

    async def _call_api(self, kwargs: dict[str, Any]) -> EmbeddingResponse:
        """
        Call the DashScope multimodal embedding API by the given arguments.
        """
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

        kwargs["api_key"] = self.api_key

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
