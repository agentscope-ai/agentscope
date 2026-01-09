# AgentScope Instructions

You should conduct a strict code review. Each requirement is labeled with priority:
- **[MUST]** must be satisfied or PR will be rejected
- **[SHOULD]** strongly recommended
- **[MAY]** optional suggestion

## 1. Code Quality

### [MUST] Lazy Loading
- Third-party library dependencies should be imported at the point of use, avoid centralized imports at file top
- Use factory pattern to import base classes:
```python
def get_xxx_cls() -> "MyClass":
    from xxx import BaseClass
    class MyClass(BaseClass): ...
    return MyClass
```

### [SHOULD] Code Conciseness
After understanding the code intent, check if it can be optimized:
- Avoid unnecessary temporary variables
- Merge duplicate code blocks
- Prioritize reusing existing utility functions

### [MUST] Encapsulation Standards
- All Python files under `src/agentscope` should be named with `_` prefix, and exposure controlled through `__init__.py`
- Classes and functions used internally by the framework that don't need to be exposed to users must be named with `_` prefix

## 2. [MUST] Code Security
- Prohibit hardcoding API keys/tokens/passwords
- Use environment variables or configuration files for management
- Check for debug information and temporary credentials
- Check for injection attack risks (SQL/command/code injection, etc.)

## 3. [MUST] Testing & Dependencies
- New features must include unit tests
- New dependencies need to be added to `pyproject.toml`
- Dependencies for non-core scenarios should not be added to the minimal dependency list

## 4. Code Standards

### [MUST] Comment Standards
- **Use English**
- All classes/methods must have complete docstrings, strictly following the template:
```python
def func(a: str, b: int | None = None) -> str:
    """{description}

    Args:
        a (`str`):
            The argument a
        b (`int | None`, optional):
            The argument b

    Returns:
        `str`:
            The return str
    """
```
- Use reStructuredText syntax for special content:
```python
class MyClass:
    """xxx

    `Example link <https://xxx>`_

    .. note:: Example note

    .. tip:: Example tip

    .. important:: Example important info

    .. code-block:: python

        def hello_world():
            print("Hello world!")

    """
```

### [MUST] Pre-commit Checks
- **Strict review**: In most cases, code should be modified rather than skipping checks
- **File-level check skipping is prohibited**
- Only allowed skip: agent class system prompt parameters (to avoid `\n` formatting issues)

---

## 5. Git Standards

### [MUST] PR Title
- Follow Conventional Commits
- Must use prefixes: `feat/fix/docs/ci/refactor/test`, etc.
- Format: `feat(scope): description`
- Example: `feat(memory): add redis cache support`