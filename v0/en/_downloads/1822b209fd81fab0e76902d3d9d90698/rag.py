# -*- coding: utf-8 -*-
"""
.. _rag:

Retrieval Augmentation Generation (RAG)
==================================================================

Agentscope has built-in supports for the retrieval augmentation generation
(RAG). There are two key modules related to RAG in AgentScope: `Knowledge` and
`KnowledgeBank`.

Create and Use Knowledge Instances
----------------------------------------------

While `Knowledge` is a base class, a specific built-in knowledge class is in
the AgentScope now. (Online search is coming soon.)


- `LlamaIndexKnowledge`: Designed to work with one of the most popular RAG library `LlamaIndex <https://www.llamaindex.ai/>`_ as local knowledge, and supporting most of LlamaIndex functionality by configuration.


Create a `LlamaIndexKnowledge` instance
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A quick start to create a `LlamaIndexKnowledge` instance is to use the `build_knowledge_instance` function.
There are three parameters need to be passed to the function.

- `knowledge_id`: a unique identifier for this knowledge instance

- `data_dirs_and_types`: a dictionary whose keys are strings of directories of the data, and values are the file extensions of the data

- `emb_model_config_name`: name of the configuration of a embedding model in AgentScope (need to be initialized in AgentScope beforehand)

A simple example is as follows.
"""
import os
import agentscope
from agentscope.rag.llama_index_knowledge import LlamaIndexKnowledge

agentscope.init(
    model_configs=[
        {
            "model_type": "dashscope_text_embedding",
            "config_name": "qwen_emb_config",
            "model_name": "text-embedding-v2",
            "api_key": os.getenv("DASHSCOPE_API_KEY"),
        },
    ],
)

local_knowledge = LlamaIndexKnowledge.build_knowledge_instance(
    knowledge_id="agentscope_qa",
    data_dirs_and_types={"./": [".md"]},
    emb_model_config_name="qwen_emb_config",
)


nodes = local_knowledge.retrieve(
    "what is agentscope?",
    similarity_top_k=1,
)


print(f"\nThe retrieved content:\n{nodes[0].content}")


# %%
# If one wants to have more control on how the data are preprocessing,
# a knowledge configuration can be passed to the function.
# Especially, `SimpleDirectoryReader` is the class in LlamaIndex library, and `init_args` is the initialization parameters of `SimpleDirectoryReader`.
# As for the data preprocessing, developers can choose different LlamaIndex `transformation operations <https://docs.llamaindex.ai/en/stable/module_guides/loading/ingestion_pipeline/transformations/>`_ to preprocess the data.

flex_knowledge_config = {
    "knowledge_id": "agentscope_qa_flex",
    "knowledge_type": "llamaindex_knowledge",
    "emb_model_config_name": "qwen_emb_config",
    "chunk_size": 1024,
    "chunk_overlap": 40,
    "data_processing": [
        {
            "load_data": {
                "loader": {
                    "create_object": True,
                    "module": "llama_index.core",
                    "class": "SimpleDirectoryReader",
                    "init_args": {
                        "input_dir": "./",
                        "required_exts": [
                            ".md",
                        ],
                    },
                },
            },
            "store_and_index": {
                "transformations": [
                    {
                        "create_object": True,
                        "module": "llama_index.core.node_parser",
                        "class": "SentenceSplitter",
                        "init_args": {
                            "chunk_size": 1024,
                        },
                    },
                ],
            },
        },
    ],
}

local_knowledge_flex = LlamaIndexKnowledge.build_knowledge_instance(
    knowledge_id="agentscope_qa_flex",
    knowledge_config=flex_knowledge_config,
)


nodes = local_knowledge.retrieve(
    "what is agentscope?",
    similarity_top_k=1,
)

print(f"\nThe retrieved content:\n{nodes[0].content}")


# %%
# Create a Batch of Knowledge Instances
# ----------------------------------------------
# For some cases where different knowledge sources exists and require different preprocessing and/or post-proprocess, a good strategy is to create multiple knolwedge instances.
# Thus, we introduce `KnowledgeBank` to better manage the knowledge instances. One can initialize a batch of knowledge with a file of mulltiple knodledge configurations.
#
# .. code-block:: python
#
#    knowledge_bank = KnowledgeBank(configs=path_to_knowledge_configs_json)
#
#
# Alternatively, one can add knowledge instance dynamically to knowledge bank as well.
#
# .. code-block:: python
#
#   knowledge_bank.add_data_as_knowledge(
#        knowledge_id="agentscope_tutorial_rag",
#        emb_model_name="qwen_emb_config",
#        data_dirs_and_types={
#            "../../docs/sphinx_doc/en/source/tutorial": [".md"],
#        },
#    )
#
# Here, the `knowledge_id` should be unique.
# If developers have their new knowledge class, they can register the new class beforehand
#
# .. code-block:: python
#
#    from your_knowledge import NewKnowledgeClass1, NewKnowledgeClass2
#    knowledge_bank = KnowledgeBank(
#      configs="configs/knowledge_config.json",
#      new_knowledge_types=[NewKnowledgeClass1, NewKnowledgeClass2]
#    )
#    # or
#    knowledge_bank.register_knowledge_type(NewKnowledgeClass2)

# %%
# (Optional) Setting up a local embedding model service
# -----------------------------------------------------------
#
# For those who are interested in setting up a local embedding service, we provide the following example based on the
# `sentence_transformers` package, which is a popular specialized package for embedding models (based on the `transformer` package and compatible with both HuggingFace and ModelScope models).
# In this example, we will use one of the SOTA embedding models, `gte-Qwen2-7B-instruct`.
#
# * Step 1: Follow the instruction on `HuggingFace <https://huggingface.co/Alibaba-NLP/gte-Qwen2-7B-instruct>`_ or `ModelScope <https://www.modelscope.cn/models/iic/gte_Qwen2-7B-instruct >"_ to download the embedding model.
#   (For those who cannot access HuggingFace directly, you may want to use a HuggingFace mirror by running a bash command
#     `export HF_ENDPOINT=https://hf-mirror.com` or add a line of code `os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"` in your Python code.)
# * Step 2: Set up the server. The following code is for reference.
#
# .. code-block:: python
#
#     import datetime
#     import argparse
#
#     from flask import Flask
#     from flask import request
#     from sentence_transformers import SentenceTransformer
#
#     def create_timestamp(format_: str = "%Y-%m-%d %H:%M:%S") -> str:
#         """Get current timestamp."""
#         return datetime.datetime.now().strftime(format_)
#
#     app = Flask(__name__)
#
#     @app.route("/embedding/", methods=["POST"])
#     def get_embedding() -> dict:
#         """Receive post request and return response"""
#         json = request.get_json()
#
#         inputs = json.pop("inputs")
#
#         global model
#
#         if isinstance(inputs, str):
#             inputs = [inputs]
#
#         embeddings = model.encode(inputs)
#
#         return {
#             "data": {
#                 "completion_tokens": 0,
#                 "messages": {},
#                 "prompt_tokens": 0,
#                 "response": {
#                     "data": [
#                         {
#                             "embedding": emb.astype(float).tolist(),
#                         }
#                         for emb in embeddings
#                     ],
#                     "created": "",
#                     "id": create_timestamp(),
#                     "model": "flask_model",
#                     "object": "text_completion",
#                     "usage": {
#                         "completion_tokens": 0,
#                         "prompt_tokens": 0,
#                         "total_tokens": 0,
#                     },
#                 },
#                 "total_tokens": 0,
#                 "username": "",
#             },
#         }
#
#     if __name__ == "__main__":
#         parser = argparse.ArgumentParser()
#         parser.add_argument("--model_name_or_path", type=str, required=True)
#         parser.add_argument("--device", type=str, default="auto")
#         parser.add_argument("--port", type=int, default=8000)
#         args = parser.parse_args()
#
#         global model
#
#         print("setting up for embedding model....")
#         model = SentenceTransformer(
#             args.model_name_or_path
#         )
#
#         app.run(port=args.port)
#
#
# * Step 3: start server.
#
# .. code-block:: bash
#
#     python setup_ms_service.py --model_name_or_path {$PATH_TO_gte_Qwen2_7B_instruct}
#
#
#
# Testing whether the model is running successfully.
#
# .. code-block:: python
#
#     from agentscope.models.post_model import PostAPIEmbeddingWrapper
#
#
#     model = PostAPIEmbeddingWrapper(
#         config_name="test_config",
#         api_url="http://127.0.0.1:8000/embedding/",
#         json_args={
#             "max_length": 4096,
#             "temperature": 0.5
#         }
#     )
#
#     print(model("testing"))
#
#
#
