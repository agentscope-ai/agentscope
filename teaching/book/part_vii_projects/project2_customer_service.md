# 项目2：智能客服机器人

> **难度**：⭐⭐（进阶）
> **预计时间**：4小时

---

## 🎯 学习目标

- 组合使用多个工具
- 集成RAG知识库
- 对话状态管理

---

## 1. 需求分析

### 功能需求

- 回答产品相关问题
- 查询订单状态
- 转接人工客服

```
用户: "我的订单什么时候发货？"
Agent: "让我帮您查询...您的订单12345预计明天发货。"
```

---

## 2. 系统设计

```
┌─────────────────────────────────────────┐
│            用户                         │
└─────────────────┬───────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────┐
│          CustomerServiceAgent           │
│  Reasoning + Acting                    │
└─────────────────┬───────────────────────┘
                  │
        ┌─────────┼─────────┐
        ▼         ▼         ▼
   ┌────────┐ ┌────────┐ ┌────────┐
   │ Product │ │ Order  │ │ FAQ    │
   │ Toolkit │ │ Toolkit │ │ RAG    │
   └────────┘ └────────┘ └────────┘
```

---

## 3. 核心代码

```python
from agentscope.agent import ReActAgent
from agentscope.tool import Toolkit
from agentscope.memory import RAGMemory

# 创建工具
product_toolkit = Toolkit()
order_toolkit = Toolkit()

# 注册产品查询工具
@product_toolkit.register_tool_function(name="query_product", description="查询产品信息")
def query_product(product_id: str):
    return {"name": "产品A", "price": 99, "stock": 100}

# 注册订单查询工具
@order_toolkit.register_tool_function(name="query_order", description="查询订单状态")
def query_order(order_id: str):
    return {"id": order_id, "status": "待发货", "eta": "明天"}

# 创建RAG知识库
faq_memory = RAGMemory(
    documents=["常见问题FAQ..."],
    embedding_model="text-embedding-3-small"
)

# 创建Agent
agent = ReActAgent(
    name="客服",
    model=model,
    toolkit=[product_toolkit, order_toolkit],
    memory=faq_memory,
    sys_prompt="你是一个智能客服，根据用户问题选择合适的工具回答。"
)
```

---

## 4. 运行结果

```
用户: 我的订单12345什么时候发货？
Agent: 让我查询一下...您的订单12345状态是"待发货"，预计明天发货。
```

---

## 5. 扩展思考

1. **如何实现多轮对话？**
   - 使用Memory存储对话历史
   - 在sys_prompt中引用历史

2. **如何转接人工？**
   - 判断问题复杂度
   - 使用MsgHub通知人工Agent

---

★ **Insight** ─────────────────────────────────────
- **多Toolkit组合** = 按功能模块化工具
- **RAGMemory** = 基于向量检索的FAQ
- **对话状态** = Memory记忆历史上下文
─────────────────────────────────────────────────
