# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 常用开发命令

### 安装与设置
```bash
# 从源码安装（开发模式）
pip install -e .

# 使用 uv 安装（更快的安装方式）
uv pip install -e .

# 安装开发依赖
pip install -e .[dev]

# 安装 pre-commit 钩子
pre-commit install
```

### 测试命令
```bash
# 运行所有测试
pytest tests

# 运行带覆盖率的测试
coverage run -m pytest tests
coverage report -m

# 运行特定测试文件
pytest tests/react_agent_test.py
```

### 代码质量检查
```bash
# 运行所有 pre-commit 检查
pre-commit run --all-files

# 代码格式化
black --line-length=79 src/ tests/

# 类型检查
mypy src/

# 代码风格检查
flake8 src/ tests/

# 代码质量检查
pylint src/
```

### AgentScope Studio
```bash
# 安装 AgentScope Studio
npm install -g @agentscope/studio

# 启动 Studio
as_studio
```

## 项目架构概览

AgentScope 采用**模块化、松耦合**的分层架构设计，遵循**LEGO式组件组合**理念。每个核心模块都具备独立性和可插拔性。

### 核心模块结构

#### 1. **智能体核心** (`src/agentscope/agent/`)
- `AgentBase` - 通用智能体基类
- `ReActAgent` - ReAct智能体实现，支持工具调用和实时中断
- `UserAgent` - 用户交互智能体

#### 2. **模型适配层** (`src/agentscope/model/`)
- 支持 OpenAI、DashScope（通义千问）、Anthropic（Claude）、Gemini、Ollama 等模型
- 统一 API 接口，支持异步调用和流式响应
- 模型配置文件位于 `src/agentscope/models/` 目录

#### 3. **工具系统** (`src/agentscope/tool/`)
- 编码工具：Python 代码执行、Shell 命令执行
- 文件工具：文件读写、文本插入
- 多模态工具：图像生成、音频处理、图像识别
- 支持自定义工具注册

#### 4. **消息系统** (`src/agentscope/message/`)
- 支持文本、图像、音频、视频等多媒体消息块
- 统一的消息格式，支持多智能体通信

#### 5. **记忆管理** (`src/agentscope/memory/`)
- 短期记忆：`InMemoryMemory`
- 长期记忆：Mem0、ReMe 系列（个人、任务、工具记忆）

#### 6. **格式化器** (`src/agentscope/formatter/`)
- 为不同模型提供统一的输入输出格式转换
- 支持多智能体场景的格式化

#### 7. **工作流编排** (`src/agentscope/pipeline/`)
- `MsgHub` - 消息中心，管理多智能体对话
- `SequentialPipeline`、`FanoutPipeline` - 工作流管道

#### 8. **检索增强生成** (`src/agentscope/rag/`)
- 文档读取器：支持 PDF、Word、图片等格式
- 向量数据库：Qdrant、Milvus 等
- 知识库管理

#### 9. **其他重要模块**
- `src/agentscope/evaluate/` - 评估和基准测试
- `src/agentscope/embedding/` - 文本嵌入模型
- `src/agentscope/tracing/` - 执行追踪和监控
- `src/agentscope/mcp/` - MCP（Model Context Protocol）客户端

### 依赖关系
- 智能体核心依赖模型、记忆、消息、工具、格式化器
- 工作流编排使用智能体和消息系统
- RAG 模块相对独立，可选择性使用嵌入模型

## 开发规范

### 代码风格
- 遵循 Black 代码格式化（行长度 79）
- 使用 flake8 进行代码风格检查
- 通过 mypy 进行类型检查
- 使用 pylint 进行代码质量检查

### 导入原则
AgentScope 遵循**懒加载原则**，在函数内部导入模块以保持 `import agentscope` 的轻量级。

### 提交信息格式
遵循 Conventional Commits 规范：
```
<type>(<scope>): <subject>

例如：
feat(agent): add support for realtime interruption
fix(model): resolve memory leak in OpenAI model
docs(readme): update installation instructions
```

### 测试要求
- 新功能必须包含相应的单元测试
- 确保所有测试通过后再提交代码
- 测试文件位于 `tests/` 目录

### 示例代码
示例代码按类型组织在 `examples/` 目录：
- `examples/agent/` - 特定智能体示例
- `examples/functionality/` - 功能展示
- `examples/workflows/` - 工作流演示
- `examples/game/` - 游戏相关示例
- `examples/evaluation/` - 评估脚本

## 重要配置

### Python 版本要求
- 最低支持 Python 3.10
- 测试覆盖 Python 3.10, 3.11, 3.12

### 环境变量
根据使用的模型设置相应的 API 密钥：
- `OPENAI_API_KEY` - OpenAI API 密钥
- `DASHSCOPE_API_KEY` - 通义千问 API 密钥
- `ANTHROPIC_API_KEY` - Claude API 密钥

### 模型配置
模型配置文件通常包含：
- `model_name` - 模型名称
- `api_key` - API 密钥（可通过环境变量设置）
- `stream` - 是否启用流式响应
- 其他模型特定参数

## 常见问题

### 添加新的聊天模型
需要实现三个组件：
1. `ChatModelBase` 子类（模型实现）
2. `FormatterBase` 子类（格式化器）
3. 可选的 `TokenCounterBase` 子类（Token 计数器）

### 添加新工具
1. 实现工具函数，包含类型提示和文档字符串
2. 使用 `@tool` 装饰器注册工具
3. 将工具添加到 `Toolkit` 中

### 多智能体对话开发
使用 `MsgHub` 管理参与者，通过 `sequential_pipeline` 或其他管道编排对话流程。