# AgentScope 教学书籍重构总体规划

> **当前状态**: v5.0 - 核心章节重构完成 + 质量审查 + 章节深化  
> **创建日期**: 2026-05-10  
> **最后更新**: 2026-05-10 (Iteration 4)

---

## 一、当前状态评估

### 1.1 现有内容规模

| 目录 | 文件数 | 总行数 | 质量评估 |
|------|-------|--------|---------|
| part_i_getting_started | 3章+summary | 1071 | ⚠️ Python语法，非AgentScope，应移至附录 |
| part_ii_core_concepts | 3章+summary | 1170 | ✅ 基础可用，需深化源码分析 |
| part_iii_advanced_topics | 3章+summary | 1259 | ✅ 核心模块，需加强调用链分析 |
| part_iv_tools_memory | 2章+summary | 724 | ⚠️ 内容偏薄，缺少RAG专门章节 |
| part_v_multi_agent | 2章+summary | 340 | ❌ 严重不足，Ch13仅90行 |
| part_vi_deployment | 2章+summary | 729 | ⚠️ 基本可用 |
| part_vii_projects | overview+5项目+summary | ~1500 | ✅ 项目结构完整 |
| practice/ (8站) | 31文件 | ~12000 | ❌ 与章节大量重复，需合并 |
| reference/ | 23文件 | ~27000 | ⚠️ 疑似AI生成填充，需逐篇验证 |
| python/ | 9章+README | ~2000 | ⚠️ 应作为附录，非主教材 |
| **总计** | **~80文件** | **~48000行** | |

### 1.2 关键问题

1. **Part I 定位错误**: 三个章节讲 Python OOP/Async/Advanced，与 AgentScope 无关
2. **章节-实践重复**: `part_*` 和 `practice/station*` 内容大量重叠
3. **Reference 膨胀**: reference/ 下有 27000 行内容，许多未经源码验证
4. **缺失关键模块**: plan/, tts/, session/, exception/, module/, realtime/ 无主章节
5. **无架构总览章**: 读者无法先理解整体架构再看细节
6. **A2A 协议缺失**: agent-to-agent 协议只在 reference 有，未整合进主书
7. **Contributor 指南弱**: 多数章节缺少实质性的 contributor 指导

### 1.3 源码覆盖缺口

| 源码模块 | 主书覆盖 | Reference覆盖 | 状态 |
|----------|---------|--------------|------|
| agent/ | Ch7 | ✅ | 充分 |
| model/ | Ch9 | ✅ | 充分 |
| message/ | Ch4 | ✅ | 充分 |
| pipeline/ | Ch5, Ch6, Ch12 | ✅ | 充分 |
| tool/ | Ch10 | ✅ | 基本 |
| memory/ | Ch11 | ✅ | 基本 |
| formatter/ | Ch9 | ✅ | 基本 |
| rag/ | - | ✅ (memory中) | 缺失独立章 |
| a2a/ | - | ✅ | **缺失主章** |
| realtime/ | - (Proj5提及) | ❌ | **严重缺失** |
| plan/ | - | ✅ | **缺失主章** |
| session/ | - | ✅ | **缺失主章** |
| exception/ | - | ❌ | **缺失** |
| types/ | - | ❌ | **缺失** |
| tts/ | - | ❌ | **缺失** |
| tracing/ | Ch13 (90行) | ✅ | 严重不足 |
| embedding/ | - | ✅ | **缺失主章** |
| token/ | - | ✅ | 可并入model章 |
| tuner/ | - | ✅ | **缺失主章** |
| evaluate/ | - | ✅ | **缺失主章** |
| mcp/ | - | ✅ (tool中) | 基本 |
| module/ | - | ✅ (state中) | **缺失主章** |

---

## 二、重构目标架构

### 2.1 新目录结构

```
teaching/book/
├── BOOK_INDEX.md                    # 书籍总目录 + 学习路径
├── README.md                        # 快速入口
├── CONTRIBUTING.md                  # 写作指南（保留）
├── MASTER_PLAN.md                   # 本文件
│
├── 00-architecture-overview/        # ★ 新增：架构总览
│   ├── 00-overview.md               # AgentScope 是什么、解决什么问题
│   ├── 00-source-map.md             # 源码地图：每个目录/模块的职责
│   └── 00-data-flow.md              # 核心数据流：一次 agent() 调用的完整旅程
│
├── 01-getting-started/              # 入门（合并原 Part I + Station 1）
│   ├── 01-installation.md           # 环境搭建、API Key 配置
│   ├── 01-first-agent.md            # 5分钟运行第一个 Agent
│   └── 01-concepts.md               # 核心概念速览：Agent/Msg/Model/Tool
│
├── 02-message-system/              # 消息系统（原 Ch4 + Station 2 精华）
│   ├── 02-msg-basics.md             # Msg 结构：name/content/role
│   ├── 02-content-blocks.md         # ContentBlock：TextBlock/ToolUseBlock/...
│   └── 02-message-lifecycle.md      # 消息生命周期：创建→传递→存储
│
├── 03-pipeline/                     # Pipeline 系统（原 Ch5, Ch6 + Station 2）
│   ├── 03-pipeline-basics.md        # SequentialPipeline
│   ├── 03-msghub.md                 # MsgHub 发布-订阅
│   └── 03-pipeline-advanced.md      # FanoutPipeline, functional pipelines
│
├── 04-agent-architecture/          # Agent 架构（原 Ch7 + Station 3）
│   ├── 04-agent-base.md             # AgentBase 源码分析
│   ├── 04-react-agent.md            # ReActAgent 完整调用链
│   ├── 04-user-agent.md             # UserAgent 人工参与
│   └── 04-a2a-agent.md              # ★ 新增：A2A 协议 Agent
│
├── 05-model-formatter/              # 模型与格式化（原 Ch9 + Station 4）
│   ├── 05-model-interface.md        # ChatModelBase 统一接口
│   ├── 05-openai-model.md           # OpenAI 模型适配
│   ├── 05-formatter-system.md       # Formatter 系统分析
│   └── 05-other-models.md           # Anthropic/Gemini/DashScope/Ollama
│
├── 06-tool-system/                  # 工具系统（原 Ch10 + Station 5）
│   ├── 06-toolkit-core.md           # Toolkit 核心源码
│   ├── 06-tool-registration.md      # 工具注册机制深度
│   ├── 06-tool-execution.md         # 工具调用执行流程
│   └── 06-mcp-integration.md        # MCP 协议集成
│
├── 07-memory-rag/                   # 记忆与知识库（原 Ch11 + 新增 RAG）
│   ├── 07-memory-architecture.md    # 记忆系统总体设计
│   ├── 07-working-memory.md         # 工作记忆：InMemory/Redis/SQLAlchemy
│   ├── 07-long-term-memory.md       # 长期记忆：Mem0/ReMe
│   └── 07-rag-knowledge.md          # ★ 新增：RAG 知识库系统
│
├── 08-multi-agent/                  # 多 Agent 系统（原 Ch12, Ch13 + Station 6）
│   ├── 08-multi-agent-patterns.md   # 多 Agent 协作模式
│   ├── 08-msg-hub-patterns.md       # MsgHub 高级模式
│   ├── 08-a2a-protocol.md           # ★ 新增：A2A 协议详解
│   └── 08-tracing-debugging.md      # Tracing 追踪与调试
│
├── 09-advanced-modules/             # ★ 新增：高级模块
│   ├── 09-plan-module.md            # Plan 规划模块
│   ├── 09-session-management.md     # Session 会话管理
│   ├── 09-realtime-agent.md         # Realtime 实时语音
│   ├── 09-tts-system.md             # TTS 语音合成
│   ├── 09-evaluate-system.md        # Evaluate 评估系统
│   └── 09-tuner-system.md           # Tuner 调优系统
│
├── 10-deployment/                   # 部署（原 Ch14, Ch15 + Station 7）
│   ├── 10-runtime.md                # Runtime 服务化
│   └── 10-docker-production.md      # Docker 生产部署
│
├── 11-projects/                     # 项目实战（原 Part VII + Station 8）
│   ├── 11-weather-agent.md
│   ├── 11-customer-service.md
│   ├── 11-multi-agent-debate.md
│   ├── 11-deep-research.md
│   └── 11-voice-assistant.md
│
├── 12-contributing/                 # ★ 新增：Contributor 成长路径
│   ├── 12-how-to-contribute.md      # PR 流程、代码规范
│   ├── 12-codebase-navigation.md    # 源码导航地图
│   ├── 12-debugging-guide.md        # 调试指南
│   └── 12-architecture-decisions.md # 架构决策记录
│
├── appendices/                      # 附录（精简）
│   ├── appendix-a-glossary.md       # 术语表
│   ├── appendix-b-python-primer.md  # ★ 合并原 python/ 和 Part I
│   ├── appendix-c-troubleshooting.md # 故障排除
│   └── appendix-d-api-reference.md  # API 快速参考
│
└── reference/                       # 深度参考（精简验证）
    ├── module-agent-deep.md
    ├── module-model-deep.md
    ├── module-message-deep.md
    └── ... (保留但精简到真实内容)
```

### 2.2 新旧对应关系

| 旧结构 | 新结构 | 变化说明 |
|--------|--------|---------|
| part_i_getting_started (3章) | appendices/appendix-b-python-primer.md | 降级为附录 |
| Station 1 | 01-getting-started/ | 合并入主教材 |
| part_ii_core_concepts (3章) | 02-message-system/ + 03-pipeline/ | 拆分展开 |
| Station 2 | 合并入 02 + 03 | 消除重复 |
| part_iii_advanced_topics (3章) | 04-agent-architecture/ + 05-model-formatter/ | 拆分扩大 |
| Station 3, 4 | 合并入 04, 05 | 消除重复 |
| part_iv_tools_memory (2章) | 06-tool-system/ + 07-memory-rag/ | 拆分扩大，RAG独立 |
| Station 5 | 合并入 06, 07 | 消除重复 |
| part_v_multi_agent (2章) | 08-multi-agent/ | 大幅扩充 |
| Station 6 | 合并入 08 | 消除重复 |
| part_vi_deployment (2章) | 10-deployment/ | 保留重建 |
| Station 7 | 合并入 10 | 消除重复 |
| part_vii_projects (5项目) | 11-projects/ | 保留重建 |
| Station 8 | 合并入 11 | 消除重复 |
| - | 09-advanced-modules/ | ★ 全新 |
| - | 12-contributing/ | ★ 全新 |
| python/ (9章) | appendices/appendix-b-python-primer.md | 降级合并 |
| reference/ (23文件) | reference/ (精简至15个) | 删除未经验证的内容 |

### 2.3 学习路径 (Level 1-9) 重新映射

| Level | 目标 | 对应章节 | 预计时间 |
|-------|------|---------|---------|
| 1 | 知道项目是什么 | 00-overview, 01-getting-started | 2小时 |
| 2 | 能运行项目 | 01-first-agent, 02-msg-basics | 2小时 |
| 3 | 理解模块边界 | 03-pipeline, 04-agent-base | 4小时 |
| 4 | 理解核心数据流 | 04-react-agent, 05-model-interface | 5小时 |
| 5 | 能跟踪源码调用链 | 05-formatter, 06-toolkit-core | 6小时 |
| 6 | 能修改小功能 | 07-memory, 08-multi-agent | 6小时 |
| 7 | 能独立开发模块 | 09-advanced-modules | 8小时 |
| 8 | 能提交高质量 PR | 11-projects, 12-contributing | 10小时 |
| 9 | 能参与架构讨论 | reference/ + 12-architecture-decisions | 持续 |

---

## 三、章节模板（每章必须包含）

### 3.1 8 项必需元素

1. **学习目标** - 学完后能做什么
2. **背景问题** - 为什么这个模块存在
3. **源码入口** - 文件路径、类名、方法名
4. **架构定位** - 模块职责、生命周期、数据流
5. **核心源码分析** - 按真实调用链分析
6. **可视化结构** - Mermaid 图
7. **工程经验** - 设计原因、替代方案、坑
8. **Contributor 指南** - 调试方法、测试策略、危险区域

### 3.2 源码引用标准

```markdown
**文件**: `src/agentscope/agent/_react_agent.py:376-537`

所有行号必须在每次编写时通过 `grep -n` 验证。
所有类名/方法名/参数名必须与源码一致。
```

---

## 四、执行计划

### 第一阶段：基础设施（Iteration 1-3）
- [x] 评估现有内容质量和覆盖度
- [ ] 确定最终目录结构
- [ ] 删除 practice/ 目录（内容合并入主章节）
- [ ] 创建新目录结构
- [ ] 重写 BOOK_INDEX.md
- [ ] 重写 README.md

### 第二阶段：核心章节重写（Iteration 4-20）
按 Level 顺序逐章重写：
1. 00-architecture-overview (3文件)
2. 01-getting-started (3文件)
3. 02-message-system (3文件)
4. 03-pipeline (3文件)
5. 04-agent-architecture (4文件)
6. 05-model-formatter (4文件)
7. 06-tool-system (4文件)
8. 07-memory-rag (4文件)
9. 08-multi-agent (4文件)
10. 09-advanced-modules (6文件)
11. 10-deployment (2文件)
12. 11-projects (5文件)
13. 12-contributing (4文件)
14. appendices/ (4文件)
15. reference/ (清理验证)

### 第三阶段：质量审查（Iteration 21-25）
- 交叉验证所有源码引用
- 术语统一检查
- 学习路径连贯性检查
- Contributor 指南完整性检查

### 第四阶段：最终打磨（Iteration 26-30）
- 格式统一
- 死链接修复
- 样例代码可运行性验证

---

## 五、质量度量标准

### 5.1 每章通过标准

- [ ] 8 项必需元素全部包含
- [ ] 所有源码路径通过 `ls` 验证
- [ ] 所有行号通过 `grep -n` 验证
- [ ] 至少包含 2 个 Mermaid 图
- [ ] 至少包含 3 个工程经验点
- [ ] Contributor 指南具体到文件级别
- [ ] 与前后章节无术语冲突

### 5.2 整书通过标准

- [ ] 学习路径 Level 1-9 流畅递进
- [ ] 所有源码模块被覆盖
- [ ] 无一章缺失 8 项元素
- [ ] 代码示例全部可运行
- [ ] 术语定义统一（全书一个 glossary）
- [ ] 重复内容消除（practice 已合并）

---

*本计划将随执行进展持续更新。*
