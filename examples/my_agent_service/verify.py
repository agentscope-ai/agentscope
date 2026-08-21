# -*- coding: utf-8 -*-
"""
验证自定义工具和中间件是否生效
"""
import asyncio
import json
import os
import sys

import httpx

BASE_URL = "http://127.0.0.1:8000"
USER_ID = "test_user"


async def main():
    async with httpx.AsyncClient(timeout=30.0) as client:
        print("=" * 60)
        print("验证自定义工具和中间件是否生效")
        print("=" * 60)
        print()

        # 1. 创建 DeepSeek 凭证
        print("[1/5] 创建 DeepSeek 凭证...")
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            print("错误: 请设置环境变量 DEEPSEEK_API_KEY")
            return

        resp = await client.post(
            f"{BASE_URL}/credential/",
            headers={"X-User-ID": USER_ID},
            json={
                "data": {
                    "type": "deepseek_credential",
                    "api_key": api_key,
                },
            },
        )
        if resp.status_code in (200, 201):
            cred_data = resp.json()
            cred_id = cred_data.get("credential_id")
            print(f"  凭证创建成功: {cred_id}")
        else:
            print(f"  凭证创建失败: {resp.status_code} - {resp.text}")
            # 尝试获取已有凭证
            resp = await client.get(
                f"{BASE_URL}/credential/",
                headers={"X-User-ID": USER_ID},
            )
            if resp.status_code == 200:
                data = resp.json()
                creds = data.get("credentials", [])
                if creds:
                    cred_id = creds[0]["id"]
                    print(f"  使用已有凭证: {cred_id}")
                else:
                    print("  没有可用凭证")
                    return
            else:
                return
        print()

        # 2. 创建 Agent
        print("[2/5] 创建 Agent...")
        resp = await client.post(
            f"{BASE_URL}/agent/",
            headers={"X-User-ID": USER_ID},
            json={
                "name": "验证助手",
                "system_prompt": "你是一个智能助手，可以帮用户查天气、做计算。请用中文回答。",
            },
        )
        if resp.status_code in (200, 201):
            agent_data = resp.json()
            agent_id = agent_data.get("agent_id")
            print(f"  Agent 创建成功: {agent_id}")
        else:
            print(f"  Agent 创建失败: {resp.status_code} - {resp.text}")
            return
        print()

        # 3. 创建 Session
        print("[3/5] 创建 Session...")
        resp = await client.post(
            f"{BASE_URL}/sessions/",
            headers={"X-User-ID": USER_ID},
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
            session_data = resp.json()
            session_id = session_data.get("session_id")
            print(f"  Session 创建成功: {session_id}")
        else:
            print(f"  Session 创建失败: {resp.status_code} - {resp.text}")
            return
        print()

        # 4. 发送消息
        print("[4/5] 发送消息: '北京天气怎么样？'")
        print("  观察控制台日志，应该看到:")
        print("    - [MY_AGENT] 创建工具: ...")
        print("    - [MY_AGENT] 创建中间件: ...")
        print("    - [MY_AGENT] 执行工具: get_weather")
        print("    - [MY_AGENT] 工具 get_weather 执行完成")
        print()

        resp = await client.post(
            f"{BASE_URL}/chat/",
            headers={"X-User-ID": USER_ID},
            json={
                "agent_id": agent_id,
                "session_id": session_id,
                "input": {
                    "name": "user",
                    "role": "user",
                    "content": [{"type": "text", "text": "北京天气怎么样？"}],
                },
            },
        )
        if resp.status_code == 200:
            print(f"  消息发送成功: {resp.json()}")
        else:
            print(f"  消息发送失败: {resp.status_code} - {resp.text}")
            return
        print()

        # 5. 等待并获取回复
        print("[5/5] 等待 Agent 回复...")
        print("  请查看 Agent Service 控制台日志，应该看到:")
        print("    - [MY_AGENT] 创建工具: ...")
        print("    - [MY_AGENT] 创建中间件: ...")
        print("    - [MY_AGENT] 执行工具: get_weather")
        print("    - [MY_AGENT] 工具 get_weather 执行完成")
        print()

        # 等待几秒让 Agent 处理
        await asyncio.sleep(5)

        # 获取消息历史
        resp = await client.get(
            f"{BASE_URL}/sessions/{session_id}/messages",
            headers={"X-User-ID": USER_ID},
            params={"agent_id": agent_id},
        )
        if resp.status_code == 200:
            messages = resp.json().get("messages", [])
            print(f"  会话消息数: {len(messages)}")
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", [])
                if content:
                    text = content[0].get("text", "")[:100]
                    print(f"    [{role}] {text}...")
        else:
            print(f"  获取消息失败: {resp.status_code}")

        print()
        print("=" * 60)
        print("验证完成！")
        print("请检查 Agent Service 控制台是否有 [MY_AGENT] 日志")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
