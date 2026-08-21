# -*- coding: utf-8 -*-
"""
验证脚本
========

测试行业知识问答 Agent 的权限控制：
- 不同用户看到不同工具
- 不同用户看到不同数据
"""
import asyncio
import json
import os
import sys

import httpx

AGENT_SERVICE_URL = "http://127.0.0.1:8000"
BIZ_PROXY_URL = "http://127.0.0.1:8001"


async def test_user(user_id: str, question: str):
    """测试单个用户。"""
    print(f"\n{'='*60}")
    print(f"测试用户: {user_id}")
    print(f"问题: {question}")
    print(f"{'='*60}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. 获取用户信息
        print("\n[1] 获取用户信息...")
        resp = await client.get(
            f"{BIZ_PROXY_URL}/api/users/{user_id}",
        )
        if resp.status_code == 200:
            user_info = resp.json()
            print(f"  用户: {user_info['name']}")
            print(f"  部门: {user_info['department']}")
            print(f"  权限: {user_info['permissions']}")
        else:
            print(f"  获取用户信息失败: {resp.status_code}")
            return

        # 2. 发送聊天请求
        print(f"\n[2] 发送聊天请求...")
        resp = await client.post(
            f"{BIZ_PROXY_URL}/api/chat",
            headers={"X-User-ID": user_id},
            json={"message": question},
        )
        if resp.status_code == 200:
            chat_resp = resp.json()
            session_id = chat_resp["session_id"]
            print(f"  Session ID: {session_id}")
            print(f"  状态: {chat_resp['status']}")
        else:
            print(f"  聊天请求失败: {resp.status_code}")
            print(f"  错误: {resp.text}")
            return

        # 3. 等待 Agent 处理
        print(f"\n[3] 等待 Agent 处理...")
        await asyncio.sleep(5)

        # 4. 获取消息历史
        print(f"\n[4] 获取消息历史...")
        resp = await client.get(
            f"{BIZ_PROXY_URL}/api/sessions/{session_id}/messages",
            headers={"X-User-ID": user_id},
        )
        if resp.status_code == 200:
            messages = resp.json().get("messages", [])
            print(f"  消息数: {len(messages)}")
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", [])
                if content:
                    for block in content:
                        if block.get("type") == "text":
                            text = block.get("text", "")[:200]
                            print(f"    [{role}] {text}...")
                        elif block.get("type") == "tool_call":
                            tool_name = block.get("name", "")
                            tool_input = block.get("input", "")
                            print(f"    [{role}] 调用工具: {tool_name}({tool_input})")
        else:
            print(f"  获取消息失败: {resp.status_code}")


async def main():
    """主函数。"""
    print("=" * 60)
    print("  行业知识问答 Agent 验证脚本")
    print("=" * 60)

    # 测试不同用户
    test_cases = [
        ("user_sales", "查一下所有订单"),
        ("user_finance", "查一下所有订单"),
        ("user_admin", "查一下所有订单"),
    ]

    for user_id, question in test_cases:
        await test_user(user_id, question)

    print("\n" + "=" * 60)
    print("  验证完成！")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
