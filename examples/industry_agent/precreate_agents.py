"""预创建 16 个用户的 Agent（完成后触发 Redis 持久化）"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import httpx, biz_system, redis

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
    print("  预创建/更新用户 Agent")
    print("=" * 60)
    created = 0
    updated = 0
    skipped = 0
    deleted_dup = 0
    errors = 0

    async with httpx.AsyncClient(timeout=10) as c:
        for user_id, suffix, prompt in _PRECREATE_CONFIG:
            user_info = biz_system.get_user_info(user_id)
            if not user_info:
                print(f"  [跳过] {user_id}：用户不存在")
                skipped += 1
                continue

            agent_name = f"{user_info['name']}的{suffix}"
            headers = {"X-User-ID": user_id}

            # 查询该用户已有的 Agent，检查是否已存在同名
            r = await c.get("http://127.0.0.1:8000/agent/", headers=headers)
            existing = r.json().get("agents", [])
            same_name = [a for a in existing if a["data"]["name"] == agent_name]

            if same_name:
                # 多于 1 个同名 → 删掉多余的，只保留第一个
                if len(same_name) > 1:
                    for dup in same_name[1:]:
                        dup_id = dup["id"]
                        r = await c.delete(
                            f"http://127.0.0.1:8000/agent/{dup_id}",
                            headers=headers,
                        )
                        if r.status_code == 204:
                            deleted_dup += 1
                    print(f"  [{user_id}] 清理 {len(same_name)-1} 个重复 → ", end="")

                agent_id = same_name[0]["id"]
                old_prompt = same_name[0]["data"]["system_prompt"]

                # 内容没变就跳过
                if old_prompt == prompt:
                    print(f"[{user_id}] 已一致 → 跳过")
                    skipped += 1
                    continue

                # 内容变了，用 PATCH 更新
                r = await c.patch(
                    f"http://127.0.0.1:8000/agent/{agent_id}",
                    headers=headers,
                    json={"name": agent_name, "system_prompt": prompt},
                )
                if r.status_code == 200:
                    print(f"[{user_id}] → 更新 {agent_id}")
                    updated += 1
                else:
                    print(f"[{user_id}] 更新失败: {r.status_code}")
                    errors += 1
                continue

            # 不存在则新建
            r = await c.post(
                "http://127.0.0.1:8000/agent/",
                headers=headers,
                json={"name": agent_name, "system_prompt": prompt},
            )
            if r.status_code in (200, 201):
                print(f"  [{user_id}] → 新建 {r.json().get('agent_id')}")
                created += 1
            else:
                print(f"  [{user_id}] 失败: {r.status_code} {r.text}")
                errors += 1

    print(f"  新建: {created}  更新: {updated}  跳过: {skipped}  清理重复: {deleted_dup}  错误: {errors}")
    print(f"  总计: {created + updated + skipped + errors}/{len(_PRECREATE_CONFIG)}")
    print("=" * 60)

    # 强制 Redis 持久化，防止重启丢数据
    if created or updated:
        r = redis.Redis(host="127.0.0.1", port=6379, protocol=2)
        r.bgsave()
        print("  Redis BGSAVE 已触发（数据已持久化）")

asyncio.run(main())
