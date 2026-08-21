"""删除所有预创建的同名 Agent，然后重新创建"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import httpx, biz_system

_PRECREATE_CONFIG = [
    ("user_sales", "销售助手", "你是销售部门的智能助手。可以查询订单信息、订单统计、客户信息及其订单记录。"),
    ("user_sales_2", "销售助手", "你是销售部门的智能助手。可以查询订单信息、订单统计、客户信息及其订单记录。"),
    ("user_sales_3", "销售助手", "你是销售部门的智能助手。可以查询订单信息、订单统计、客户信息及其订单记录。"),
    ("user_finance", "财务助手", "你是财务部门的智能助手。可以查询订单、发票、以及销售业绩数据。注意核对金额。"),
    ("user_finance_2", "财务助手", "你是财务部门的智能助手。可以查询订单、发票、以及销售业绩数据。注意核对金额。"),
    ("user_admin", "管理助手", "你是管理部门的智能助手，拥有全部查询权限。可以查询订单、客户、发票和业绩报表。"),
    ("user_hr", "HR助手", "你是人力资源部门的智能助手，可以查询客户信息。关注客户关系和等级。"),
    ("user_hr_2", "HR助手", "你是人力资源部门的智能助手，可以查询客户信息。关注客户关系和等级。"),
    ("user_tech", "技术助手", "你是技术部门的智能助手，可以查询订单信息。关注订单状态和技术要求。"),
    ("user_tech_2", "技术助手", "你是技术部门的智能助手，可以查询订单信息。关注订单状态和技术要求。"),
    ("user_marketing", "市场助手", "你是市场部门的智能助手，可以查询客户和发票信息。关注客户价值和预算。"),
    ("user_marketing_2", "市场助手", "你是市场部门的智能助手，可以查询客户和发票信息。关注客户价值和预算。"),
    ("user_support", "客服助手", "你是客服部门的智能助手，可以查询订单和客户信息。用耐心友好的语气回答。"),
    ("user_support_2", "客服助手", "你是客服部门的智能助手，可以查询订单和客户信息。用耐心友好的语气回答。"),
    ("user_logistics", "物流助手", "你是物流部门的智能助手，可以查询订单信息。关注订单状态和物流进度。"),
    ("user_logistics_2", "物流助手", "你是物流部门的智能助手，可以查询订单信息。关注订单状态和物流进度。"),
]

async def main():
    print("=" * 60)
    print("  重置用户 Agent（删除 → 重建）")
    print("=" * 60)

    deleted = 0
    created = 0

    async with httpx.AsyncClient(timeout=10) as c:
        for user_id, suffix, prompt in _PRECREATE_CONFIG:
            user_info = biz_system.get_user_info(user_id)
            if not user_info:
                continue
            agent_name = f"{user_info['name']}的{suffix}"
            headers = {"X-User-ID": user_id}

            # 1. 列出该用户所有 Agent
            r = await c.get("http://127.0.0.1:8000/agent/", headers=headers)
            agents = r.json().get("agents", [])

            # 2. 删除所有同名的
            for a in agents:
                if a["data"]["name"] == agent_name:
                    aid = a["id"]
                    dr = await c.delete(
                        f"http://127.0.0.1:8000/agent/{aid}",
                        headers=headers,
                    )
                    if dr.status_code == 204:
                        deleted += 1

            # 3. 重建一个
            r = await c.post(
                "http://127.0.0.1:8000/agent/",
                headers=headers,
                json={"name": agent_name, "system_prompt": prompt},
            )
            if r.status_code in (200, 201):
                created += 1
                print(f"  [{user_id}] 重建 {r.json().get('agent_id')}")
            else:
                print(f"  [{user_id}] 创建失败: {r.status_code}")

    print(f"  删除: {deleted}  新建: {created}")
    print("=" * 60)

asyncio.run(main())
