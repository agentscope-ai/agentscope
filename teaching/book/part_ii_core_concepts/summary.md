# 第二部分小结：Agent开发基础

本部分介绍了AgentScope的三大核心概念：Msg、Pipeline和MsgHub。

---

## 📚 学到的内容

### 第4章：Msg消息机制
- Msg统一消息格式
- role区分来源（user/assistant/system）
- ContentBlocks支持结构化数据

### 第5章：Pipeline
- **SequentialPipeline**：顺序执行，A→B→C
- **FanoutPipeline**：并行分发，一对多
- 按数据依赖选择类型

### 第6章：MsgHub
- 发布-订阅模式
- 广播给所有参与者
- 松耦合通信

---

## 🔗 与后续内容的联系

Msg、Pipeline、MsgHub是理解Agent如何通信的基础：

| 概念 | 应用 |
|------|------|
| Msg | Agent间传递的消息格式 |
| Pipeline | 编排Agent执行顺序 |
| MsgHub | 多Agent松耦合协作 |

---

## ➡️ 下一站

准备好进入**[第三部分：Agent核心原理](./part_iii_core_concepts/)**？

在接下来的三章中，我们将学习：
- ReActAgent工作原理
- Hook钩子机制
- Model与Formatter
