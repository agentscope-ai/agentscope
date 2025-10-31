# SearchSubAgent Architectural Correction Plan

## Problem Statement
Current SearchSubAgent implementation violates fundamental SubAgent pattern by mixing tool orchestration logic with agent responsibilities. It implements a tool wrapper disguised as an agent, creating unnecessary complexity and architectural debt.

## Linus Torvalds Style: Direct Fix Strategy

**DELETE → MOVE → REBUILD**

### 1. DELETE Flawed Implementation
**What to remove:**
- `examples/agent_search_subagent/search_subagent.py` - Entire file (architecturally wrong)
- `examples/agent_search_subagent/tools.py` - Business logic belongs in tools layer
- All tests that validate wrong architecture

**Why delete:** Current code is fundamentally wrong - tool wrapper masquerading as agent. Keep it and we maintain technical debt.

### 2. MOVE Logic to Proper Layer
**What to create:**
- `src/agentscope/tool/_search/intelligent_search.py` - Intelligent routing as tool
- Provider selection, fallback, result aggregation logic
- Clear tool function signature with `{query: str}` contract

**Why move:** Business logic belongs in tools layer, not agent layer. SubAgent should be minimal wrapper.

### 3. REBUILD Proper SubAgent
**What to create:**
- Minimal `examples/agent_search_subagent/search_subagent.py` (< 100 lines)
- Simple `reply()` that calls intelligent search tool via toolkit
- No routing logic, no orchestration, just delegation

**Why rebuild:** SubAgent pattern requires agent-as-tool, not tool-as-agent.

## Execution Steps

1. **Delete flawed files**
   ```bash
   rm examples/agent_search_subagent/search_subagent.py
   rm examples/agent_search_subagent/tools.py
   ```

2. **Create intelligent search tool**
   ```python
   # src/agentscope/tool/_search/intelligent_search.py
   async def search_intelligent(query: str) -> ToolResponse:
       # Provider selection, fallback, aggregation
   ```

3. **Create minimal subagent**
   ```python
   # examples/agent_search_subagent/search_subagent.py
   async def reply(self, input_obj: SearchSubAgentInput) -> Msg:
       response = await self.toolkit.call_tool_function({
           'name': 'search_intelligent',
           'arguments': {'query': input_obj.query}
       })
       return Msg(content=response.content)
   ```

4. **Update tests** - Validate tool delegation, not routing logic

5. **Update docs** - Remove architectural anti-patterns

## Acceptance Criteria

- ✅ SearchSubAgent < 100 lines, pure delegation
- ✅ All intelligent logic in tools layer
- ✅ Zero Ruff warnings
- ✅ Tests pass
- ✅ No architectural violations

## Risk Assessment

**Risk:** None - current code must be removed anyway
**Mitigation:** Direct replacement with correct pattern

## Timeline

**1 day:** Delete wrong code, create correct implementation, update tests, update docs.

---

This plan eliminates technical debt rather than accumulating it.