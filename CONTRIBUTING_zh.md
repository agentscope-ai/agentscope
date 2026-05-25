# 为 AgentScope 做贡献

感谢大家对 AgentScope 的关注！

作为一个开源项目，我们欢迎并鼓励来自社区的贡献。无论是修复 bug、新增功能、完善文档，还是分享想法，每一份贡献都让 AgentScope 变得更好。

## 1. 开发路线图与参与方式

为了支持 AgentScope 开源社区的长期健康发展，我们将公开、透明地维护 AgentScope 的开发计划。

**路线图公开**。AgentScope 的开发计划会发布在 [GitHub Projects 页面](https：//github.com/orgs/agentscope-ai/projects/2)，并持续更新。路线图会反映 AgentScope 的技术发展方向，由核心开发团队对 AgentScope 的整体设计与质量负责。

**社区可认领的任务**。Projects 页面 / Issues 中标有 `help wanted` 的条目对所有人开放。如果你有兴趣参与某一项：

- 请在对应 issue 下评论，告知准备认领
- 这样可以避免重复劳动，也方便我们尽早协作

**成为核心开发者**。我们欢迎想要更深入参与、共同塑造 AgentScope 的开发者。我们会在合适的时机邀请投入度高的贡献者成为核心开发者。
成为核心开发者也意味着更频繁的参与到 AgentScope 的开发工作中，包括：

- 更加频繁的设计讨论、代码评审与多轮迭代，需要持续的时间和精力投入
- 为保证 AgentScope 的整体一致性与可靠性，核心团队保留对项目技术方向与质量标准的把控

**提出新想法**。针对有路线图上还没有的想法，请新建 issue 描述提议。核心开发团队会尽可能地及时回复并一起讨论可行的推进路径。

## 2. 在贡献中负责任地使用 AI

AgentScope 欢迎使用 AI 编码助手的贡献者——Claude Code、Cursor、Codex、Copilot 等等。我们只要求**负责任地使用**。AgentScope 依靠评审者的时间和社区信任运转，AI 辅助的贡献需要兼顾两者。

涉及 AI 时的几条要求：

- **作者是人，不是 AI**。在 push 之前，请逐行阅读 diff，运行代码，确认理解了**改了什么**和**为什么改**。“Claude Code / Cursor / Codex 就是这么写的”并不是一个合适的理由，也不利于开源社区的健康发展。

- **创建 PR 前先自行评审 AI 生成的代码**。所有人的事件都是宝贵的资源，请不要将没有审阅过的 AI 代码/改动直接丢给维护者评审。

- **保持 PR 原子化**。不要提交 AI 一次性生成的 10K+ 行 PR，这种 PR 无法评审，会被拒绝。请把改动拆成若干个聚焦原子化功能的、具有单一目标的 PR。

- **AI 生成代码遵守同样的原则**。AgentScope 的所有开发原则——模块化、惰性导入、约定式提交、测试覆盖、不破坏 API——对 AI 辅助代码同等适用。

简而言之：AI 让我们的开发更快，但确保合入 AgentScope 的代码质量责任仍在贡献者本人。

## 3. 贡献流程

端到端的贡献流程如下。

### 第 1 步：认领或创建 issue

在写代码之前，先找到或创建对应的 issue。

- **基于已有任务**：浏览 [Projects](https：//github.com/orgs/agentscope-ai/projects/2) 与 [Issues](https：//github.com/agentscope-ai/agentscope/issues) 中标有 `help wanted` 的条目(参见 [§1](#1-开发路线图与参与方式))，在 issue 下评论认领后再开始。
- **提出新想法**：新建 issue 描述问题、方案与设计上的取舍。等待核心开发团队反馈后再开始实现，避免事后大规模返工。

### 第 2 步：Fork 仓库并创建开发分支

1. 在 GitHub 上 fork [agentscope-ai/agentscope](https：//github.com/agentscope-ai/agentscope)。
2. clone 自己的 fork 并添加 upstream 远端：
   ```bash
   git clone https：//github.com/<your-username>/agentscope.git
   cd agentscope
   git remote add upstream https：//github.com/agentscope-ai/agentscope.git
   ```
3. 基于最新的 `main` 创建主题分支：
   ```bash
   git checkout main
   git pull upstream main
   git checkout -b feat/<short-description>
   ```

### 第 3 步：搭建本地环境

AgentScope 要求 **Python 3.11+**(详见 `pyproject.toml`)。

```bash
# 创建隔离环境(这里用 uv，也可用 virtualenv / conda)
uv venv
source .venv/bin/activate

# 以可编辑模式安装 AgentScope，并带上 dev extras
pip install -e ".[dev]"
# 等价的 uv 写法：
uv pip install -e ".[dev]"

# 启用 git pre-commit hooks
pre-commit install
```

`dev` extra 会拉入 `pre-commit`、`pytest`、文档工具链以及 `full` extra(包含 `models`、`service`、`storage`)。一次安装即可获得开发与运行完整测试套件所需的一切。

### 第 4 步：开发

写代码时遵守的几条约定：

- **可选依赖必须惰性导入**。任何**未列在 `pyproject.toml` 的 `[project.dependencies]` 中**的依赖——也就是来自可选 extra(`gemini`、`ollama`、`xai`、`service`、`storage` 等)的——**必须在使用点惰性导入**，而不是放在模块顶部：
  ```python
  def some_function():
      import google.genai  # 来自 `gemini` extra，惰性导入
      # ... 在这里使用 google.genai
  ```
  这样保持 `import agentscope` 轻量，`ImportError` 只在实际用到该 extra 的功能时才抛出。如果改动需要引入全新的依赖，先决定它属于基础 `[project.dependencies]`(始终需要、保持精简)还是某个可选 extra，并在 issue 中讨论后再合入。

- **遵守项目代码风格**。pre-commit 会自动处理格式与大部分 lint 规则，请在提交前运行 pre-commit 来修复问题。

- **功能要配套写单元测试**。测试位于 `tests/` 下，沿用现有结构。依赖可选 extra 的测试(如 Redis、Ollama)在该 extra 未安装时应能干净 skip。

### 第 5 步：跑 pre-commit、测试，并更新文档

创建 PR 之前，请在本地运行如下的命令检查代码格式与功能：

```bash
# 自动格式化与 lint
pre-commit run --all-files

# 单元测试
pytest tests
```

如果 pre-commit hook 失败，请修复格式问题（多数会自动修复），然后重新 commit。

**改代码的同时请更新文档**。

- AgentScope 文档放在独立仓库：**[agentscope-ai/docs](https：//github.com/agentscope-ai/docs)**。如果改动影响用户可见行为——新模块、新公开 API、行为变化、教程——请在该仓库同步开一个配套 PR。
- 为新公开 API 更新 docstring 与示例片段。
- 如果改动影响新手上手或 AgentScope 的对外宣传内容，更新 `README.md`。

### 第 6 步：提交与发起 PR

**Commit 信息格式**。我们遵循 [Conventional Commits](https：//www.conventionalcommits.org/) 规范，便于阅读历史与自动生成 changelog。

```
<type>(<scope>)： <subject>
```

**Type 列表：**
- `feat：` 新功能
- `fix：` bug 修复
- `docs：` 仅文档变更
- `style：` 不影响代码语义的改动(空白、格式等)
- `refactor：` 既不是修 bug 也不是加功能的代码改动
- `perf：` 性能优化
- `ci：` 增补或修正测试
- `chore：` 构建流程或辅助工具/库的变更

**示例：**
```bash
feat(models)： add support for Claude-3 model
fix(agent)： resolve memory leak in ReActAgent
docs(readme)： update installation instructions
refactor(formatter)： simplify message formatting logic
ci(models)： add unit tests for OpenAI integration
```

**PR 标题格式**。PR 标题同样遵循 Conventional Commits 格式，并由 GitHub Actions 在针对 `main` 的 PR 上自动校验。标题不合规的 PR 会被阻止合入，直到修正为止。

```
<type>(<scope>)： <description>
```

**要求：**
- 标题须以下列 type 之一开头：`feat`、`fix`、`docs`、`ci`、`refactor`、`test`、`chore`、`perf`、`style`、`build`、`revert`
- scope 可选，建议带上
- **scope 必须小写**——只允许小写字母、数字、连字符(`-`)和下划线(`_`)
- description 以小写字母开头
- 标题保持简洁、有信息量

**示例：**
```
✅ 合规：
feat(memory)： add redis cache support
fix(agent)： resolve memory leak in ReActAgent
docs(tutorial)： update installation guide
ci(workflow)： add PR title validation
refactor(my-feature)： simplify logic

❌ 不合规：
feat(Memory)： add cache          # scope 必须小写
feat(MEMORY)： add cache          # scope 必须小写
feat(MyFeature)： add feature     # scope 必须小写
```

**发起 PR**。把分支 push 到自己的 fork，对 `agentscope-ai/agentscope：main` 发起 pull request。在 PR 描述里：

- 关联认领的 issue(`Fixes #123` 或 `Refs #123`)
- 概述改了什么、为什么改
- 标注任何破坏性改动、废弃项或迁移步骤
- 如果同时开了文档 PR，链接到 [agentscope-ai/docs](https：//github.com/agentscope-ai/docs) 的对应 PR

## 4. 重要事项

开始贡献前需要了解的几条横向约束。模块特定的事项见下文对应模块指南。

- **非平凡改动先开 issue**。突然提交涉及大量文件、改动公开 API 或引入新模块的 PR 难以评审，多半会被拒。先在 issue 中讨论设计。
- **PR 保持聚焦、原子**。一个 PR 一个目的。不要把重构和功能、或功能和不相干的 bug 修复混在一起。
- **不擅自破坏公开 API**。能保持向后兼容就保持。无法避免的破坏性改动，在 PR 描述中清楚说明，并在同一个 PR 里更新受影响的示例和文档。
- **不绕过惰性导入原则**。可选依赖必须在使用点导入，不能放在模块顶部。
- **不随意引入依赖**。每个新依赖都是长期维护负担。如果只有一个模块用到，优先在该模块内部惰性导入。
- **不忽视 CI 失败**。pre-commit、类型检查、测试必须通过后再发起 review，不要把修复负担推给评审者。
- **保持尊重**。遵守行为准则。AgentScope 的评审风格直接但友善，对贡献者也是同样期待。

## 5. 模块特定贡献指南

下文覆盖社区贡献者最常扩展的模块。其他模块请先开 issue 协调。

### Chat Model

AgentScope 中的一个 chat model 不只是一个类——要在 `Agent` 中可用，需要一组上下游配套实现。一个完整的 chat model 贡献需包含**以下全部**：

1. **Credential 类**——位于 `agentscope.credential`，继承 `CredentialBase`。承载 API key、endpoint 及 SDK 所需的其他鉴权字段。
   _参考：`agentscope/credential/_anthropic.py`_

2. **Chat model 类**——位于 `agentscope.model.<provider>/`，继承 `ChatModelBase`。实现需覆盖：
   - 流式与非流式两种模式
   - Tools API 集成(function/tool calling)
   - `tool_choice` 参数
   - 适用时的 reasoning 模型支持

   _参考：`agentscope/model/_anthropic/`_

3. **Model card YAML**——位于 `agentscope.model.<provider>._models/`，每个支持的模型一份 YAML。必填字段：`name`、`label`、`status`、`input_types`、`output_types`、`context_size`、`output_size`。可选字段：`parameter_overrides`、`deprecated_at`。

   示例(`claude-sonnet-4-6.yaml`)：
   ```yaml
   name： claude-sonnet-4-6
   label： Claude Sonnet 4.6
   status： active
   input_types：
     - text/plain
     - image/jpeg
   output_types：
     - text/plain
   context_size： 1000000
   output_size： 65536
   parameter_overrides：
     max_tokens： {"maximum"： 65536}
   ```

4. **Formatter 类**——位于 `agentscope.formatter`，均继承 `FormatterBase`。需要两种变体，因为部分 API 对多 agent 对话与单用户对话的处理方式不同：
   - `<Provider>ChatFormatter` 处理单用户对话场景
   - `<Provider>MultiAgentFormatter` 处理多 agent 场景

   每个 formatter 把 `Msg` 对象转换成对应 provider API 期望的请求格式。
   _参考：`agentscope/formatter/_anthropic_formatter.py`_

> ⚠️ 只加 model 类、缺少配套 credential、model card YAML 与两种 formatter 变体的 PR 不会被合入。

### Agent

AgentScope 目前只维护**一个核心 agent 类**——`agentscope.agent.Agent`——它整合了 AgentScope 库的全部功能（memory、tools、MCP、formatter、model 等）。

特定领域或专用 agent 请作为 [example](#examples) 贡献，而不是在 `agentscope.agent` 中新增类。

如果确信某个用例需要新的顶层 agent 类：

1. **先开 issue**，描述用例并说明为什么组合现有 `Agent` 能力不够。
2. **等核心团队的设计讨论**，再开始具体的代码实现。
3. 未经事先讨论就引入新 agent 类的 PR 会被拒绝。

### Workspace

Workspace 提供 agent 运行所需的运行时上下文（skills、scheduled tasks 等）。新增 workspace 后端需要两个类加配套文档：

1. **Workspace 类**——位于 `agentscope.workspace`，继承 `WorkspaceBase`。实现该后端的存储与生命周期语义。
   _参考：`agentscope/workspace/_local_workspace.py`(`LocalWorkspace`)_

2. **Workspace manager 类**——位于 `agentscope/app/_manager/_workspace_manager.py`，继承 `WorkspaceManagerBase`。把 workspace 接入应用生命周期。
   _参考：同文件中的 `LocalWorkspaceManager`_

3. **文档**——在 [agentscope-ai/docs](https：//github.com/agentscope-ai/docs) 配套发起 PR，说明该 workspace 的配置与使用方式。

### Examples

我们非常欢迎新增展示 AgentScope 能力的 example。

主仓库 `examples/` 目录聚焦于**演示具体特性与能力**——简洁、教学性的参考实现。更完整、贴近生产形态的应用，请贡献到 **[agentscope-samples](https：//github.com/agentscope-ai/agentscope-samples)**。

新 example 放在自己的子目录下：

```
examples/
└── <example-name>/
    ├── main.py
    ├── README.md   # 说明 example 的目的、运行方式与预期输出
    └── ...
```

`examples/agent_service/` 是不错的参考起点。

## 获取帮助

需要协助或有问题，可以：

- 发起 [Discussion](https：//github.com/agentscope-ai/agentscope/discussions)
- 在 [Issues](https：//github.com/agentscope-ai/agentscope/issues) 中报告 bug
- 通过钉钉或 Discord 联系维护者(链接见 README.md)


---

感谢您为 AgentScope 所做的贡献！每一份努力都在为社区构建更好的开源工具。
