# # -*- coding: utf-8 -*-
# """"""
#
# from qdrant_client import QdrantClient
# from qdrant_client.local.qdrant_local import QdrantLocal
# from qdrant_client.models import VectorParams, Distance
# from qdrant_client.models import PointStruct
#
#
# client = QdrantLocal(location="./qdrant_storage")
#
# # client.create_collection(
# #     collection_name="test_collection",
# #     vectors_config=VectorParams(size=4, distance=Distance.DOT),
# # )
#
# operation_info = client.upsert(
#     collection_name="test_collection",
#     wait=True,
#     points=[
#         PointStruct(id=1, vector=[0.05, 0.61, 0.76, 0.74], payload={"city": "Berlin"}),
#         PointStruct(id=2, vector=[0.19, 0.81, 0.75, 0.11], payload={"city": "London"}),
#         PointStruct(id=3, vector=[0.36, 0.55, 0.47, 0.94], payload={"city": "Moscow"}),
#         PointStruct(id=4, vector=[0.18, 0.01, 0.85, 0.80], payload={"city": "New York"}),
#         PointStruct(id=5, vector=[0.24, 0.18, 0.22, 0.44], payload={"city": "Beijing"}),
#         PointStruct(id=6, vector=[0.35, 0.08, 0.11, 0.44], payload={"city": "Mumbai"}),
#     ],
# )
#
# print(operation_info)
#
# search_result = client.query_points(
#     collection_name="test_collection",
#     query=[0.2, 0.1, 0.9, 0.7],
#     with_payload=False,
#     limit=3
# )
#
# print(search_result)
import asyncio
import os
import threading

from agentscope import UserAgent
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter, \
    DashScopeMultiAgentFormatter
from agentscope.model import DashScopeChatModel

alice = ReActAgent(
    name="Alice", model=DashScopeChatModel(model_name="qwen-max", api_key=os.environ["DASHSCOPE_API_KEY"]),
    formatter=DashScopeMultiAgentFormatter(),
    sys_prompt="You are a helpful assistant named Alice."
)

bob = ReActAgent(
    name="Bob", model=DashScopeChatModel(model_name="qwen-max", api_key=os.environ["DASHSCOPE_API_KEY"]),
    formatter=DashScopeMultiAgentFormatter(),
    sys_prompt="You are a helpful assistant named Bob."
)

user = UserAgent(name="user")

async def ask_user_input():
    res = await user()

async def speak():
    msg = None
    while True:
        msg = await alice(msg)
        msg = await bob(msg)
# 新启动一个线程，这个线程内调用这个ask_user_input函数

thread = threading.Thread(target=lambda: asyncio.run(speak()))

thread.start()

async def main():
    await speak()



