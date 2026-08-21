# -*- coding: utf-8 -*-
"""智能报价场景的多 Agent 示例。

架构：
- 总 Agent (Supervisor): 负责统一风格、意图识别、汇总输出
- 报价专家 (Quotation Specialist): 负责具体报价计算
"""
import os

import uvicorn
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware

from agentscope.app import create_app, SubAgentTemplate
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.rag.knowledge_base_manager import CollectionPerKbManager
from agentscope.app.storage import RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.permission import PermissionContext, PermissionMode
from agentscope.rag import QdrantStore

# 存储配置
storage = RedisStorage(
    host="127.0.0.1",
    port=6379,
    protocol=2,
)

vector_store = QdrantStore(location=":memory:")

# 定义报价专家 SubAgent
quotation_specialist = SubAgentTemplate(
    type="quotation_specialist",
    description=(
        "报价专家，专门处理产品报价、价格计算、折扣方案等问题。"
        "当用户询问产品价格、报价、费用计算时，使用此专家。"
    ),
    system_prompt_template="""你是 {member_name}，一位专业的报价专家。

## 团队信息
- 团队: {team_name}
- 负责人: {leader_name}

## 你的职责
1. 根据用户需求计算产品报价
2. 提供多种方案供用户选择
3. 解释价格构成和优惠策略

## 产品价目表
| 产品 | 基础价格 | 说明 |
|------|---------|------|
| 基础版 SaaS | 99元/月 | 适合个人用户，5个项目 |
| 专业版 SaaS | 299元/月 | 适合小团队，无限项目 |
| 企业版 SaaS | 定制报价 | 大型企业，私有化部署 |
| API 调用 | 0.01元/次 | 按实际调用量计费 |
| 技术支持 | 500元/小时 | 专家一对一 |

## 折扣策略
- 年付享 8 折
- 10人以上团队享 9 折
- 老客户续费享 95 折

## 输出格式
请按以下格式输出报价：
```
【产品方案】
- 产品名称: xxx
- 价格: xxx
- 适用场景: xxx

【优惠信息】
- 可享受的折扣
- 最终价格

【建议】
- 根据用户需求给出推荐
```

## 汇报规则
- 通过 TeamSay 向 {leader_name} 汇报报价结果
- 只汇报最终报价和建议，不要汇报计算过程
""",
    permission_context=PermissionContext(
        mode=PermissionMode.EXPLORE,  # 只读模式，报价不需要修改权限
    ),
)

# 总 Agent 的 System Prompt（控制统一风格）
QUOTATION_SYSTEM_PROMPT = """你是一个智能报价助手的总协调者。

## 你的职责
1. 分析用户的报价需求
2. 将问题分配给报价专家处理
3. 汇总专家的报价结果，用统一的风格输出给用户

## 统一风格要求
- 语气：专业、友好、简洁
- 格式：先给出结论（推荐方案），再列出详情
- 长度：控制在 200 字以内
- 必须包含：推荐方案、价格、理由

## 工作流程
1. 识别用户需求（需要什么产品/服务）
2. 调用 quotation_specialist 获取报价
3. 收到报价后，用统一风格整理输出

## 输出模板
```
【推荐方案】
{产品名} - {价格}

【方案详情】
- 包含: {功能列表}
- 适合: {适用场景}

【为什么推荐】
{一句话理由}

需要我详细解释或调整方案吗？
```

## 注意事项
- 如果用户需求不明确，先询问清楚再报价
- 如果涉及复杂定制需求，建议联系销售团队
"""

app = create_app(
    storage=storage,
    message_bus=InMemoryMessageBus(),
    workspace_manager=LocalWorkspaceManager(
        basedir=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "workspaces",
        ),
    ),
    knowledge_base_manager=CollectionPerKbManager(
        storage=storage,
        vector_store=vector_store,
    ),
    custom_subagent_templates=[quotation_specialist],
    extra_middlewares=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ],
)


if __name__ == "__main__":
    uvicorn.run(
        "quotation_main:app",
        host="0.0.0.0",
        port=8001,  # 使用不同端口，避免与主服务冲突
        reload=True,
    )
