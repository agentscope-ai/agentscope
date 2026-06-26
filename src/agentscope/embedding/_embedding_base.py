# -*- coding: utf-8 -*-
"""The embedding model base class."""
from __future__ import annotations

import asyncio
import inspect
from abc import abstractmethod
from pathlib import Path
from typing import Any, Generic, TypeVar, Type, Union

from pydantic import BaseModel, ConfigDict

from ._embedding_model_card import EmbeddingModelCard
from ._embedding_response import EmbeddingResponse
from ._embedding_usage import EmbeddingUsage
from .._logging import logger
from ..credential import CredentialBase
from ..message import DataBlock, TextBlock

#: Type variable for embedding input elements.
#:
#: Bound to the union of all element shapes the framework supports;
#: each concrete subclass narrows it to its accepted input via
#: ``class Foo(EmbeddingModelBase[str | TextBlock]): ...`` so callers
#: get accurate IDE completion and type-checking on ``__call__``.
#:
#: ``__call__`` accepts ``TextBlock`` and unpacks it to ``.text``
#: before invoking :meth:`_call_api`; therefore ``_call_api``'s input
#: type is intentionally decoupled from :data:`InputT` (it is typed
#: ``list[Any]`` on the base and narrowed by each subclass).
InputT = TypeVar("InputT", bound=Union[str, TextBlock, DataBlock])


class EmbeddingModelBase(Generic[InputT]):
    """Base class for embedding models.

    Generic over :data:`InputT` so that text-only subclasses
    (``EmbeddingModelBase[str]``) and multimodal subclasses
    (``EmbeddingModelBase[str | TextBlock | DataBlock]``) expose the
    correct ``inputs`` type to the IDE.

    Follows the same pattern as :class:`~agentscope.model.ChatModelBase`:

    - ``__call__`` splits inputs into batches of size
      :attr:`batch_size`, calls :meth:`_call_api` for each batch
      **concurrently** via :func:`asyncio.gather`, and merges the
      results.  Each batch call is wrapped with retry logic.
    - Subclasses only implement :meth:`_call_api` for a **single
      batch** — no batching or retry code needed.
    - Each subclass may override :meth:`_get_retryable_exceptions` to
      declare provider-specific retriable errors.
    """

    class Parameters(BaseModel):
        """Provider-specific tunables for embedding models.

        Intentionally empty in the base — ``dimensions`` is a contract
        property of an embedding model (its output vector size), not a
        tunable knob, so it lives directly on the instance via the
        required :paramref:`__init__.dimensions` argument.  Subclasses
        extend this class to expose **non-dimensional** knobs (e.g.
        Gemini's ``task_type``, Dashscope's ``text_type``).

        ``extra="allow"`` is set so old persisted configs (where
        ``dimensions`` lived in ``parameters``) keep deserialising
        without raising; :meth:`EmbeddingModelBase.__init__` extracts
        it back out for backward compatibility.
        """

        model_config = ConfigDict(extra="allow")

    credential: CredentialBase
    """The API credential."""

    model: str
    """The embedding model name."""

    dimensions: int
    """The output embedding vector dimensions.

    Set directly from the :paramref:`__init__.dimensions` argument —
    a required, first-class field rather than something derived from
    :attr:`parameters`.
    """

    context_size: int
    """Maximum input length (in tokens) per single input item."""

    batch_size: int
    """Maximum number of input items per API call."""

    max_retries: int
    """The maximum number of retries for the underlying API."""

    retry_delay: float
    """Seconds to sleep between retry attempts."""

    supports_multimodal: bool = False
    """Whether this model instance accepts :class:`DataBlock` inputs in
    addition to text.  Text-only models keep the default ``False``;
    multimodal subclasses must set it to ``True`` (per instance when
    routing depends on the model name)."""

    def __init__(
        self,
        credential: CredentialBase,
        model: str,
        dimensions: int | None,
        parameters: BaseModel | None,
        context_size: int,
        batch_size: int,
        max_retries: int,
        retry_delay: float,
    ) -> None:
        """Initialize the embedding model base class.

        Args:
            credential (`CredentialBase`):
                The API credential used for authentication.
            model (`str`):
                The name of the embedding model.
            dimensions (`int | None`):
                The output embedding vector dimensions for this
                instance.  Required and first-class — see the class
                docstring for the rationale of keeping ``dimensions``
                outside :class:`Parameters`.  For backward compatibility
                with older configs that stored ``dimensions`` inside
                :class:`Parameters`, ``None`` is accepted at the
                signature level and is back-filled from
                ``parameters.dimensions`` when present.
            parameters (`BaseModel | None`):
                Provider-specific non-dimensional parameters.  When
                ``None``, the default ``Parameters()`` is used.
            context_size (`int`):
                Maximum input length (in tokens) per single input item.
            batch_size (`int`):
                Maximum number of input items per API call.  When
                ``__call__`` receives more items, it splits them into
                batches and calls :meth:`_call_api` concurrently.
            max_retries (`int`):
                The maximum number of retries for each batch API call.
                Only exceptions listed in
                :meth:`_get_retryable_exceptions` count against this
                budget.
            retry_delay (`float`):
                Seconds to sleep between retry attempts.
        """
        resolved_parameters = parameters or self.Parameters()
        # Backward-compat: older session/KB configs persisted
        # ``dimensions`` inside ``parameters``.  Promote it to the
        # constructor argument when the caller did not pass one
        # explicitly, then strip it from the parameters object so it
        # never reaches provider-specific request payloads.
        param_dump = resolved_parameters.model_dump()
        legacy_dimensions = param_dump.pop("dimensions", None)
        if dimensions is None:
            if legacy_dimensions is None:
                raise ValueError(
                    "dimensions is required: pass it explicitly to "
                    "EmbeddingModelBase.__init__ or include it in the "
                    "legacy `parameters` mapping.",
                )
            dimensions = int(legacy_dimensions)
            resolved_parameters = type(resolved_parameters)(**param_dump)
        elif legacy_dimensions is not None:
            # Both routes set it — explicit constructor wins, strip the
            # legacy mirror so it can't drift.
            resolved_parameters = type(resolved_parameters)(**param_dump)

        if dimensions <= 0:
            raise ValueError(
                f"dimensions must be a positive integer, got {dimensions}.",
            )

        self.credential = credential
        self.model = model
        self.dimensions = dimensions
        self.parameters = resolved_parameters
        self.context_size = context_size
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    @classmethod
    def _get_retryable_exceptions(cls) -> tuple[Type[Exception], ...]:
        """Return exception types that should trigger a retry.

        Defaults to an empty tuple (no retries).  Subclasses can
        override to declare provider-specific retryable exceptions.
        """
        return ()

    # ------------------------------------------------------------------
    # Public API — batching + concurrent retry
    # ------------------------------------------------------------------

    async def __call__(
        self,
        inputs: list[InputT],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Embed a list of inputs with automatic batching and retry.

        The inputs are split into chunks of :attr:`batch_size`.  All
        chunks are dispatched **concurrently** via
        :func:`asyncio.gather`.  Each chunk is individually retried up
        to ``max_retries`` times on retryable errors.  Results are
        merged into a single :class:`EmbeddingResponse` preserving the
        original input order.

        Args:
            inputs (`list[InputT]`):
                The input data to embed.  For text-only models this is
                ``list[str]``; for multimodal models it is
                ``list[str | TextBlock | DataBlock]``.  Any
                :class:`TextBlock` items are transparently unpacked to
                their ``.text`` field on entry, so subclasses' batching
                and ``_call_api`` only have to handle ``str`` (and
                ``DataBlock`` for multimodal variants).
            **kwargs:
                Additional keyword arguments forwarded to
                :meth:`_call_api`.

        Returns:
            `EmbeddingResponse`:
                A merged response containing embeddings for all inputs.
        """
        if not inputs:
            return EmbeddingResponse(
                embeddings=[],
                usage=EmbeddingUsage(tokens=0, time=0),
            )

        normalized: list[Any] = [
            item.text if isinstance(item, TextBlock) else item
            for item in inputs
        ]

        # Split into batches.
        batches = [
            normalized[i : i + self.batch_size]
            for i in range(0, len(normalized), self.batch_size)
        ]

        if len(batches) > 1:
            logger.info(
                "Embedding %d inputs in %d batches (batch_size=%d) "
                "for model %s.",
                len(normalized),
                len(batches),
                self.batch_size,
                self.model,
            )

        # Dispatch all batches concurrently, each with retry.
        results: list[EmbeddingResponse] = await asyncio.gather(
            *(self._call_with_retry(batch, **kwargs) for batch in batches),
        )

        return self._merge_responses(results)

    # ------------------------------------------------------------------
    # Internal — merge multiple batch responses
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_responses(
        responses: list[EmbeddingResponse],
    ) -> EmbeddingResponse:
        """Merge multiple batch :class:`EmbeddingResponse` objects into
        one, preserving input order.

        Args:
            responses (`list[EmbeddingResponse]`):
                Batch responses to merge.

        Returns:
            `EmbeddingResponse`: The merged response.
        """
        if len(responses) == 1:
            return responses[0]

        all_embeddings: list = []
        total_tokens = 0
        total_time = 0.0

        for resp in responses:
            all_embeddings.extend(resp.embeddings)
            if resp.usage:
                total_time += resp.usage.time
                if resp.usage.tokens:
                    total_tokens += resp.usage.tokens

        return EmbeddingResponse(
            embeddings=all_embeddings,
            usage=EmbeddingUsage(
                tokens=total_tokens,
                time=total_time,
            ),
            source="api",
        )

    # ------------------------------------------------------------------
    # Internal — retry wrapper for a single batch
    # ------------------------------------------------------------------

    async def _call_with_retry(
        self,
        inputs: list[Any],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call :meth:`_call_api` with retry logic for a single batch.

        Args:
            inputs (`list[Any]`):
                A single batch of inputs (size ≤ ``batch_size``), already
                normalised by :meth:`__call__` (any :class:`TextBlock`
                items unpacked to their ``.text``). Typed as
                ``list[Any]`` because the concrete element shape depends
                on the subclass — see :meth:`_call_api`.
            **kwargs:
                Forwarded to :meth:`_call_api`.
        """
        retryable = tuple(self._get_retryable_exceptions())
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                return await self._call_api(inputs, **kwargs)
            except Exception as e:
                if not isinstance(e, retryable):
                    raise
                last_error = e
                if attempt < self.max_retries:
                    logger.warning(
                        "Batch attempt %d failed for embedding model "
                        "%s: %s. Retrying in %.1fs...",
                        attempt + 1,
                        self.model,
                        str(e),
                        self.retry_delay,
                    )
                    await asyncio.sleep(self.retry_delay)
                else:
                    logger.warning(
                        "All %d attempt(s) failed for a batch of "
                        "embedding model %s.",
                        self.max_retries + 1,
                        self.model,
                    )

        if last_error is not None:
            raise last_error
        raise RuntimeError(
            f"Failed to call embedding model {self.model} after "
            f"{self.max_retries + 1} retries.",
        )

    # ------------------------------------------------------------------
    # Abstract — subclasses implement this for a single batch
    # ------------------------------------------------------------------

    @abstractmethod
    async def _call_api(
        self,
        inputs: list[Any],
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """Call the underlying embedding API for a **single batch**.

        Subclasses must implement this method.  The batch splitting,
        concurrency, and retry logic are handled by :meth:`__call__`
        — this method only needs to handle one API call.

        .. note::
            The parameter is typed ``list[Any]`` rather than
            ``list[InputT]`` because :meth:`__call__` unpacks
            :class:`TextBlock` items to their ``.text`` field *before*
            dispatching to this method.  The element shape this method
            actually receives is therefore subclass-specific
            (:data:`InputT` minus :class:`TextBlock`).  Subclasses
            should override with their concrete narrower type, e.g.
            ``list[str]`` for text-only models or
            ``list[str | DataBlock]`` for multimodal ones.

        Args:
            inputs (`list[Any]`):
                A batch of inputs (guaranteed ``len(inputs) <=
                self.batch_size``).
            **kwargs:
                Additional keyword arguments.

        Returns:
            `EmbeddingResponse`:
                The embedding response for this batch.
        """

    # ------------------------------------------------------------------
    # Model card discovery
    # ------------------------------------------------------------------

    @classmethod
    def list_models(
        cls,
        custom_yaml_dir: str | None = None,
    ) -> list[EmbeddingModelCard]:
        """List candidate embedding models from YAML files.

        Each concrete subclass should live in its own provider
        subdirectory (e.g. ``embedding/_openai/_model.py``) with a
        sibling ``_models/`` directory containing YAML files — identical
        to the layout used by :class:`~agentscope.model.ChatModelBase`.

        Args:
            custom_yaml_dir (`str | None`):
                Override the YAML directory.

        Returns:
            `list[EmbeddingModelCard]`:
                A list of embedding model cards.
        """
        if custom_yaml_dir is None:
            subclass_file = Path(inspect.getfile(cls))
            yaml_dir = subclass_file.parent / "_models"
        else:
            yaml_dir = Path(custom_yaml_dir)

        if not yaml_dir.is_dir():
            return []

        yaml_files = list(yaml_dir.glob("*.yaml"))

        model_cards = []
        for yaml_file in yaml_files:
            try:
                card = EmbeddingModelCard.from_yaml(
                    yaml_path=str(yaml_file),
                    parameter_class=cls.Parameters,
                )
                model_cards.append(card)
            except Exception as e:
                logger.warning(
                    "Failed to load embedding model card %s: %s",
                    yaml_file,
                    str(e),
                )
                continue

        return model_cards
