# -*- coding: utf-8 -*-
"""
.. _rag:

检索增强生成（RAG）
==================================================================

Agentscope 内置了对检索增强生成（RAG）的支持。AgentScope 中与 RAG 相关的两个关键模块是：Knowledge 和 KnowledgeBank。

创建和使用知识(Knowledge)实例
----------------------------------------------

虽然 Knowledge 是一个基类，但 AgentScope 中目前有一个具体的内置知识类。（在线搜索会知识类会很快更新。）

- LlamaIndexKnowledge：旨在与最流行的 RAG 库之一 `LlamaIndex <https://www.llamaindex.ai/>`_ 协同工作，用作本地知识，并通过配置支持 LlamaIndex 的大部分功能。


创建一个 LlamaIndexKnowledge 实例
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

快速创建一个 LlamaIndexKnowledge 实例的方法是使用 build_knowledge_instance 函数。
该函数需要传入三个参数：

knowledge_id：该知识实例的唯一标识符

data_dirs_and_types：一个字典，其键为数据所在目录的字符串，值为数据文件的扩展名

emb_model_config_name：AgentScope 中embedding模型配置的名称（需要在 AgentScope 中预先初始化）

一个简单的例子如下。
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
# 如果希望对数据的预处理有更多的控制，
# 可以将一个知识配置传递给该函数。
# 特别地，`SimpleDirectoryReader` 是 LlamaIndex 库中的一个类，而 `init_args` 是 `SimpleDirectoryReader` 的初始化参数。
# 至于数据预处理，开发者可以选择不同的 LlamaIndex `transformation operations <https://docs.llamaindex.ai/en/stable/module_guides/loading/ingestion_pipeline/transformations/>`_ 来对数据进行预处理。


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
# For some cases where different knowledge sources exists and require different preprocessing and/or post-process, a good strategy is to create multiple knowledge instances.
# Thus, we introduce `KnowledgeBank` to better manage the knowledge instances. One can initialize a batch of knowledge with a file of multiple knowledge configurations.
#
# .. code-block:: python
#
#    knowledge_bank = KnowledgeBank(configs=path_to_knowledge_configs_json)
#
#
# 或者，也可以将知识实例动态地添加到知识库中。
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
# 这里，`knowledge_id` 应该是唯一的。
# 如果开发者有他们自己的新知识类，可以预先注册该新类。
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
# (拓展) 架设自己的embedding model服务
# -----------------------------------------------------------
#
# 我们在此也对架设本地embedding model感兴趣的用户提供以下的样例。
# 以下样例基于在embedding model范围中很受欢迎的`sentence_transformers` 包（基于`transformer` 而且兼容HuggingFace和ModelScope的模型）。
# 这个样例中，我们会使用当下最好的文本向量模型之一`gte-Qwen2-7B-instruct`。
#
# * 第一步: 遵循在 [HuggingFace](https://huggingface.co/Alibaba-NLP/gte-Qwen2-7B-instruct) 或者 [ModelScope](https://www.modelscope.cn/models/iic/gte_Qwen2-7B-instruct )的指示下载模型。
#   (如果无法直接从HuggingFace下载模型，也可以考虑使用HuggingFace镜像：bash命令行`export HF_ENDPOINT=https://hf-mirror.com`，或者在Python代码中加入`os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"`)
# * 第二步: 设置服务器。以下是一段参考代码。
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
# * 第三部：启动服务器。
#
# .. code-block:: bash
#
#     python setup_ms_service.py --model_name_or_path {$PATH_TO_gte_Qwen2_7B_instruct}
#
#
#
# 测试服务是否成功启动。
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
