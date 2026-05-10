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
teaching/
├── book/                           # 主教材
│   ├── README.md                   # 本文件
│   ├── BOOK_INDEX.md               # 详细目录
│   ├── part_i_getting_started/     # 第一部分：Python基础
│   ├── part_ii_core_concepts/     # 第二部分：Agent开发基础
│   ├── part_iii_advanced_topics/  # 第三部分：Agent核心原理
│   ├── part_iv_tools_memory/      # 第四部分：工具与记忆
│   ├── part_v_multi_agent/         # 第五部分：多Agent系统
│   ├── part_vi_deployment/         # 第六部分：部署与运维
│   ├── part_vii_projects/          # 第七部分：项目实战
│   ├── practice/                    # 实践练习（8站）
│   │   ├── station1_departure/    # 1站：Python基础
│   │   ├── station2_user_interface/  # 2站：Msg消息
│   │   ├── station3_agent_brain/   # 3站：ReActAgent
│   │   ├── station4_model_engine/  # 4站：Model/Formatter
│   │   ├── station5_tools_memory/  # 5站：Toolkit/Memory
│   │   ├── station6_multi_agent/   # 6站：多Agent协作
│   │   ├── station7_deployment/     # 7站：部署运维
│   │   └── station8_projects/        # 8站：项目实战
│   ├── appendices/                 # 附录（A-E）
│   │   ├── appendix_a.md           # 术语对照表
│   │   ├── appendix_b.md           # Python速查卡
│   │   ├── appendix_c.md           # 代码模板库
│   │   ├── appendix_d.md           # 常见错误急救箱
│   │   ├── appendix_e.md           # 学习路径图
│   │   └── troubleshooting.md     # 故障排除
│   └── reference/                  # 参考资料（深度内容）
│       ├── module_*_deep.md      # 20个模块深度解析
│       ├── best_practices.md      # 最佳实践
│       ├── case_studies.md        # 案例研究
│       └── reference_*.md         # 官方文档参考
└── python/                        # Python语法教程（前置内容）
    ├── 01_class_object.md         # 类与对象
    ├── 02_async_await.md          # 异步编程
    └── ...                        # 共9章
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
cd examples/agent/react_agent && python main.py
```

---

## 📐 学习路径依赖图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Level 1: 入门级                                     │
│  ┌─────────────────┐                                                        │
│  │ Station 1       │ → 安装环境 + 运行第一个Agent                              │
│  │ 环境搭建        │                                                        │
│  └────────┬────────┘                                                        │
│           ▼                                                                 │
│  ┌─────────────────┐                                                        │
│  │ Station 2-1    │ → 理解Msg消息                                           │
│  │ Msg是什么      │                                                        │
│  └────────┬────────┘                                                        │
│           ▼                                                                 │
│  ┌─────────────────┐                                                        │
│  │ Chapter 4      │ → 深入Msg系统 + ContentBlock                            │
│  │ 消息传递机制    │                                                        │
│  └────────┬────────┘                                                        │
└───────────┼─────────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Level 2: 基础级                                     │
│  ┌─────────────────┐                                                        │
│  │ Chapter 5      │ → Pipeline管道编排                                       │
│  │ 管道流水线      │                                                        │
│  └────────┬────────┘                                                        │
│           ▼                                                                 │
│  ┌─────────────────┐                                                        │
│  │ Chapter 6      │ → MsgHub发布订阅                                         │
│  │ 发布-订阅模式   │                                                        │
│  └────────┬────────┘                                                        │
│           ▼                                                                 │
│  ┌─────────────────┐                                                        │
│  │ Station 3-1    │ → 理解ReActAgent工作原理                                 │
│  │ ReAct如何工作   │                                                        │
│  └────────┬────────┘                                                        │
└───────────┼─────────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Level 3: 进阶级                                     │
│  ┌─────────────────┐                                                        │
│  │ Chapter 7      │ → ReActAgent核心源码                                     │
│  │ ReActAgent原理 │                                                        │
│  └────────┬────────┘                                                        │
│           ▼                                                                 │
│  ┌─────────────────┐                                                        │
│  │ Chapter 8      │ → Hook机制                                              │
│  │ Hook机制       │                                                        │
│  └────────┬────────┘                                                        │
│           ▼                                                                 │
│  ┌─────────────────┐                                                        │
│  │ Chapter 9      │ → Model与Formatter                                      │
│  │ 模型格式化      │                                                        │
│  └────────┬────────┘                                                        │
└───────────┼─────────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Level 4: 高级级                                     │
│  ┌─────────────────┐                                                        │
│  │ Chapter 10-11   │ → Toolkit + Memory                                      │
│  │ 工具与记忆      │                                                        │
│  └────────┬────────┘                                                        │
│           ▼                                                                 │
│  ┌─────────────────┐                                                        │
│  │ Chapter 12-13   │ → 多Agent系统 + 调试追踪                                 │
│  │ 多Agent协作     │                                                        │
│  └────────┬────────┘                                                        │
└───────────┼─────────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Level 5: 实战级                                     │
│  ┌─────────────────┐                                                        │
│  │ Project 1-5     │ → 5个完整项目实战                                       │
│  │ 项目实战        │                                                        │
│  └─────────────────┘                                                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 学习路径说明

| 层级 | 名称 | 核心技能 | 预计时间 |
|------|------|----------|----------|
| Level 1 | 入门级 | 环境搭建、Msg消息 | 2小时 |
| Level 2 | 基础级 | Pipeline、MsgHub、ReAct循环 | 4小时 |
| Level 3 | 进阶级 | Agent源码、Hook、Formatter | 6小时 |
| Level 4 | 高级级 | Toolkit、Memory、多Agent | 4小时 |
| Level 5 | 实战级 | 完整项目开发 | 30小时 |

---

## 🔗 源码映射索引

### 核心模块源码位置

| 模块 | 源码路径 | 对应章节 | 对应站点 |
|------|----------|----------|----------|
| **Msg** | `src/agentscope/message/_message_base.py` | Chapter 4 | Station 2-1 |
| **ContentBlock** | `src/agentscope/message/_message_block.py` | Chapter 4 | Station 2-1 |
| **Pipeline** | `src/agentscope/pipeline/_class.py` | Chapter 5 | Station 2-2 |
| **MsgHub** | `src/agentscope/pipeline/_msghub.py` | Chapter 6 | Station 2-3 |
| **AgentBase** | `src/agentscope/agent/_agent_base.py` | Chapter 7 | Station 3 |
| **ReActAgent** | `src/agentscope/agent/_react_agent.py` | Chapter 7 | Station 3-1 |
| **ReActAgentBase** | `src/agentscope/agent/_react_agent_base.py` | Chapter 7 | Station 3 |
| **Hook** | `src/agentscope/hooks/` | Chapter 8 | Station 3-2 |
| **FormatterBase** | `src/agentscope/formatter/` | Chapter 9 | Station 4 |
| **ChatModelBase** | `src/agentscope/model/` | Chapter 9 | Station 4-1 |
| **Toolkit** | `src/agentscope/tool/_toolkit.py` | Chapter 10 | Station 5 |
| **ToolResponse** | `src/agentscope/tool/_response.py` | Chapter 10 | Station 5 |
| **MemoryBase** | `src/agentscope/memory/` | Chapter 11 | Station 5-2 |
| **LongTermMemory** | `src/agentscope/memory/` | Chapter 11 | Station 5-2 |
| **FanoutPipeline** | `src/agentscope/pipeline/_functional.py` | Chapter 12 | Station 6 |
| **Runtime** | `src/agentscope/runtime/` | Chapter 14 | Station 7 |
| **A2A Protocol** | `src/agentscope/a2a/` | - | - |

### 源码行号速查

| 类/方法 | 文件 | 行号 |
|---------|------|------|
| `Msg.__init__` | `_message_base.py` | 21 |
| `Msg.to_dict` | `_message_base.py` | 75 |
| `Msg.get_content_blocks` | `_message_base.py` | 150-198 |
| `TextBlock` | `_message_block.py` | 9 |
| `ToolUseBlock` | `_message_block.py` | 79 |
| `ToolResultBlock` | `_message_block.py` | 94 |
| `MsgHub.__init__` | `_msghub.py` | 42 |
| `MsgHub.broadcast` | `_msghub.py` | 130 |
| `AgentBase.reply` | `_agent_base.py` | 197 |
| `AgentBase.observe` | `_agent_base.py` | 185 |
| `ReActAgent.reply` | `_react_agent.py` | 376 |
| `ReActAgent._reasoning` | `_react_agent.py` | 540 |
| `ReActAgent._acting` | `_react_agent.py` | 657 |
| `Toolkit.register_tool_function` | `_toolkit.py` | 274 |
| `Toolkit.call_tool_function` | `_toolkit.py` | 853 |

### 深度文档索引

| 主题 | 深度文档 |
|------|----------|
| Agent系统 | [module_agent_deep.md](./reference/module_agent_deep.md) |
| Model系统 | [module_model_deep.md](./reference/module_model_deep.md) |
| 消息系统 | [module_message_deep.md](./reference/module_message_deep.md) |
| 工具系统 | [module_tool_mcp_deep.md](./reference/module_tool_mcp_deep.md) |
| 记忆系统 | [module_memory_rag_deep.md](./reference/module_memory_rag_deep.md) |
| Pipeline | [module_pipeline_infra_deep.md](./reference/module_pipeline_infra_deep.md) |
| Formatter | [module_formatter_deep.md](./reference/module_formatter_deep.md) |
| Runtime | [module_runtime_deep.md](./reference/module_runtime_deep.md) |
| A2A协议 | [module_a2a_deep.md](./reference/module_a2a_deep.md) |

---

## 📖 双轨学习系统

本书提供**两条并行学习路径**，适合不同学习风格：

### 理论路径：章节学习 (`part_*`)

系统化学习每个主题的理论知识。

| 部分 | 章节 | 主题 |
|------|------|------|
| [第一部分](./part_i_getting_started/) | 第1-3章 | Python基础 |
| [第二部分](./part_ii_core_concepts/) | 第4-6章 | Agent开发基础 |
| [第三部分](./part_iii_advanced_topics/) | 第7-9章 | Agent核心原理 |
| [第四部分](./part_iv_tools_memory/) | 第10-11章 | 工具与记忆 |
| [第五部分](./part_v_multi_agent/) | 第12-13章 | 多Agent系统 |
| [第六部分](./part_vi_deployment/) | 第14-15章 | 部署与运维 |
| [第七部分](./part_vii_projects/) | 项目1-5 | 项目实战 |

### 实践路径：站点练习 (`practice/station*`)

通过追踪代码执行路径，深入理解系统工作原理。

| 站 | 内容 | 对应章节 |
|----|------|----------|
| [1站：出发](./practice/station1_departure/) | Python基础、环境搭建 | 第1-2章 |
| [2站：用户界面](./practice/station2_user_interface/) | Msg消息、Pipeline、MsgHub | 第4-6章 |
| [3站：Agent大脑](./practice/station3_agent_brain/) | ReActAgent、Hook机制 | 第7-8章 |
| [4站：Model引擎](./practice/station4_model_engine/) | Model、Formatter | 第9章 |
| [5站：工具与记忆](./practice/station5_tools_memory/) | Toolkit、Memory | 第10-11章 |
| [6站：多Agent](./practice/station6_multi_agent/) | 多Agent协作 | 第12-13章 |
| [7站：部署上线](./practice/station7_deployment/) | Runtime、Docker | 第14-15章 |
| [8站：项目实战](./practice/station8_projects/) | 5个完整项目 | 项目1-5 |

**建议**：先学习对应章节，再完成站点练习巩固知识。

---

## ✨ 书籍特色

1. **Java锚定** - 用Java概念类比理解Python/AgentScope
2. **追踪式学习** - 追踪数据在系统中的完整旅程
3. **图解优先** - 每一步都用ASCII图说明
4. **项目驱动** - 5个完整项目串联知识点
5. **先跑起来** - 代码完整可运行
6. **双轨并行** - 理论章节 + 实践站点，互补学习

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
- [Python基础教程](../python/) - Java开发者Python速成
- [模块深度解析](./reference/) - 20个模块源码解读
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
