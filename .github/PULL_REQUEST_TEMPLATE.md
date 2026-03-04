## PR Title Format

Please ensure your PR title follows the Conventional Commits format:
- Format: `<type>(<scope>): <description>`
- Example: `feat(memory): add redis cache support`
- Allowed types: `feat`, `fix`, `docs`, `ci`, `refactor`, `test`, `chore`, `perf`, `style`, `build`, `revert`
- Description should start with a lowercase letter

## AgentScope Version

[The version of AgentScope you are working on, e.g. `import agentscope; print(agentscope.__version__)`]

## Description

[Please describe the background, purpose, changes made, and how to test this PR]

## Checklist

Please check the following items before code is ready to be reviewed.

- [ ]  Code has been formatted with `pre-commit run --all-files` command
- [ ]  All tests are passing
- [ ]  Docstrings are in Google style
- [ ]  Related documentation has been updated (e.g. links, examples, etc.)
- [ ]  Code is ready for review