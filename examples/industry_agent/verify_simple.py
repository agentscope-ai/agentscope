# -*- coding: utf-8 -*-
"""
简化验证脚本
============

直接测试 Agent Service 的权限控制，不经过业务后端代理。
"""
import asyncio
import json
import os

import httpx

AGENT_SERVICE_URL = "http://127.0.0.1:8000"


async def test_user_tools(user_id: str):
    """测试不同用户的工具权限。"""
    print(f"\n{'='*60}")
    print(f"测试用户: {user_id}")
    print(f"{'='*60}")

    # 1. 创建凭证（如果还没有）
    print("\n[1] 创建凭证...")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{AGENT_SERVICE_URL}/credential/",
            headers={"X-User-ID": user_id},
            json={
                "data": {
                    "type": "deepseek_credential",
                    "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
                },
            },
        )
        if resp.status_code in (200, 201):
            cred_id = resp.json().get("credential_id")
            print(f"  凭证 ID: {cred_id}")
        else:
            # 尝试获取已有凭证
            resp = await client.get(
                f"{AGENT_SERVICE_URL}/credential/",
                headers={"X-User-ID": user_id},
            )
            if resp.status_code == 200:
                creds = resp.json().get("credentials", [])
                if creds:
                    cred_id = creds[0]["id"]
                    print(f"  使用已有凭证: {cred_id}")
                else:
                    print("  没有可用凭证")
                    return
            else:
                print(f"  获取凭证失败: {resp.status_code}")
                return

    # 2. 创建 Agent
    print("\n[2] 创建 Agent...")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{AGENT_SERVICE_URL}/agent/",
            headers={"X-User-ID": user_id},
            json={
                "name": f"{user_id}的助手",
                "system_prompt": "你是行业知识问答助手，可以查询订单、客户、发票。",
            },
        )
        if resp.status_code in (200, 201):
            agent_id = resp.json().get("agent_id")
            print(f"  Agent ID: {agent_id}")
        else:
            print(f"  创建 Agent 失败: {resp.status_code}")
            return

    # 3. 创建 Session
    print("\n[3] 创建 Session...")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{AGENT_SERVICE_URL}/sessions/",
            headers={"X-User-ID": user_id},
            json={
                "agent_id": agent_id,
                "chat_model_config": {
                    "type": "deepseek",
                    "credential_id": cred_id,
                    "model": "deepseek-v4-flash",
                    "parameters": {},
                },
            },
        )
        if resp.status_code in (200, 201):
            session_id = resp.json().get("session_id")
            print(f"  Session ID: {session_id}")
        else:
            print(f"  创建 Session 失败: {resp.status_code}")
            return

    # 4. 发送消息
    print("\n[4] 发送消息: '查一下所有订单'")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{AGENT_SERVICE_URL}/chat/",
            headers={"X-User-ID": user_id},
            json={
                "agent_id": agent_id,
                "session_id": session_id,
                "input": {
                    "name": "user",
                    "role": "user",
                    "content": [{"type": "text", "text": "查一下所有订单"}],
                },
            },
        )
        if resp.status_code == 200:
            print(f"  消息发送成功")
        else:
            print(f"  发送失败: {resp.status_code}")
            return

    # 5. 等待并获取结果
    print("\n[5] 等待 Agent 处理...")
    await asyncio.sleep(5)

    print("\n[6] 获取消息历史...")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{AGENT_SERVICE_URL}/sessions/{session_id}/messages",
            headers={"X-User-ID": user_id},
            params={"agent_id": agent_id},
        )
        if resp.status_code == 200:
            messages = resp.json().get("messages", [])
            print(f"  消息数: {len(messages)}")
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", [])
                for block in content:
                    if block.get("type") == "text":
                        text = block.get("text", "")[:100]
                        print(f"    [{role}] {text}...")
                    elif block.get("type") == "tool_call":
                        tool_name = block.get("name", "")
                        print(f"    [{role}] 调用工具: {tool_name}")
        else:
            print(f"  获取消息失败: {resp.status_code}")


async def main():
    """主函数。"""
    print("=" * 60)
    print("  行业 Agent 权限控制验证")
    print("=" * 60)

    # 测试不同用户
    users = ["user_sales", "user_finance", "user_admin"]

    for user_id in users:
        await test_user_tools(user_id)

    print("\n" + "=" * 60)
    print("  验证完成！")
    print("  请查看 Agent Service 控制台日志，观察：")
    print("    - [FACTORY] 创建工具: user=xxx")
    print("    - [FACTORY] 用户 xxx 的工具权限: [...]")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
