# Troubleshooting and Common Pitfalls

<!-- Navigation Metadata -->
<!-- Example: Troubleshooting | Level: Problem Solving | Prerequisites: process/README.md -->
<!-- Related: prompting/best-practices.md, execution/troubleshooting.md, case-studies.md -->

**📍 You are here:** [Main Guide](../../README.md) → [Examples](README.md) → **Troubleshooting & Pitfalls**

## Quick Navigation
- **📋 Learn Process:** [Process Guide](../process/README.md) - Avoid pitfalls with systematic approach
- **💬 Better Communication:** [Prompting Best Practices](../prompting/best-practices.md) - Prevent misunderstandings
- **⚡ Implementation Issues:** [Execution Troubleshooting](../execution/troubleshooting.md) - Fix coding problems
- **📖 Real Examples:** [Case Studies](case-studies.md) - Learn from actual failures

---

A comprehensive guide to avoiding common mistakes in spec-driven development and recovering when things go wrong.

## Common Pitfalls by Phase

### Requirements Phase Pitfalls

#### 1. Vague or Ambiguous Requirements

**The Problem:**
```markdown
# BAD EXAMPLE
- User should be able to manage their data
- System should be fast and reliable
- Interface should be user-friendly
```

**Why It Fails:**
- No measurable criteria
- Subjective terms without definition
- Missing specific user actions

**The Solution:**
```markdown
# GOOD EXAMPLE
**User Story:** As a registered user, I want to edit my profile information, so that I can keep my account details current.

#### Acceptance Criteria
1. WHEN a user clicks "Edit Profile" THEN the system SHALL display an editable form with current profile data
2. WHEN a user submits valid profile changes THEN the system SHALL save the changes within 2 seconds
3. WHEN a user enters invalid data THEN the system SHALL display specific error messages within the form
```

**Recovery Strategy:**
- Review each requirement and ask "How would I test this?"
- Convert subjective terms to measurable criteria
- Add specific user actions and system responses

#### 2. Requirements Scope Creep During Initial Phase

**The Problem:**
Starting with "simple user login" and ending up with "complete user management system with roles, permissions, audit logging, and social authentication."

**Why It Fails:**
- Loses focus on core functionality
- Makes design phase overwhelming
- Creates unrealistic implementation timeline

**The Solution:**
- Define a clear boundary for the current spec
- Document "future enhancements" separately
- Use the "could/should/must" prioritization framework

**Recovery Strategy:**
```markdown
## Current Spec Scope (MUST HAVE)
- Basic email/password authentication
- User session management
- Password reset functionality

## Future Enhancements (COULD HAVE)
- Social login integration
- Role-based permissions
- Audit logging
```

#### 3. Missing Error and Edge Cases

**The Problem:**
Only documenting the "happy path" scenarios.

**Common Missing Cases:**
- Network failures
- Invalid input handling
- Concurrent user actions
- System resource limitations

**The Solution:**
For each requirement, explicitly consider:
- What happens when this fails?
- What are the boundary conditions?
- How should the system behave under stress?

### Design Phase Pitfalls

#### 1. Over-Engineering the Initial Design

**The Problem:**
```markdown
# BAD EXAMPLE - Too Complex for Initial Implementation
## Architecture
- Microservices with event sourcing
- CQRS pattern implementation
- Distributed caching layer
- Message queue system
- API gateway with rate limiting
```

**Why It Fails:**
- Adds unnecessary complexity
- Makes implementation tasks overwhelming
- Increases chance of implementation failure

**The Solution:**
```markdown
# GOOD EXAMPLE - Appropriate for Requirements
## Architecture
- Single service with clear module separation
- Direct database access with connection pooling
- RESTful API endpoints
- Simple authentication middleware
```

**Recovery Strategy:**
- Review each design decision against actual requirements
- Ask "What's the simplest solution that meets the requirements?"
- Document complex features as "future architectural evolution"

#### 2. Insufficient Technical Research

**The Problem:**
Making design decisions without understanding:
- Available libraries and frameworks
- Performance characteristics
- Integration requirements
- Deployment constraints

**Warning Signs:**
- Design assumes capabilities that don't exist
- No consideration of technical limitations
- Missing integration details

**The Solution:**
- Research key technical decisions during design phase
- Validate assumptions with proof-of-concept code
- Document technical constraints and their impact

#### 3. Design-Implementation Gap

**The Problem:**
Creating designs that are theoretically sound but practically difficult to implement.

**Common Issues:**
- Complex data relationships without clear implementation path
- Assumed libraries or services that don't exist
- Performance requirements without implementation strategy

**Recovery Strategy:**
- Review design with implementation feasibility in mind
- Break complex components into simpler, implementable pieces
- Add implementation notes for complex design decisions

### Tasks Phase Pitfalls

#### 1. Tasks Too Large or Vague

**The Problem:**
```markdown
# BAD EXAMPLE
- [ ] Implement user authentication system
- [ ] Create database layer
- [ ] Build API endpoints
```

**Why It Fails:**
- No clear completion criteria
- Too much work for single task
- Unclear dependencies

**The Solution:**
```markdown
# GOOD EXAMPLE
- [ ] 1.1 Create User model with validation
  - Implement User class with email, password fields
  - Add email format validation
  - Add password strength requirements
  - Write unit tests for User model validation
  - _Requirements: 1.2, 2.1_

- [ ] 1.2 Implement password hashing utilities
  - Create password hashing function using bcrypt
  - Create password verification function
  - Write unit tests for password utilities
  - _Requirements: 1.2, 3.1_
```

**Recovery Strategy:**
- Break large tasks into 2-4 hour implementation chunks
- Add specific deliverables and test criteria
- Ensure each task has clear completion definition

#### 2. Missing Task Dependencies

**The Problem:**
Tasks that can't be implemented because prerequisite work isn't complete.

**Example:**
```markdown
- [ ] 2.1 Implement user login endpoint
- [ ] 2.2 Add authentication middleware
- [ ] 1.1 Create User model  # Should come first!
```

**The Solution:**
- Review task sequence for logical dependencies
- Ensure foundational components are implemented first
- Use task numbering that reflects implementation order

#### 3. No Integration or End-to-End Tasks

**The Problem:**
All tasks focus on individual components without connecting them together.

**Missing Elements:**
- Integration between components
- End-to-end workflow testing
- System-level validation

**The Solution:**
Always include integration tasks:
```markdown
- [ ] 5.1 Integrate authentication with API endpoints
- [ ] 5.2 Create end-to-end user registration flow
- [ ] 5.3 Test complete login/logout workflow
```

## Process-Level Pitfalls

### 1. Skipping User Approval Between Phases

**The Problem:**
Moving from Requirements → Design → Tasks without user validation at each step.

**Why It Fails:**
- Compounds errors across phases
- User discovers issues too late to fix efficiently
- Implementation doesn't match user expectations

**Recovery Strategy:**
- Always get explicit approval before moving to next phase
- If issues are discovered later, return to the appropriate phase
- Don't try to fix fundamental issues during implementation

### 2. Treating Specs as Immutable

**The Problem:**
Refusing to update requirements or design when implementation reveals issues.

**Better Approach:**
- Specs are living documents that can be updated
- Implementation insights should inform spec improvements
- Document changes and rationale for future reference

### 3. Perfectionism Paralysis

**The Problem:**
Spending too much time perfecting requirements or design instead of moving forward.

**Warning Signs:**
- Multiple revisions without significant improvement
- Analysis paralysis on minor decisions
- Avoiding implementation phase

**Recovery Strategy:**
- Set time limits for each phase
- Aim for "good enough" rather than perfect
- Remember that implementation will reveal areas for improvement

## Recovery Strategies

### When Requirements Are Fundamentally Flawed

**Symptoms:**
- Design phase reveals major gaps
- Requirements conflict with each other
- User feedback indicates misunderstanding

**Recovery Steps:**
1. Stop current phase work
2. Return to requirements with specific issues identified
3. Focus revision on problem areas only
4. Get explicit approval before proceeding

### When Design Doesn't Support Requirements

**Symptoms:**
- Tasks phase reveals implementation impossibility
- Design complexity far exceeds requirement complexity
- Missing critical system components

**Recovery Steps:**
1. Identify specific design-requirement mismatches
2. Revise design to address gaps
3. Simplify over-engineered components
4. Validate revised design against all requirements

### When Tasks Are Unimplementable

**Symptoms:**
- Tasks require non-existent capabilities
- Task dependencies are circular or unclear
- Individual tasks are too large or vague

**Recovery Steps:**
1. Review tasks against design and requirements
2. Break down large tasks into implementable chunks
3. Reorder tasks to respect dependencies
4. Add missing integration and testing tasks

## Prevention Strategies

### Requirements Phase Prevention
- Use EARS format consistently
- Include error cases and edge conditions
- Get specific examples for each requirement
- Validate requirements with potential users

### Design Phase Prevention
- Research technical decisions during design
- Keep initial design simple and extensible
- Document assumptions and constraints
- Validate design against requirements frequently

### Tasks Phase Prevention
- Ensure each task is 2-4 hours of work
- Include testing and integration tasks
- Sequence tasks by dependency order
- Reference specific requirements for each task

## Warning Signs to Watch For

### Early Warning Signs
- Difficulty explaining requirements to others
- Design decisions made without research
- Tasks that seem overwhelming or unclear
- Resistance to moving between phases

### Critical Warning Signs
- Multiple failed attempts at same phase
- Growing complexity without added value
- Implementation consistently failing
- User confusion about spec content

## When to Start Over

Sometimes the best recovery strategy is to restart with lessons learned:

**Consider Restarting When:**
- Fundamental misunderstanding of user needs
- Technical approach is completely wrong
- Spec has become too complex to follow
- More time spent on fixes than forward progress

**How to Restart Effectively:**
1. Document lessons learned from failed attempt
2. Identify the root cause of failure
3. Start with simplified scope
4. Apply prevention strategies from the beginning

---

[← Back to Examples](README.md) | [View Case Studies →](case-studies.md)