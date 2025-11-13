# 个人智能体知识管理系统 (Personal Agent Knowledge Management System)

基于 AgentScope 框架构建的个人知识管理和智能体协作系统，旨在帮助用户高效管理知识并实现长期目标。

## 项目概述

### 核心理念
> 个人成长的核心系统：PKM作为知识源，Agent协作系统作为辅助，结合针对个人的元学习策略。

### 主要功能
- **多端文章收集**: 支持Web插件、一键分享到inbox
- **AI Agent协作**: 多智能体协作系统
- **个人知识库**: 基于PostgreSQL的知识索引与存储
- **记忆系统**: 长期记忆与工作记忆管理
- **元学习策略**: 基于快速学习方法的智能辅助

## 项目结构

```
agent-app/
├── src/
│   ├── agents/          # 智能体实现
│   ├── models/          # 数据模型
│   ├── memory/          # 记忆管理
│   ├── tools/           # 工具集合
│   ├── pipelines/       # 工作流编排
│   └── utils/           # 工具函数
├── config/              # 配置文件
├── data/
│   ├── inbox/          # 收件箱
│   └── knowledge_base/ # 知识库
├── docs/               # 文档
├── examples/           # 示例代码
└── tests/              # 测试代码
```

## 核心Agent设计

### 1. KnowledgeCollectorAgent (知识收集智能体)
- 负责从多端收集文章和资料
- 支持网页抓取、文档解析
- 自动分类和标签提取

### 2. KnowledgeOrganizerAgent (知识整理智能体)
- 对收集的知识进行整理和归纳
- 提取关键信息和知识点
- 建立知识关联

### 3. LearningStrategyAgent (学习策略智能体)
- 基于元学习策略制定学习计划
- 监控学习进度
- 提供个性化学习建议

### 4. GoalAssistantAgent (目标辅助智能体)
- 帮助用户完成长期目标
- 分解复杂任务
- 提供执行指导

### 5. MemoryManagerAgent (记忆管理智能体)
- 管理用户的长期记忆和工作记忆
- 智能检索相关信息
- 记忆强化和遗忘管理

## 技术栈

- **框架**: AgentScope 1.0
- **数据库**: PostgreSQL
- **向量存储**: Qdrant/Milvus
- **缓存**: Redis
- **Web插件**: JavaScript + Content Script
- **API**: FastAPI (可选)

## 快速开始

### 1. 环境准备
```bash
cd agent-app
pip install -e ../
pip install agentscope[full]
```

### 2. 配置环境变量
```bash
export KNOWLEDGE_DB_URL="postgresql://user:pass@localhost/knowledge_db"
export OPENAI_API_KEY="your_openai_key"
export AGENTSCOPE_API_KEY="your_agentscope_key"
```

### 3. 运行示例
```bash
python examples/knowledge_collector_demo.py
```

## 开发计划

### Phase 1: MVP 后端 (2-3周)
- [ ] 基础知识收集功能
- [ ] 简单的知识存储和检索
- [ ] 基本的Agent协作框架
- [ ] PostgreSQL数据库设计

### Phase 2: 核心功能 (4-6周)
- [ ] 完整的多智能体协作系统
- [ ] 高级知识管理和检索
- [ ] 长期记忆系统
- [ ] 元学习策略实现

### Phase 3: 前端和插件 (3-4周)
- [ ] Web浏览器插件
- [ ] 移动端分享功能
- [ ] 用户界面优化
- [ ] 数据可视化

## 学习策略参考

本项目参考了以下快速学习方法：
1. **目标导向学习**: 明确学习目标，制定学习路径
2. **费曼学习法**: 通过教授来深化理解
3. **间隔重复**: 基于遗忘曲线的复习策略
4. **知识连接**: 建立新旧知识的关联
5. **实践应用**: 在实践中巩固知识

## 贡献指南

欢迎提交Issue和PR来改进这个项目！

## 许可证

MIT License