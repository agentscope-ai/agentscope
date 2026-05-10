# 《AgentScope Agent开发实战》书籍结构

> **目标读者**: 有编程基础的开发者（尤其是Java开发者）
> **学习路径**: 从"Hello World"到"生产级Agent系统"

---

## 📚 整体结构

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

## 📖 各部分详细内容

### 第一部分：Python基础 (`part_i_getting_started/`)

本部分为Java开发者提供Python基础知识的快速入门，聚焦于与Java不同和Agent开发必需的概念。

```
part_i_getting_started/
├── chapter1_python_oop.md          # Python面向对象（vs Java）
├── chapter2_async_programming.md    # 异步编程（async/await）
├── chapter3_advanced_syntax.md       # 装饰器、上下文管理器、元类
└── summary.md                      # 部分小结
```

**内容要点**：
- 类与对象：Python的`self`、继承、接口
- 异步编程：协程、async/await、事件循环
- 装饰器：函数装饰器、类装饰器
- 上下文管理器：`with`语句、自定义`__enter__/__exit__`
- 元类：理解`type`、元类编程

---

### 第二部分：Agent开发基础 (`part_ii_core_concepts/`)

本部分介绍消息传递、管道编排和发布订阅模式，是理解Agent协作的基础。

```
part_ii_core_concepts/
├── chapter4_message_system.md      # Msg消息机制
├── chapter5_pipeline.md            # Pipeline管道
├── chapter6_msg_hub.md            # MsgHub发布订阅
└── summary.md                    # 部分小结
```

**内容要点**：
- Msg：消息的创建、发送、接收
- Pipeline：顺序编排、并行分发
- MsgHub：订阅机制、广播模式

---

### 第三部分：Agent核心原理 (`part_iii_advanced_topics/`)

本部分深入ReActAgent的工作机制，包括推理过程、Hook和模型调用。

```
part_iii_advanced_topics/
├── chapter7_react_agent.md        # ReActAgent原理
├── chapter8_hook.md               # Hook机制
├── chapter9_model_formatter.md    # Model与Formatter
└── summary.md                    # 部分小结
```

**内容要点**：
- ReAct：Reasoning + Acting循环
- Hook：拦截器、插件机制
- Formatter：消息格式化、模型适配
- ChatModel：统一模型接口

---

### 第四部分：工具与记忆 (`part_iv_tools_memory/`)

本部分介绍如何扩展Agent能力，包括工具调用和记忆管理。

```
part_iv_tools_memory/
├── chapter10_toolkit.md           # Toolkit工具系统
├── chapter11_memory.md           # Memory记忆系统
└── summary.md                    # 部分小结
```

**内容要点**：
- Toolkit：工具注册、函数调用
- Memory：短期记忆、长期记忆、RAG
- ToolResultBlock：工具返回格式化

---

### 第五部分：多Agent系统 (`part_v_multi_agent/`)

本部分介绍如何构建多Agent协作系统。

```
part_v_multi_agent/
├── chapter12_multi_agent.md       # 多Agent协作模式
├── chapter13_trace_debug.md       # 消息追踪与调试
└── summary.md                    # 部分小结
```

**内容要点**：
- FanoutPipeline：并行分发
- MsgHub：多Agent协调
- 消息追踪：完整数据流可视化

---

### 第六部分：部署与运维 (`part_vi_deployment/`)

本部分介绍如何将Agent系统部署到生产环境。

```
part_vi_deployment/
├── chapter14_runtime.md           # Runtime运行时
├── chapter15_docker.md           # Docker部署
└── summary.md                   # 部分小结
```

**内容要点**：
- Runtime：服务化、生命周期管理
- Docker：容器化、镜像构建
- 监控与日志

---

### 第七部分：项目实战 (`part_vii_projects/`)

本部分通过5个完整项目，将理论知识转化为实际能力。

```
part_vii_projects/
├── overview.md                   # 项目总览
├── project1_weather.md          # 天气查询Agent
├── project2_customer_service.md  # 智能客服
├── project3_debate.md           # 多Agent辩论
├── project4_research.md         # 深度研究助手
├── project5_voice.md            # 语音助手
└── summary.md                  # 部分小结
```

每个项目包含：
- 需求分析
- 系统设计
- 代码实现
- 测试验证
- 扩展思考

---

## 🛠️ 实践站点 (`practice/`)

实践站点提供"追踪式学习"，通过追踪数据在系统中的完整旅程来深入理解工作原理。

```
practice/
├── station1_departure/           # 1站：Python基础
│   ├── 1-0_python_basics.md
│   ├── 1-1_environment_setup.md
│   ├── 1-2_trace_your_first_agent.md
│   └── 1-3_glossary.md
├── station2_user_interface/      # 2站：Msg消息
│   ├── 2-1_what_is_msg.md
│   ├── 2-2_what_is_pipeline.md
│   ├── 2-3_what_is_msghub.md
│   ├── 2-4_trace_message_flow.md
│   └── 2-5_glossary.md
├── station3_agent_brain/         # 3站：ReActAgent
│   ├── 3-1_how_react_agent_works.md
│   ├── 3-2_what_is_hook.md
│   ├── 3-3_trace_agent_thinking.md
│   └── 3-4_glossary.md
├── station4_model_engine/        # 4站：Model/Formatter
│   ├── 4-1_unified_interface.md
│   ├── 4-2_what_is_formatter.md
│   ├── 4-3_trace_model_call.md
│   └── 4-4_glossary.md
├── station5_tools_memory/        # 5站：Toolkit/Memory
│   ├── 5-1_what_is_toolkit.md
│   ├── 5-2_how_memory_works.md
│   ├── 5-3_trace_tool_call.md
│   └── 5-4_glossary.md
├── station6_multi_agent/         # 6站：多Agent协作
│   ├── 6-1_what_is_pipeline_multi.md
│   ├── 6-2_what_is_msghub_multi.md
│   ├── 6-3_trace_multi_agent.md
│   └── 6-4_glossary.md
├── station7_deployment/          # 7站：部署运维
│   ├── 7-1_what_is_runtime.md
│   ├── 7-2_trace_runtime.md
│   ├── 7-3_docker_deployment.md
│   └── 7-4_glossary.md
└── station8_projects/            # 8站：项目实战
    ├── P8-1_weather_agent.md
    ├── P8-2_customer_service.md
    ├── P8-3_multi_agent_debate.md
    ├── P8-4_deep_research.md
    └── P8-5_voice_assistant.md
```

---

## 📚 参考资料 (`reference/`)

深度模块解析，提供各组件的详细源码解读。

```
reference/
├── module_agent_deep.md          # Agent模块深度解析
├── module_model_deep.md         # Model模块深度解析
├── module_tool_mcp_deep.md      # Tool/MCP模块深度解析
├── module_memory_rag_deep.md    # Memory/RAG模块深度解析
├── module_pipeline_infra_deep.md # Pipeline基础设施深度解析
├── module_formatter_deep.md     # Formatter模块深度解析
├── module_message_deep.md       # Message模块深度解析
├── module_dispatcher_deep.md    # Dispatcher模块深度解析
├── module_runtime_deep.md        # Runtime模块深度解析
├── module_session_deep.md       # Session模块深度解析
├── module_tracing_deep.md       # Tracing模块深度解析
├── module_state_deep.md         # StateModule深度解析
├── module_config_deep.md        # Config模块深度解析
├── module_utils_deep.md        # Utils模块深度解析
├── module_file_deep.md         # File模块深度解析
├── module_embedding_token_deep.md # Embedding/Token深度解析
├── module_plan_deep.md          # Plan模块深度解析
├── module_evaluate_deep.md      # Evaluate模块深度解析
├── module_tuner_deep.md        # Tuner模块深度解析
├── best_practices.md            # 最佳实践指南
├── case_studies.md             # 案例研究
├── reference_best_practices.md  # 最佳实践参考
└── reference_official_docs.md  # 官方文档参考
```

---

## 📎 附录 (`appendices/`)

```
appendices/
├── appendix_a.md                 # 术语对照表（Java vs Python vs AgentScope）
├── appendix_b.md                # Python语法速查卡
├── appendix_c.md                # 代码模板库
├── appendix_d.md                # 常见错误急救箱（快速错误查找表）
├── appendix_e.md                # 学习路径图（可裁剪）
└── troubleshooting.md          # 故障排除指南
```

> **使用建议**：
> - **appendix_d.md**：快速查找错误→解决方案的对照表
> - **troubleshooting.md**：深入阅读，理解错误原因和完整排查思路
> - 两者配合使用效果最佳：先用d定位问题，再用troubleshooting深入理解

---

## 🐍 Python基础教程 (`../python/`)

为Java开发者提供的Python语法速成教程。

```
../python/
├── 01_class_object.md           # 类与对象
├── 02_async_await.md          # 异步编程
├── 03_decorator.md            # 装饰器
├── 04_type_hints.md           # 类型提示
├── 05_dataclass.md            # 数据类
├── 06_context_manager.md       # 上下文管理器
├── 07_inheritance.md          # 继承与多态
├── 08_metaclass.md           # 元类
├── 09_module_system.md        # 模块系统
└── README.md                  # 教程说明
```

---

## 🚀 学习路径建议

### 路径A：快速入门（2周）
1. 第1-3章（Python基础）→ 跳过或快速浏览
2. 第4-6章（开发基础）→ 仔细阅读
3. 第16章（天气查询）→ 跟着做
4. 附录A（术语表）→ 随手查阅

### 路径B：系统学习（8周）
1. 第一部分（Python基础）→ 仔细阅读
2. 第二至五部分（核心原理）→ 仔细阅读 + 练习
3. 第16-17章（基础项目）→ 跟着做
4. 第六部分（部署）→ 阅读理解
5. 剩余项目 → 选做

### 路径C：深入精通（12周）
1. 完成路径B全部内容
2. 所有项目完整实现
3. 参考资料（reference/）→ 深入研究
4. 探索AgentScope源码

---

## 🔄 更新日志

- **v2.0** (2026-05-09): 重组为双轨学习系统（理论章节 + 实践站点）
- **v1.0** (2026-05-09): 初始书籍结构设计
