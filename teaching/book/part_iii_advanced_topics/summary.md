# 第三部分小结：Agent核心原理

本部分深入介绍了ReActAgent的工作机制和核心组件。

---

## 📚 学到的内容

### 第7章：ReActAgent
- ReAct = Reasoning + Acting + Observation
- 思考决定策略，行动执行操作
- 多轮循环直到完成任务

### 第8章：Hook机制
- 拦截器模式，运行时扩展
- 关键点：before/after各阶段
- AOP横切关注点分离

### 第9章：Model与Formatter
- Model统一接口，屏蔽差异
- Formatter格式适配，Msg↔API
- 按模型选择Formatter

---

## 🔗 与后续内容的联系

这些是Agent的"大脑"：

| 组件 | 作用 |
|------|------|
| ReAct | 思考决策循环 |
| Hook | 行为扩展 |
| Model/Formatter | 智能来源 |

---

## ➡️ 下一站

准备好进入**[第四部分：工具与记忆](./part_iv_tools_memory/)**？

在接下来的两章中，我们将学习：
- Toolkit工具系统
- Memory记忆系统
