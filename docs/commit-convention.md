# Git Commit 信息规范

本规范基于 [Conventional Commits](https://www.conventionalcommits.org/)，并结合 AgentScope 项目实际情况细化。所有 commit message 与 PR 标题均须遵循本规范。

## 1. 整体结构

```
<type>(<scope>): <subject> (#<issue/pr-number>)

<body>

<footer>
```

- **第一行（标题行）**：必填，描述本次改动的核心内容
- **body**：可选，说明改动动机与影响
- **footer**：可选，用于关联 issue、声明破坏性变更等

## 2. 标题行规则

### 2.1 type（必填）

| type | 说明 | 适用范围 |
| --- | --- | --- |
| `feat` | 新功能 | agent / model / formatter / tool / app / rag / mcp / workspace / storage / permission / tts / embedding |
| `fix` | bug 修复 | 同上 |
| `docs` | 仅文档变更 | docs / examples / README / CONTRIBUTING |
| `style` | 不影响代码语义的改动（空白、格式、分号等） | 任意模块 |
| `refactor` | 既不是修 bug 也不是加功能的代码重构 | agent / model / formatter / tool / app / rag / mcp / workspace / storage / permission / tts / embedding |
| `perf` | 性能优化 | agent / model / tool / app / storage / rag |
| `test` | 增补或修正测试 | tests / 各模块对应测试文件 |
| `ci` | CI 配置或脚本变更 | .github / workflows / scripts / pre-commit 配置 |
| `build` | 构建系统或外部依赖变更 | pyproject.toml / setup 相关 |
| `chore` | 其他不改动源码的杂项（发布流程、辅助工具等） | 任意 |
| `revert` | 回滚之前的提交 | 任意 |

### 2.2 scope（可选，建议填写）

取值为受影响的模块名，与 `src/agentscope/` 下的目录对齐，例如：

`agent`、`model`、`formatter`、`tool`、`message`、`app`、`rag`、`mcp`、`workspace`、`storage`、`permission`、`tts`、`embedding`、`examples`、`tests`、`deps`

跨多个模块时可省略 scope，或使用更高层级的概括（如 `core`）。

**scope 必须小写**——只允许小写字母、数字、连字符（`-`）和下划线（`_`）。

### 2.3 subject（必填）

- 使用中文或英文均可，描述本次改动的核心内容
- 长度不超过 **50 个字符**
- 描述“做了什么”，而非“怎么做的”

### 2.4 issue/PR 编号（可选）

若改动关联某个 issue 或 PR，在 subject 末尾以 `(#123)` 形式标注：

```
fix(agent): resolve memory leak in ReActAgent (#245)
```

## 3. body（可选）

当标题行不足以说明改动时编写，规则：

- 与标题行之间空一行
- 说明 **为什么改（why）** 和 **改了什么（what）**，不必重复 diff 能看到的细节
- 每行不超过 **72 个字符**，可分多个段落

示例：

```
之前的 formatter 在处理包含多模态内容的消息时，
会丢弃 tool-call 参数，导致下游模型报错。

本次修改在内容扁平化过程中保留了 tool-call 块，
并为混合内容场景添加了回归测试。
```

## 4. footer（可选）

- **关联 issue**：使用 `Closes #123`、`Fixes #456`、`Refs #789`，一条一行
- **破坏性变更**：以 `BREAKING CHANGE: ` 开头，描述不兼容点及迁移方式；也可在 type/scope 后加 `!` 标记，如 `feat(api)!: rename session id field`

示例：

```
Closes #123
Refs #456

破坏性变更：`Session.uid` 已重命名为 `Session.id`。
调用方需要相应更新持久化代码。
```

## 5. 完整示例

### 5.1 新功能

```
feat(models): 为 Gemini TTS 添加流式支持 (#312)

Gemini TTS 之前会缓冲完整音频后再返回，
导致长文本的延迟明显。本次引入可配置块大小的分块流式传输。

Closes #300
Refs #287
```

### 5.2 Bug 修复

```
fix(agent): 修复 ReActAgent 中的内存泄漏 (#245)

ReActAgent 在消息历史中持有已完成工具结果的引用，
阻止了垃圾回收。本次修改在每次推理步骤后清理中间结果。

Fixes #240
```

### 5.3 破坏性变更

```
feat(api)!: 重命名会话 ID 字段

破坏性变更：`Session.uid` 已重命名为 `Session.id`。
调用方需要相应更新持久化代码。

Closes #180
```

### 5.4 文档变更

```
docs(readme): 更新安装说明

添加基于 uv 的安装命令，并阐明 PyPI 安装与源码安装的区别。
```

### 5.5 测试与 CI

```
test(models): 添加 OpenAI 集成的单元测试

添加覆盖流式传输、工具调用和错误处理的测试，
针对 OpenAI 聊天模型适配器。
```

```
ci(workflow): 添加 PR 标题校验

添加 GitHub Actions 工作流，校验 PR 标题是否符合
Conventional Commits 格式。
```

## 6. 校验要求

- PR 标题同样遵循本格式，由 GitHub Actions 在针对 `main` 的 PR 上自动校验，不合规的 PR 将被阻止合入
- commit 前运行 `pre-commit run --all-files` 与 `pytest tests` 确保通过
- **scope 必须小写**，description 以小写字母开头
- 标题保持简洁、有信息量

### 合规示例

```
✅ 合规：
feat(memory): 添加 redis 缓存支持
fix(agent): 修复 ReActAgent 中的内存泄漏
docs(tutorial): 更新安装指南
ci(workflow): 添加 PR 标题校验
refactor(my-feature): 简化逻辑

❌ 不合规：
feat(Memory): 添加缓存          # scope 必须小写
feat(MEMORY): 添加缓存          # scope 必须小写
feat(MyFeature): 添加功能       # scope 必须小写
```
