# -*- coding: utf-8 -*-
"""Basic class for system prompt generator."""

from abc import ABC
import random

from typing import Any, List, Literal, Optional, Union

from loguru import logger
from tqdm import tqdm

from agentscope.manager import FileManager, ModelManager
from agentscope.message import Msg
from agentscope.model import ModelResponse
from agentscope.prompt._prompt_utils import _find_top_k_embeddings


class _SentencePieceEmbeddingModel:
    """The wrapper class for the sentence_transformers library. It is used to
    generate embeddings for the examples locally.

    Note:
        To download the model, you need to be accessible to the huggingface.
    """

    def __init__(self, model_name_or_path: str) -> None:
        """The constructor of the SentencePieceEmbeddingModel.

        Args:
            model_name_or_path (`str`):
                The name or path of the model. Full model list refers to
                https://www.sbert.net/docs/sentence_transformer/pretrained_models.html#original-models
        """

        self.model = None
        self.model_name_or_path = model_name_or_path

    def __call__(self, queries: Union[str, List[str]]) -> Any:
        # Lazy loading the model
        if self.model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise ImportError(
                    "The sentence-transformers library is required. "
                    "Install it with `pip install sentence-transformers`.",
                ) from e

            logger.info(
                f"Loading local embedding model: {self.model_name_or_path}",
            )
            self.model = SentenceTransformer(self.model_name_or_path)
            logger.info("Finish loading the local embedding model.")

        embedding = self.model.encode(queries)
        return ModelResponse(embedding=[embedding])


class SystemPromptGeneratorBase(ABC):
    """Base class for system prompt generator, which receives the users'
    input and returns an optimized system prompt in the `optimize`
    method."""

    def __init__(
        self,
        model_config_name: str,
        meta_prompt: str = None,
        response_prompt_template: str = None,
        example_num: int = 0,
        example_list: Optional[list] = None,
        example_selection_strategy: Literal["random", "similarity"] = "random",
        embed_model_config_name: Optional[str] = None,
        example_prompt_template: Optional[str] = None,
        local_embedding_model: str = None,
    ) -> None:
        """The constructor of the SystemPromptOptimizer, which uses the
        specified model and meta prompt to optimize the users' system prompt.

        Args:
            model_config_name (`str`):
                The name of the model config, which is used to load the model
                from the configuration.
            meta_prompt (`str`):
                The meta prompt used to optimize the users' system prompt.
            response_prompt_template (`Optional[str]`):
                The prompt template used to remind the LLM to generate the
                optimized system prompt.
            example_num (`int`):
                The number of examples that will be attached to the end of the
                meta prompt. If `0`, no examples will be attached.
            example_list (`List`):
                The candidate examples that will be chosen from. AgentScope
                provides a default list of examples.
            example_selection_strategy (`Literal["random", "similarity"]`):
                The strategy used to select examples.
            embed_model_config_name (`str`):
                If the example selection method is `"similarity"`, an embedding
                model config name is required.
        """
        model_manager = ModelManager.get_instance()
        self.model = model_manager.get_model_by_config_name(model_config_name)
        self.meta_prompt = meta_prompt
        self.response_prompt_template = response_prompt_template

        # example related
        self.example_num = example_num
        self.example_list = example_list or []
        self.example_selection_strategy = example_selection_strategy
        self.example_prompt_template = example_prompt_template

        # assert example_num <= len(example_list)
        if self.example_num > len(self.example_list):
            raise ValueError(
                f"The number of examples to select ({self.example_num}) "
                f"is larger than the candidate examples provided "
                f"({len(self.example_list)}).",
            )

        # Used to cache the embeddings of the examples.
        self.embed_model_name = None
        self.example_embeddings = None
        self.local_embedding_model = local_embedding_model
        # Load embed model if needed
        if (
            self.example_num > 0
            and self.example_selection_strategy == "similarity"
        ):
            if embed_model_config_name is None:
                logger.info(
                    f"Embedding model config name is not provided, a default "
                    f'local embedding model "{self.local_embedding_model}" '
                    f"will be used.",
                )
                self.embed_model = _SentencePieceEmbeddingModel(
                    self.local_embedding_model,
                )
                self.embed_model_name = self.local_embedding_model
            else:
                model_manager = ModelManager.get_instance()
                self.embed_model = model_manager.get_model_by_config_name(
                    embed_model_config_name,
                )
                self.embed_model_name = model_manager.get_config_by_name(
                    embed_model_config_name,
                )

            self.example_embeddings = self._generate_embeddings()

    def _get_example_prompt(self, examples: List[dict]) -> str:
        """Get the prompt examples"""
        examples_prompt = []
        for index, example in enumerate(examples):
            values = {"index": index + 1, **example}
            examples_prompt.append(
                self.example_prompt_template.format_map(values),
            )
        return "\n".join(examples_prompt)

    def _select_example(self, user_prompt: str) -> List:
        """Select the examples that are most similar with the given user query

        Args:
            user_prompt (`str`):
                The user query used to select the examples.

        Returns:
            `List`:
                The selected examples.
        """
        if self.example_selection_strategy == "random":
            return self._select_random_example()
        elif self.example_selection_strategy == "similarity":
            return self._select_similar_example(user_prompt)
        else:
            raise ValueError(
                f"Invalid example selection method "
                f"{self.example_selection_strategy}",
            )

    def _select_similar_example(self, user_prompt: str) -> List:
        """Select the examples using embedding similarity

        Args:
            user_prompt (`str`):
                The user query used to select the examples.

        Returns:
            `List`:
                The selected examples.
        """
        # Get the human query embed using the embedding model
        human_query_embed = self.embed_model(user_prompt).embedding[0]

        # TODO: use the retrieval service instead rather than achieving it
        #  locally
        selected_indices = _find_top_k_embeddings(
            human_query_embed,
            self.example_embeddings,
            self.example_num,
        )

        return [self.example_list[_] for _ in selected_indices]

    def _select_random_example(self) -> List:
        """Select the examples randomly."""
        return random.sample(
            self.example_list,
            self.example_num,
        )

    def _generate_embeddings(self) -> List:
        """Generate embeddings for the examples."""
        example_embeddings = []
        for example in tqdm(self.example_list, desc="Generating embeddings"):
            user_prompt = example["user_prompt"]

            # Load cached embedding instead of generating them again
            file_manager = FileManager.get_instance()
            cached_embedding = file_manager.fetch_cached_text_embedding(
                text=user_prompt,
                embedding_model=self.embed_model_name,
            )
            if cached_embedding is None:
                new_embedding = self.embed_model(user_prompt).embedding[0]
                example_embeddings.append(new_embedding)
                # Cache the embedding
                file_manager.cache_text_embedding(
                    text=user_prompt,
                    embedding=new_embedding,
                    embedding_model=self.embed_model_name,
                )
            else:
                example_embeddings.append(cached_embedding)
        return example_embeddings

    def generate(self, user_input: str) -> str:
        """Generate (optimized) system prompt according to the user input,
        which could be a user's system prompt or query.


        Args:
            user_input (`str`):
                The user input, could be user's system prompt or query, e.g.
                "Write a system prompt for a chatbot".

        Returns:
            `str`:
                The optimized system prompt.
        """
        # Select examples
        examples = self._select_example(user_input)

        # Format the prompt
        formatted_prompt = "\n".join(
            [
                self.meta_prompt,
                self._get_example_prompt(examples),
                self.response_prompt_template.format(user_prompt=user_input),
            ],
        )

        prompt = self.model.format(
            Msg(
                "user",
                formatted_prompt,
                role="user",
            ),
        )

        # Generate the optimized prompt
        response = self.model(prompt).text

        return response
