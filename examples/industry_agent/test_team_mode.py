# -*- coding: utf-8 -*-
"""
测试 Team 模式（主 Agent + 场景 Agent）
======================================

主 Agent 作为 Leader，识别用户意图后创建场景 Agent（Worker）处理。
"""
import asyncio
import json
import os

import httpx

AGENT_SERVICE_URL = "http://127.0.0.1:8000"


async def test_team_mode(user_id: str, question: str):
    """测试 Team 模式。"""
    print(f"\n{'='*60}")
    print(f"测试用户: {user_id}")
    print(f"问题: {question}")
    print(f"{'='*60}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. 创建凭证
        print("\n[1] 创建凭证...")
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
            print(f"  凭证创建失败: {resp.status_code}")
            return

        # 2. 创建主 Agent（Leader）
        print("\n[2] 创建主 Agent（Leader）...")
        resp = await client.post(
            f"{AGENT_SERVICE_URL}/agent/",
            headers={"X-User-ID": user_id},
            json={
                "name": "主助手",
                "system_prompt": (
                    "你是主助手（Leader），负责协调团队处理用户请求。\n\n"
                    "## 你的团队\n"
                    "你可以创建以下专家来处理特定任务：\n"
                    "- order_expert: 订单查询专家\n"
                    "- customer_expert: 客户查询专家\n"
                    "- invoice_expert: 发票查询专家\n\n"
                    "## 工作流程\n"
                    "1. 分析用户问题\n"
                    "2. 如果需要查询数据，创建对应的专家\n"
                    "3. 专家完成后会向你汇报\n"
                    "4. 你汇总结果后回复用户\n\n"
                    "请用中文回答。"
                ),
            },
        )
        if resp.status_code in (200, 201):
            agent_id = resp.json().get("agent_id")
            print(f"  主 Agent ID: {agent_id}")
        else:
            print(f"  Agent 创建失败: {resp.status_code}")
            return

        # 3. 创建 Session
        print("\n[3] 创建 Session...")
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
            print(f"  Session 创建失败: {resp.status_code}")
            return

        # 4. 发送消息
        print(f"\n[4] 发送消息...")
        resp = await client.post(
            f"{AGENT_SERVICE_URL}/chat/",
            headers={"X-User-ID": user_id},
            json={
                "agent_id": agent_id,
                "session_id": session_id,
                "input": {
                    "name": "user",
                    "role": "user",
                    "content": [{"type": "text", "text": question}],
                },
            },
        )
        if resp.status_code == 200:
            print(f"  消息发送成功")
        else:
            print(f"  发送失败: {resp.status_code}")
            return

        # 5. 等待处理
        print(f"\n[5] 等待 Agent 处理（Team 模式可能需要更长时间）...")
        await asyncio.sleep(15)

        # 6. 获取消息历史
        print(f"\n[6] 获取消息历史...")
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
                        text = block.get("text", "")[:150]
                        print(f"    [{role}] {text}...")
                    elif block.get("type") == "tool_call":
                        tool_name = block.get("name", "")
                        print(f"    [{role}] 调用工具: {tool_name}")
        else:
            print(f"  获取消息失败: {resp.status_code}")


async def main():
    """主函数。"""
    print("=" * 60)
    print("  Team 模式测试（主 Agent + 场景 Agent）")
    print("=" * 60)

    # 测试 Team 模式
    test_cases = [
        ("user_sales", "帮我查一下订单A001的详情"),
        ("user_finance", "查一下客户华为的联系方式"),
        ("user_admin", "帮我查一下发票I001的信息"),
    ]

    for user_id, question in test_cases:
        await test_team_mode(user_id, question)

    print("\n" + "=" * 60)
    print("  测试完成！")
    print("  请查看 Agent Service 控制台日志，观察：")
    print("    - 主 Agent 创建 Team")
    print("    - 主 Agent 创建场景 Agent（Worker）")
    print("    - 场景 Agent 调用对应工具")
    print("    - 场景 Agent 向主 Agent 汇报结果")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
