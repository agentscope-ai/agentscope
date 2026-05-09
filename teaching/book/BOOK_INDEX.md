# 《AgentScope Agent开发实战》

> **目标读者**: 有编程基础的开发者（尤其是Java开发者）
> **学习路径**: 从"Hello World"到"生产级Agent系统"

---

## 📚 书籍结构

```
第一部分：Python基础
├── 第1章：Python面向对象编程
├── 第2章：Python异步编程
└── 第3章：Python高级语法

第二部分：Agent开发基础
├── 第4章：消息传递机制（Msg）
├── 第5章：管道与流水线（Pipeline）
└── 第6章：发布-订阅模式（MsgHub）

第三部分：Agent核心原理
├── 第7章：ReActAgent工作原理
├── 第8章：Hook机制
└── 第9章：Model与Formatter

第四部分：工具与记忆
├── 第10章：Toolkit工具系统
└── 第11章：Memory记忆系统

第五部分：多Agent系统
├── 第12章：多Agent协作模式
└── 第13章：消息追踪与调试

第六部分：部署与运维
├── 第14章：Runtime运行时
└── 第15章：Docker部署

第七部分：项目实战
├── 项目1：天气查询Agent
├── 项目2：智能客服机器人
├── 项目3：多Agent辩论系统
├── 项目4：深度研究助手
└── 项目5：语音对话助手

附录
├── 附录A：术语对照表
├── 附录B：Python语法速查卡
├── 附录C：代码模板库
├── 附录D：常见错误急救箱
├── 附录E：AgentScope API参考
└── 附录F：学习路径图
```

---

## 🚀 快速开始

### 推荐学习路径

1. **第1-3章** → Python基础（为非Python开发者）
2. **第4-6章** → 核心概念入门
3. **第7-11章** → 深入原理
4. **第12-15章** → 高级主题
5. **项目实战** → 动手实践

### 学习时间估算

| 部分 | 预计时间 |
|------|----------|
| 第一部分 | 6小时 |
| 第二部分 | 4小时 |
| 第三部分 | 6小时 |
| 第四部分 | 4小时 |
| 第五部分 | 4小时 |
| 第六部分 | 3小时 |
| 第七部分 | 30小时 |
| **总计** | **~57小时** |

---

## 📖 章节内容

### [第一部分：Python基础](./part_i_getting_started/)

为Java开发者量身定制的Python基础，快速掌握与AgentScope开发相关的Python特性。

- [第1章：Python面向对象](./part_i_getting_started/chapter1_python_oop.md)
- [第2章：异步编程](./part_i_getting_started/chapter2_async_programming.md)
- [第3章：高级语法](./part_i_getting_started/chapter3_advanced_syntax.md)
- [部分小结](./part_i_getting_started/summary.md)

### [第二部分：Agent开发基础](./part_ii_core_concepts/)

AgentScope三大核心概念：消息、管道、发布订阅。

- [第4章：消息传递机制](./part_ii_core_concepts/chapter4_message_system.md)
- [第5章：管道与流水线](./part_ii_core_concepts/chapter5_pipeline.md)
- [第6章：发布-订阅模式](./part_ii_core_concepts/chapter6_msg_hub.md)
- [部分小结](./part_ii_core_concepts/summary.md)

### [第三部分：Agent核心原理](./part_iii_advanced_topics/)

深入ReActAgent的工作机制。

- [第7章：ReActAgent工作原理](./part_iii_advanced_topics/chapter7_react_agent.md)
- [第8章：Hook机制](./part_iii_advanced_topics/chapter8_hook.md)
- [第9章：Model与Formatter](./part_iii_advanced_topics/chapter9_model_formatter.md)
- [部分小结](./part_iii_advanced_topics/summary.md)

### [第四部分：工具与记忆](./part_iv_tools_memory/)

扩展Agent能力和持久化上下文。

- [第10章：Toolkit工具系统](./part_iv_tools_memory/chapter10_toolkit.md)
- [第11章：Memory记忆系统](./part_iv_tools_memory/chapter11_memory.md)
- [部分小结](./part_iv_tools_memory/summary.md)

### [第五部分：多Agent系统](./part_v_multi_agent/)

构建多Agent协作系统。

- [第12章：多Agent协作模式](./part_v_multi_agent/chapter12_multi_agent.md)
- [第13章：消息追踪与调试](./part_v_multi_agent/chapter13_trace_debug.md)
- [部分小结](./part_v_multi_agent/summary.md)

### [第六部分：部署与运维](./part_vi_deployment/)

将Agent系统部署到生产环境。

- [第14章：Runtime运行时](./part_vi_deployment/chapter14_runtime.md)
- [第15章：Docker部署](./part_vi_deployment/chapter15_docker.md)
- [部分小结](./part_vi_deployment/summary.md)

### [第七部分：项目实战](./part_vii_projects/)

5个完整项目，从入门到精通。

- [项目总览](./part_vii_projects/overview.md)
- [项目1：天气查询Agent](./part_vii_projects/project1_weather.md)
- [项目2：智能客服机器人](./part_vii_projects/project2_customer_service.md)
- [项目3：多Agent辩论系统](./part_vii_projects/project3_debate.md)
- [项目4：深度研究助手](./part_vii_projects/project4_research.md)
- [项目5：语音对话助手](./part_vii_projects/project5_voice.md)
- [部分小结](./part_vii_projects/summary.md)

---

## ✨ 书籍特色

1. **Java锚定** - 用Java概念类比理解Python
2. **追踪式学习** - 追踪数据在系统中的完整旅程
3. **图解优先** - 每一步都用追踪图说明
4. **项目驱动** - 5个完整项目串联知识点
5. **先跑起来** - 代码完整可运行

---

## 📊 篇幅

| 部分 | 章节数 | 预计篇幅 |
|------|--------|----------|
| 第一部分 | 3章 | 90页 |
| 第二部分 | 3章 | 75页 |
| 第三部分 | 3章 | 90页 |
| 第四部分 | 2章 | 50页 |
| 第五部分 | 2章 | 50页 |
| 第六部分 | 2章 | 40页 |
| 第七部分 | 5项目 | 200页 |
| **总计** | **20章** | **~595页** |

---

*最后更新：2026-05-09*
