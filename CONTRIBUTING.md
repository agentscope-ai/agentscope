# Contributing to AgentScope


Thank you for your interest in contributing to AgentScope!

As an open-source project, we warmly welcome and encourage
contributions from the community. Whether you're fixing bugs, adding new features, improving documentation, or sharing
ideas, your contributions help make AgentScope better for everyone.

## 1. Development Roadmap and How to Get Involved

To support the long-term, healthy growth of AgentScope and its open-source
community, we keep our development plan transparent and openly tracked.

**Our roadmap is public.** The AgentScope development plan is published and
continuously updated on our [GitHub Projects page](https://github.com/orgs/agentscope-ai/projects/2).
The roadmap reflects the technical direction set by the core team, who are
responsible for AgentScope's overall design and quality.

**Tasks open to the community.** Items labeled `help wanted` on the Projects
page/issues are contribution opportunities open to everyone. If one of these
interests you:

- Comment on the related issue to let us know you'd like to take it on
- This helps us avoid duplicate efforts and coordinate with you early

**If you'd like to join the core development.** We warmly welcome contributors
who want to go deeper and help shape AgentScope itself. Over time, we plan to
gradually invite committed contributors into the core development circle.
Before reaching out, we'd like to share a few honest expectations so you can
decide whether it's a good fit right now:

- Core development involves frequent design discussions, code reviews, and
  iterative revisions — it asks for a sustained investment of time and energy
- To keep AgentScope cohesive and reliable, the core team retains
  responsibility for the project's technical direction and quality bar; core
  contributors work within this collaborative process

If this fits your situation, please reach out to the core developers — we'd
love to talk.

**Proposing something new.** If you have an idea that isn't on the roadmap
yet, please open a new issue describing your proposal. The core team will
respond and discuss it with you so we can find the best path forward together.

## 2. Responsible Use of AI in Contributions

AgentScope welcomes contributors who use AI coding assistants — Claude Code,
Cursor, Codex, Copilot, and others. We just ask that they be used
**responsibly**. AgentScope is sustained by reviewer time and community
trust, and AI-assisted contributions need to honor both.

A few expectations when AI is involved in your work:

- **You — not the AI — are the author.** Read the diff line by line, run it,
  and make sure you understand *what* changed and *why* before you push.
  "Claude Code / Cursor / Codex told me to do it" is not an acceptable
  answer in code review, and is not the kind of behavior that builds a
  healthy open-source community. PRs whose authors cannot explain their own
  changes will be closed.

- **Review your AI-generated code before opening a PR.** Reviewer time is
  the most precious resource in this project. Don't outsource your own
  review to the maintainers by dumping unreviewed AI output into a PR.

- **Keep PRs atomic.** Do not submit a 10K+-line PR produced by an AI in a
  single shot. Such PRs are unreviewable and will be rejected. Break the
  work into focused, single-purpose PRs the same way a human contributor
  would.

- **AI-assisted code follows the same rules.** All of AgentScope's
  development principles — modularity, lazy imports, conventional commits,
  test coverage, no surprise API breaks — apply identically to code written
  with AI assistance. AI is not an excuse for skipping conventions.

The goal is simple: AI helps you move faster, but the responsibility for
what lands in AgentScope still rests with you as a human contributor.

## 3. Contribution Workflow

End-to-end, contributing a change to AgentScope looks like this.

### Step 1. Claim or create an issue

Before writing code, find or open the issue that frames your work.

- **Working on an existing item?** Browse [Projects](https://github.com/orgs/agentscope-ai/projects/2)
  and [Issues](https://github.com/agentscope-ai/agentscope/issues) for items
  labeled `help wanted` (see [§1](#1-development-roadmap-and-how-to-get-involved)).
  Comment on the issue to claim it before starting.
- **Proposing something new?** Open a new issue describing the problem,
  your proposed solution, and any design alternatives. Wait for feedback
  from the core team before starting a non-trivial implementation — this
  avoids wasted rewrites.

### Step 2. Fork the repo and create a development branch

1. Fork [agentscope-ai/agentscope](https://github.com/agentscope-ai/agentscope) on GitHub.
2. Clone your fork and add the upstream remote:
   ```bash
   git clone https://github.com/<your-username>/agentscope.git
   cd agentscope
   git remote add upstream https://github.com/agentscope-ai/agentscope.git
   ```
3. Create a topic branch off the latest `main`:
   ```bash
   git checkout main
   git pull upstream main
   git checkout -b feat/<short-description>
   ```
   Use a branch name aligned with the change type, e.g., `feat/redis-memory`,
   `fix/react-agent-leak`, `docs/contributing-update`.

### Step 3. Set up your local environment

AgentScope requires **Python 3.11+** (see `pyproject.toml`).

```bash
# Create an isolated environment (uv shown; virtualenv / conda also fine)
uv venv
source .venv/bin/activate

# Install AgentScope in editable mode with the dev extras
pip install -e ".[dev]"
# or, equivalently, with uv:
uv pip install -e ".[dev]"

# Enable the git pre-commit hooks
pre-commit install
```

The `dev` extra pulls in `pre-commit`, `pytest`, the documentation
toolchain, and the `full` extra (which itself includes `models`, `service`,
and `storage`). A single installation gives you everything needed to develop
and run the complete test suite.

### Step 4. Develop

A few conventions to follow while writing code:

- **Lazy imports for optional dependencies.** Any dependency **not listed in
  `[project.dependencies]` of `pyproject.toml`** — i.e., anything coming
  from the optional groups (`gemini`, `ollama`, `xai`, `service`, `storage`,
  etc.) — **must be lazy-imported** at point of use rather than at module
  top level:
  ```python
  def some_function():
      import google.genai  # from the `gemini` extra — lazy-imported
      # ... use google.genai here
  ```
  This keeps `import agentscope` lightweight, and `ImportError` surfaces
  only when a feature actually relying on the extra is invoked. If your
  change requires a brand-new dependency, decide first whether it belongs
  in the base `[project.dependencies]` (always required, kept small) or in
  one of the optional extras — and discuss it in the issue before merging.

- **Follow the project's code style.** Pre-commit handles formatting and
  most lint rules automatically. Don't fight the formatter.

- **Write unit tests alongside features.** Tests live under `tests/` and
  follow the existing structure. Tests that rely on an optional extra
  (e.g., Redis, Ollama) should skip cleanly when that extra isn't
  installed.

### Step 5. Run pre-commit, tests, and update documentation

Before opening the PR, run the same checks CI will run:

```bash
# Auto-format and lint
pre-commit run --all-files

# Run the unit tests
pytest tests
```

If a pre-commit hook fails, fix the issue (most fixes are applied
automatically) and re-stage the files. Don't bypass hooks with
`--no-verify`.

**Update documentation alongside the code change.**

- AgentScope's user-facing documentation lives in a separate repository:
  **[agentscope-ai/docs](https://github.com/agentscope-ai/docs)**. If your
  change affects user-facing behavior — new modules, new public APIs,
  behavior changes, tutorials — please open a companion PR there.
- Update inline docstrings and example snippets for any new public APIs.
- Update `README.md` if your change affects how users get started or what
  AgentScope advertises.

### Step 6. Commit and open a pull request

**Commit message format.** We follow the [Conventional Commits](https://www.conventionalcommits.org/)
specification. This keeps commit history readable and enables automatic
changelog generation.

```
<type>(<scope>): <subject>
```

**Types:**
- `feat:` A new feature
- `fix:` A bug fix
- `docs:` Documentation only changes
- `style:` Changes that do not affect the meaning of the code (whitespace, formatting, etc.)
- `refactor:` A code change that neither fixes a bug nor adds a feature
- `perf:` A code change that improves performance
- `ci:` Adding missing tests or correcting existing tests
- `chore:` Changes to the build process or auxiliary tools and libraries

**Examples:**
```bash
feat(models): add support for Claude-3 model
fix(agent): resolve memory leak in ReActAgent
docs(readme): update installation instructions
refactor(formatter): simplify message formatting logic
ci(models): add unit tests for OpenAI integration
```

**Pull request title format.** PR titles follow the same Conventional
Commits format and are validated automatically by GitHub Actions on PRs
against `main`. PRs with invalid titles will be blocked until corrected.

```
<type>(<scope>): <description>
```

**Requirements:**
- Title must start with one of: `feat`, `fix`, `docs`, `ci`, `refactor`, `test`, `chore`, `perf`, `style`, `build`, `revert`
- Scope is optional but recommended
- **Scope must be lowercase** — only lowercase letters, numbers, hyphens (`-`), and underscores (`_`) are allowed
- Description should start with a lowercase letter
- Keep the title concise and descriptive

**Examples:**
```
✅ Valid:
feat(memory): add redis cache support
fix(agent): resolve memory leak in ReActAgent
docs(tutorial): update installation guide
ci(workflow): add PR title validation
refactor(my-feature): simplify logic

❌ Invalid:
feat(Memory): add cache          # Scope must be lowercase
feat(MEMORY): add cache          # Scope must be lowercase
feat(MyFeature): add feature     # Scope must be lowercase
```

**Open the PR.** Push your branch to your fork and open a pull request
against `agentscope-ai/agentscope:main`. In the PR description:

- Link the issue you claimed (`Fixes #123` or `Refs #123`)
- Summarize what changed and why
- Note any breaking changes, deprecations, or migration steps
- Link the companion docs PR in [agentscope-ai/docs](https://github.com/agentscope-ai/docs)
  if you opened one

## 4. Important Notices

A few cross-cutting constraints worth knowing before you start a
contribution. Module-specific notices live in the corresponding module
guide below.

- **Open an issue before non-trivial work.** Surprise PRs that touch many
  files, change public APIs, or introduce a new module are difficult to
  review and likely to be rejected. Discuss the design in an issue first.
- **Keep PRs focused and atomic.** One PR, one purpose. Don't bundle a
  refactor with a feature, or a feature with an unrelated bug fix.
- **Don't break public APIs without notice.** Maintain backward
  compatibility when you can. If a breaking change is unavoidable, call it
  out clearly in the PR description and update the affected examples and
  docs in the same PR.
- **Don't bypass the lazy import principle.** Optional dependencies must be
  imported at point of use, not at module top level.
- **Don't add dependencies casually.** Every new dependency is a long-term
  maintenance commitment. If a dependency is needed by only one module,
  prefer a lazy import inside that module.
- **Don't ignore CI failures.** Pre-commit, type checks, and tests must
  pass before a PR is ready for review. Don't push the burden of fixing
  them onto the reviewer.
- **Be respectful.** Follow our Code of Conduct. AgentScope's review
  culture is direct but kind, and we expect the same from contributors.

## 5. Module-Specific Contribution Guides

The notes below cover the modules most commonly extended by community
contributors. For other modules, please open an issue first so we can
coordinate.

### Chat Model

A chat model in AgentScope is more than a single class — to be usable
inside an `Agent`, it needs a small set of upstream/downstream pieces.
A complete chat-model contribution includes **all** of the following:

1. **Credential class** — under `agentscope.credential`, subclassing
   `CredentialBase`. Carries the API key, endpoint, and other auth fields
   your SDK needs.
   _Reference: `agentscope/credential/_anthropic.py`_

2. **Chat model class** — under `agentscope.model.<provider>/`, subclassing
   `ChatModelBase`. The implementation needs to cover:
   - Both streaming and non-streaming modes
   - Tools API integration (function/tool calling)
   - The `tool_choice` argument
   - Reasoning models, where applicable

   _Reference: `agentscope/model/_anthropic/`_

3. **Model card YAML(s)** — under
   `agentscope.model.<provider>._models/`, one YAML per supported model.
   Required fields: `name`, `label`, `status`, `input_types`,
   `output_types`, `context_size`, `output_size`. Optional:
   `parameter_overrides`, `deprecated_at`.

   Example (`claude-sonnet-4-6.yaml`):
   ```yaml
   name: claude-sonnet-4-6
   label: Claude Sonnet 4.6
   status: active
   input_types:
     - text/plain
     - image/jpeg
   output_types:
     - text/plain
   context_size: 1000000
   output_size: 65536
   parameter_overrides:
     max_tokens: {"maximum": 65536}
   ```

4. **Formatter classes** — under `agentscope.formatter`, both subclassing
   `FormatterBase`. Two variants are required because some APIs treat
   multi-agent conversations differently from single-user chat:
   - `<Provider>ChatFormatter` for single-user chat scenarios
   - `<Provider>MultiAgentFormatter` for multi-agent scenarios

   Each formatter converts `Msg` objects into the request format the
   provider's API expects.
   _Reference: `agentscope/formatter/_anthropic_formatter.py`_

> ⚠️ PRs that add only the model class without the matching credential,
> model card YAML, and both formatter variants will not be merged.

### Agent

AgentScope deliberately maintains a **single core agent class** —
`agentscope.agent.Agent` — that integrates all functionality of the
AgentScope library (memory, tools, MCP, formatters, models, etc.).

For specialized or domain-specific agents, please contribute them as
[examples](#examples) rather than as new classes in `agentscope.agent`.

If you believe a use case genuinely requires a new top-level agent class:

1. **Open an issue first** describing the use case and explaining why
   composing existing `Agent` capabilities is insufficient.
2. **Wait for design discussion** with the core team before starting any
   implementation.
3. PRs that introduce a new agent class without prior discussion will be
   rejected.

### Workspace

A Workspace provides the runtime context an agent operates in (skills,
scheduled tasks, etc.). Adding a new workspace backend requires two
classes plus documentation:

1. **Workspace class** — under `agentscope.workspace`, subclassing
   `WorkspaceBase`. Implements the storage and lifecycle semantics of
   your backend.
   _Reference: `agentscope/workspace/_local_workspace.py` (`LocalWorkspace`)_

2. **Workspace manager class** — alongside
   `agentscope/app/_manager/_workspace_manager.py`, subclassing
   `WorkspaceManagerBase`. Wires your workspace into the application
   lifecycle.
   _Reference: `LocalWorkspaceManager` in the same file._

3. **Documentation** — open a companion PR in
   [agentscope-ai/docs](https://github.com/agentscope-ai/docs) describing
   how to configure and use your workspace.

### Examples

We highly encourage contributions of new examples that showcase
AgentScope's capabilities.

The `examples/` directory in the main repository focuses on
**demonstrating specific features and capabilities** — concise,
educational reference implementations. For more complete, production-style
applications, please contribute them to
**[agentscope-samples](https://github.com/agentscope-ai/agentscope-samples)**
instead.

A new example should live in its own subdirectory:

```
examples/
└── <example-name>/
    ├── main.py
    ├── README.md   # explain the example's purpose, how to run it, and expected output
    └── ...
```

`examples/agent_service/` is a good starting reference.

## Getting Help

If you need assistance or have questions:

- Open a [Discussion](https://github.com/agentscope-ai/agentscope/discussions)
- Report bugs via [Issues](https://github.com/agentscope-ai/agentscope/issues)
- Contact the maintainers at DingTalk or Discord (links in the README.md)


---

Thank you for contributing to AgentScope! Your efforts help build a better tool for the entire community.
