# 《AgentScope Agent开发实战》

> **致敬经典**：本书借鉴《网络是怎样连接的》(户根勤 著) 的写作风格
> **目标读者**：有编程基础的开发者（尤其是Java开发者）
> **学习路径**：从"Hello World"到"生产级Agent系统"

---

## 🎯 这本书能让你学到什么

1. **掌握AgentScope核心概念**：Msg、Pipeline、MsgHub
2. **理解ReActAgent工作原理**：Reasoning + Acting循环
3. **开发多Agent协作系统**：发布-订阅、管道编排
4. **部署Agent到生产环境**：Runtime、Docker
5. **完成5个实战项目**：天气、客服、辩论、研究、语音

---

## 📚 书籍结构

```
┌────────────────────────────────────────────────────────────────────┐
│                      第一部分：Python基础                              │
│                 （为Java开发者量身定制）                              │
│   第1章：Python面向对象    第2章：异步编程    第3章：高级语法           │
├────────────────────────────────────────────────────────────────────┤
│                      第二部分：Agent开发基础                          │
│        第4章：消息传递    第5章：管道流水线    第6章：发布订阅          │
├────────────────────────────────────────────────────────────────────┤
│                      第三部分：Agent核心原理                          │
│      第7章：ReActAgent    第8章：Hook    第9章：Model/Formatter      │
├────────────────────────────────────────────────────────────────────┤
│                      第四部分：工具与记忆                              │
│                  第10章：Toolkit    第11章：Memory                   │
├────────────────────────────────────────────────────────────────────┤
│                      第五部分：多Agent系统                            │
│                第12章：协作模式    第13章：追踪调试                    │
├────────────────────────────────────────────────────────────────────┤
│                      第六部分：部署与运维                              │
│                    第14章：Runtime    第15章：Docker                   │
├────────────────────────────────────────────────────────────────────┤
│                      第七部分：项目实战                                │
│        项目1-5：天气Agent → 客服机器人 → 辩论系统 → 研究助手 → 语音助手  │
└────────────────────────────────────────────────────────────────────┘
```

---

## 🚀 快速开始

### 推荐学习路径

| 路径 | 适合人群 | 时间 | 内容 |
|------|---------|------|------|
| **快速入门** | Python熟手 | 1周 | 第4-6章 + 项目1 |
| **系统学习** | 一般开发者 | 4周 | 第1-15章 + 项目1-3 |
| **深入精通** | 全栈工程师 | 8周 | 全部 + 项目4-5 |

### 环境准备

```bash
# 1. 安装AgentScope
pip install agentscope

# 2. 验证安装
python -c "import agentscope; print('OK')"

# 3. 快速测试
python -m agentscope.examples.quickstart
```

---

## 📖 各部分内容

### [第一部分：Python基础](./part_i_getting_started/)

为Java开发者提供Python核心知识速览。

| 章节 | 主题 | 关键点 |
|------|------|--------|
| [第1章](./part_i_getting_started/chapter1_python_oop.md) | 面向对象 | self、继承、dataclass |
| [第2章](./part_i_getting_started/chapter2_async_programming.md) | 异步编程 | async/await、事件循环 |
| [第3章](./part_i_getting_started/chapter3_advanced_syntax.md) | 高级语法 | 装饰器、上下文管理器、元类 |

---

### [第二部分：Agent开发基础](./part_ii_core_concepts/)

理解AgentScope的三大核心概念。

| 章节 | 主题 | 关键点 |
|------|------|--------|
| [第4章](./part_ii_core_concepts/chapter4_message_system.md) | Msg消息 | role、ContentBlock |
| [第5章](./part_ii_core_concepts/chapter5_pipeline.md) | Pipeline | Sequential、Fanout |
| [第6章](./part_ii_core_concepts/chapter6_msg_hub.md) | MsgHub | 发布-订阅、松耦合 |

---

### [第三部分：Agent核心原理](./part_iii_advanced_topics/)

深入ReActAgent的工作机制。

| 章节 | 主题 | 关键点 |
|------|------|--------|
| [第7章](./part_iii_advanced_topics/chapter7_react_agent.md) | ReActAgent | Reasoning+Acting循环 |
| [第8章](./part_iii_advanced_topics/chapter8_hook.md) | Hook | 拦截器、AOP |
| [第9章](./part_iii_advanced_topics/chapter9_model_formatter.md) | Model/Formatter | 统一接口、格式适配 |

---

### [第四部分：工具与记忆](./part_iv_tools_memory/)

扩展Agent能力。

| 章节 | 主题 | 关键点 |
|------|------|--------|
| [第10章](./part_iv_tools_memory/chapter10_toolkit.md) | Toolkit | 工具注册、函数调用 |
| [第11章](./part_iv_tools_memory/chapter11_memory.md) | Memory | 短期/长期、RAG |

---

### [第五部分：多Agent系统](./part_v_multi_agent/)

构建多Agent协作。

| 章节 | 主题 | 关键点 |
|------|------|--------|
| [第12章](./part_v_multi_agent/chapter12_multi_agent.md) | 协作模式 | 流水线、广播、分层 |
| [第13章](./part_v_multi_agent/chapter13_trace_debug.md) | 追踪调试 | tracing、可视化 |

---

### [第六部分：部署与运维](./part_vi_deployment/)

生产环境部署。

| 章节 | 主题 | 关键点 |
|------|------|--------|
| [第14章](./part_vi_deployment/chapter14_runtime.md) | Runtime | 生命周期、服务化 |
| [第15章](./part_vi_deployment/chapter15_docker.md) | Docker | 容器化、docker-compose |

---

### [第七部分：项目实战](./part_vii_projects/)

5个完整项目。

| 项目 | 难度 | 核心技能 | 预计时间 |
|------|------|---------|---------|
| [天气查询](./part_vii_projects/project1_weather.md) | ⭐ | Toolkit基础 | 2小时 |
| [智能客服](./part_vii_projects/project2_customer_service.md) | ⭐⭐ | 多工具、RAG | 4小时 |
| [多Agent辩论](./part_vii_projects/project3_debate.md) | ⭐⭐⭐ | MsgHub协作 | 6小时 |
| [深度研究](./part_vii_projects/project4_research.md) | ⭐⭐⭐⭐ | 多Agent编排 | 8小时 |
| [语音对话](./part_vii_projects/project5_voice.md) | ⭐⭐⭐⭐⭐ | 实时交互 | 12小时 |

---

## ✨ 书籍特色

1. **Java锚定** - 用Java概念类比理解Python/AgentScope
2. **追踪式学习** - 追踪数据在系统中的完整旅程
3. **图解优先** - 每一步都用ASCII图说明
4. **项目驱动** - 5个完整项目串联知识点
5. **先跑起来** - 代码完整可运行

---

## 📊 学习时间估算

| 部分 | 预计时间 |
|------|----------|
| 第一部分：Python基础 | 6小时 |
| 第二部分：Agent开发基础 | 4小时 |
| 第三部分：Agent核心原理 | 6小时 |
| 第四部分：工具与记忆 | 4小时 |
| 第五部分：多Agent系统 | 4小时 |
| 第六部分：部署与运维 | 3小时 |
| 第七部分：项目实战 | 30小时 |
| **总计** | **~57小时** |

---

## 🔗 配套资源

- [AgentScope官方文档](https://agentscope.readthedocs.io)
- [Python基础教程](./python/)
- [附录：术语对照表](./appendices/appendix_a.md)
- [附录：Python速查卡](./appendices/appendix_b.md)
- [附录：代码模板](./appendices/appendix_c.md)

---

## 📝 参与贡献

欢迎提交Issue和Pull Request！

详见 [CONTRIBUTING.md](./CONTRIBUTING.md)

---

## License

本书为 AgentScope 学习资料，基于 Apache 2.0 许可证。

---

*最后更新：2026-05-09*
