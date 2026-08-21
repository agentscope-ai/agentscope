# -*- coding: utf-8 -*-
"""
业务后端代理接口
================

模拟你的业务系统后端，提供：
- 用户认证（模拟）
- 代理转发到 Agent Service
- 自动管理 session

这个文件演示你的业务后端如何对接 Agent Service。
"""
import asyncio
import json
import os
import sys
from typing import Optional

# 添加当前目录到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

import biz_system


# ──────────────────────────────────────────────
# 1. 配置
# ──────────────────────────────────────────────

AGENT_SERVICE_URL = "http://127.0.0.1:8000"

# 固定的 Agent ID（服务启动时预创建）
DEFAULT_AGENT_ID = "industry_agent_001"


# ──────────────────────────────────────────────
# 2. 请求/响应模型
# ──────────────────────────────────────────────

class ChatRequest(BaseModel):
    """聊天请求。"""
    message: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    """聊天响应。"""
    session_id: str
    status: str


class UserInfo(BaseModel):
    """用户信息。"""
    id: str
    name: str
    department: str
    permissions: list[str]


# ──────────────────────────────────────────────
# 3. FastAPI 应用
# ──────────────────────────────────────────────

app = FastAPI(title="业务后端代理", version="1.0.0")


# ──────────────────────────────────────────────
# 4. 接口
# ──────────────────────────────────────────────

@app.get("/api/users/{user_id}", response_model=UserInfo)
async def get_user_info(user_id: str):
    """获取用户信息（模拟认证）。"""
    user = biz_system.get_user_info(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    permissions = biz_system.get_tool_permissions(user_id)
    return UserInfo(
        id=user["id"],
        name=user["name"],
        department=user["department"],
        permissions=permissions,
    )


@app.post("/api/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    x_user_id: str = Header(..., description="用户ID"),
):
    """聊天接口（代理到 Agent Service）。

    你的业务系统调用这个接口，后端自动转发到 Agent Service。
    """
    user_id = x_user_id

    # 验证用户
    user = biz_system.get_user_info(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")

    # 如果没有 session_id，创建一个
    session_id = request.session_id
    if not session_id:
        session_id = await create_session(user_id)

    # 转发到 Agent Service
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                f"{AGENT_SERVICE_URL}/chat/",
                headers={"X-User-ID": user_id},
                json={
                    "agent_id": DEFAULT_AGENT_ID,
                    "session_id": session_id,
                    "input": {
                        "name": "user",
                        "role": "user",
                        "content": [{"type": "text", "text": request.message}],
                    },
                },
            )

            if resp.status_code != 200:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"Agent Service 错误: {resp.text}",
                )

            return ChatResponse(
                session_id=session_id,
                status="started",
            )

        except httpx.RequestError as e:
            raise HTTPException(
                status_code=503,
                detail=f"无法连接 Agent Service: {e}",
            )


@app.get("/api/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    x_user_id: str = Header(..., description="用户ID"),
):
    """获取会话消息历史。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{AGENT_SERVICE_URL}/sessions/{session_id}/messages",
                headers={"X-User-ID": x_user_id},
                params={"agent_id": DEFAULT_AGENT_ID},
            )

            if resp.status_code != 200:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=resp.text,
                )

            return resp.json()

        except httpx.RequestError as e:
            raise HTTPException(
                status_code=503,
                detail=f"无法连接 Agent Service: {e}",
            )


# ──────────────────────────────────────────────
# 5. 辅助函数
# ──────────────────────────────────────────────

async def create_session(user_id: str) -> str:
    """为用户创建新的 session。"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{AGENT_SERVICE_URL}/sessions/",
            headers={"X-User-ID": user_id},
            json={
                "agent_id": DEFAULT_AGENT_ID,
                "chat_model_config": {
                    "type": "deepseek",
                    "credential_id": "default_credential",  # 需要提前创建
                    "model": "deepseek-v4-flash",
                    "parameters": {},
                },
            },
        )

        if resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"创建 session 失败: {resp.text}",
            )

        data = resp.json()
        return data.get("session_id")


async def precreate_agent():
    """预创建默认 Agent。"""
    await asyncio.sleep(2)  # 等待 Agent Service 启动

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                f"{AGENT_SERVICE_URL}/agent/",
                headers={"X-User-ID": "system"},
                json={
                    "name": "行业知识助手",
                    "system_prompt": (
                        "你是一个XX行业知识问答助手，可以帮用户查询订单、客户、发票等信息。\n"
                        "请用中文回答，语气友好专业。\n"
                        "如果用户的问题不需要工具，直接回答即可。"
                    ),
                },
            )

            if resp.status_code in (200, 201):
                agent_id = resp.json().get("agent_id")
                print(f"[预创建] Agent ID: {agent_id}")
                global DEFAULT_AGENT_ID
                DEFAULT_AGENT_ID = agent_id
            else:
                print(f"[预创建] Agent 创建失败: {resp.status_code}")

        except Exception as e:
            print(f"[预创建] Agent 创建异常: {e}")


# ──────────────────────────────────────────────
# 6. 启动
# ──────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    """服务启动时预创建 Agent。"""
    asyncio.create_task(precreate_agent())


if __name__ == "__main__":
    print("=" * 60)
    print("  业务后端代理")
    print("=" * 60)
    print()
    print("  API 文档: http://127.0.0.1:8001/docs")
    print()
    print("  测试用户:")
    print("    - user_sales  (销售)")
    print("    - user_finance (财务)")
    print("    - user_admin  (管理)")
    print()
    print("=" * 60)

    uvicorn.run(
        "biz_proxy:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
    )
