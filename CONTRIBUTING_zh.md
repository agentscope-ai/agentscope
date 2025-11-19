# 贡献到 AgentScope

## 欢迎！🎉

感谢开源社区对 AgentScope 项目的关注和支持，作为一个开源项目，我们热烈欢迎并鼓励来自社区的贡献。无论是修复错误、添加新功能、改进文档还是
分享想法，这些贡献都能帮助 AgentScope 变得更好。

## 如何贡献

为了确保顺利协作并保持项目质量，请在贡献时遵循以下指南：

### 1. 检查现有计划和问题

在开始贡献之前，请查看我们的开发路线图：

- **查看 [Projects](https://github.com/orgs/agentscope-ai/projects/2) 页面** 和 **[带有 `roadmap` 标签的 Issues](https://github.com/agentscope-ai/agentscope/issues?q=is%3Aissue%20state%3Aopen%20label%3ARoadmap)** 以了解我们计划的开发任务。

  - **如果存在相关问题** 并且标记为未分配或开放状态：
    - 请在该问题下评论，表达您有兴趣参与该任务
    - 这有助于协调开发工作，避免重复工作

  - **如果不存在相关问题**：
    - 请创建一个新 issue 用以描述对应的更改或功能
    - 我们的团队将及时进行回复并提供反馈
    - 这有助于我们维护项目路线图并协调社区工作

### 2. 提交信息格式

AgentScope 遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范。这使得提交历史更易读，并能够自动生成更新日志。

**格式：**
```
<type>(<scope>): <subject>
```

**类型：**
- `feat:` 新功能
- `fix:` 错误修复
- `docs:` 仅文档更改
- `style:` 不影响代码含义的更改（空格、格式等）
- `refactor:` 既不修复错误也不添加功能的代码更改
- `perf:` 提高性能的代码更改
- `ci:` 添加缺失的测试或更正现有测试
- `chore:` 对构建过程或辅助工具和库的更改

**示例：**
```bash
feat(models): add support for Claude-3 model
fix(agent): resolve memory leak in ReActAgent
docs(readme): update installation instructions
refactor(formatter): simplify message formatting logic
ci(models): add unit tests for OpenAI integration
```

### 3. 代码开发指南

#### a. 提交前检查

在提交代码之前，请运行 pre-commit 钩子以确保代码质量和一致性：

**安装：**
```bash
pip install pre-commit
pre-commit install
```

**运行 pre-commit：**
```bash
# 在所有文件上运行
pre-commit run --all-files

# 安装后，pre-commit 将在 git commit 时自动运行
```

#### b. 关于代码中的 Import

AgentScope 遵循**懒加载导入原则**以最小化资源加载：

- **推荐做法**：仅在实际使用时导入模块
  ```python
  def some_function():
      import openai
      # 在此处使用 openai 库
  ```

这种方法确保 `import agentscope` 是一个轻量操作，不会加载不必要的依赖项。

#### c. 单元测试

- 所有新功能都必须包含适当的单元测试
- 在提交 PR 之前确保现有测试通过
- 使用以下命令运行测试：
  ```bash
  pytest tests
  ```

#### d. 文档

- 为新功能更新相关文档
- 在适当的地方包含代码示例
- 如果更改影响面向用户的功能，请更新 README.md


## 贡献类型

### 添加新的 ChatModel

AgentScope 目前内置支持以下 API 提供商：**OpenAI**、**DashScope**、**Gemini**、**Anthropic** 和 **Ollama**。
其中 `OpenAIChatModel` 的实现还兼容不同的服务提供商，如 vLLM，DeepSeek、SGLang 等。

**⚠️ 重要：**

添加新的 ChatModel 不仅涉及模型层面的实现，还涉及到其它组件的配合，具体包括：
- 消息格式化器（formatter）
- Token 计数器（token counter）
- Tools API 集成

这意味着添加一个 ChatModel 需要大量的工作来确保其与 AgentScope 生态系统的其他部分无缝集成。
为了更好地专注于智能体能力开发和维护，**官方开发团队目前不计划添加对新 API 的支持**。
但是当开发者社区有强烈需求时，我们将尽力满足这些需求。

**对于一个 ChatModel 类的实现**，为了与仓库中 `ReActAgent` 兼容，所需要实现的组件如下：

#### 必需组件：

1. **ChatModel**（位于 `agentscope.model` 下）：
   ```python
   from agentscope.model import ChatModelBase


   class YourChatModel(ChatModelBase):
       """
       需要考虑的功能包括：
       - 集成 tools API
       - 支持流式和非流式模式，并与 tools API 兼容
       - 支持 tool_choice 参数
       - 考虑支持推理模型
       """
   ```

2. **格式化器类**（位于 `agentscope.formatter` 下）：
   ```python
   from agentscope.formatter import FormatterBase

   class YourModelFormatter(FormatterBase):
       """
       将 `Msg` 对象转换为对应 API 提供商所需的格式。
       如果模型 API 不支持多智能体场景（例如不支持消息中的 name 字段），需要
       为 chatbot 和多智能体场景分别实现两个格式化器类。
       """
   ```

3. **Token 计数器**（位于 `agentscope.token` 下，推荐）：
   ```python
   from agentscope.token import TokenCounterBase

   class YourTokenCounter(TokenCounterBase):
       """
       为对应模型实现 token 计数逻辑（推荐实现，非严格要求）。
       """
   ```

### 添加新的智能体

为了确保 AgentScope 中所有的功能实现都是**模块化的、可拆卸的和可组合的**，`agentscope.agent` 模块目前仅维护 **`ReActAgent`** 类作为核心实现。

在 AgentScope 中，我们遵循示例优先的开发工作流程：

- 在 `examples/` 目录中初步实现新的功能
- 然后将重要功能抽象和模块化，集成到核心库中
- 修改 `examples/` 目录中的示例以使用新的核心功能

对于专门的或特定领域的智能体，我们建议按照以下组织形式将它们贡献到 **`examples/agent`** 目录：

```
examples/
└── agent/
    ├── main.py
    ├── README.md  # 解释智能体的目的和用法
    └── ... # 其他脚本
```

### 添加新的示例


请将它们添加到 `examples/` 目录，并附上清晰的 README 说明示例的目的和用法。

目前我们的示例根据类型组织到子目录中：
- `examples/functionality/` 用于展示 AgentScope 的特定基础功能
- `examples/evaluation/` 用于评估
- `examples/workflows/` 用于工作流演示
- `examples/training/` 用于训练相关示例

示例结构如下：

```
examples/
└── {example_type}/
    └── {example_name}/
        ├── main.py
        ├── README.md  # 解释示例的目的和用法
        └── ... # 其他脚本
```


## Do's and Don'ts

### ✅ DO

- **从小处着手**：从小的、可管理的贡献开始
- **及早沟通**：在实现主要功能之前进行讨论
- **编写测试**：确保代码经过充分测试
- **添加代码注释**：帮助他人理解贡献内容
- **遵循提交约定**：使用约定式提交消息
- **保持尊重**：遵守我们的行为准则
- **提出问题**：如果不确定某事，请提问！

### ❌ DON'T

- **不要用大型 PR 让我们措手不及**：大型的、意外的 PR 难以审查，并且可能与项目目标不一致。在进行重大更改之前，请务必先开启一个问题进行讨论
- **不要忽略 CI 失败**：修复持续集成标记的任何问题
- **不要混合关注点**：保持 PR 专注于单一功能的实现或修复
- **不要忘记更新测试**：功能的更改应反映在测试中
- **不要破坏现有 API**：在可能的情况下保持向后兼容性，或清楚地记录破坏性更改
- **不要添加不必要的依赖项**：保持核心库轻量级
- **不要绕过懒加载导入原则**：确保 AgentScope 在导入阶段不至于臃肿

## 获取帮助

如果需要帮助或有疑问：

- 💬 开启一个 [Discussion](https://github.com/agentscope-ai/agentscope/discussions)
- 🐛 通过 [Issues](https://github.com/agentscope-ai/agentscope/issues) 报告错误
- 📧 通过钉钉交流群或 Discord 联系开发团队（链接在 README.md 中）


---

感谢为 AgentScope 做出贡献！🚀

