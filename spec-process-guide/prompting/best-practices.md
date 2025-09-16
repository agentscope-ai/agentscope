# Prompting Best Practices

<!-- Navigation Metadata -->
<!-- Prompting: Best Practices | Level: Practical Guide | Prerequisites: prompting/strategies.md -->
<!-- Related: ai-reasoning/decision-frameworks.md, examples/troubleshooting-pitfalls.md, templates/README.md -->

**📍 You are here:** [Main Guide](../../README.md) → [Prompting Strategies](README.md) → **Best Practices**

## Quick Navigation
- **📚 Learn Strategies:** [Prompting Strategies](strategies.md) - Core approaches first
- **📝 Use Templates:** [Prompt Templates](templates.md) - Ready-to-use patterns
- **🧠 Understand AI:** [Decision Frameworks](../ai-reasoning/decision-frameworks.md) - How AI makes choices
- **🔧 Fix Problems:** [Troubleshooting Guide](../examples/troubleshooting-pitfalls.md) - When prompting goes wrong

---

Effective techniques for AI collaboration during spec creation, including troubleshooting guidance and examples of successful interactions.

## Core Principles

### 1. Context is King

**Provide Rich Context**
- Always include relevant background about your project, technology stack, and constraints
- Reference previous discussions and decisions to maintain continuity
- Explain the "why" behind your requirements, not just the "what"

**Example - Good Context Setting:**
```
I'm working on a React e-commerce application that currently handles 10k daily users. 
We use TypeScript, Node.js backend with PostgreSQL, and deploy on AWS. 
I need to add a product recommendation feature that integrates with our existing 
user behavior tracking system and should handle our expected 50% traffic growth.
```

**Example - Poor Context:**
```
I need a recommendation system.
```

### 2. Be Specific and Concrete

**Use Concrete Examples**
- Provide specific scenarios rather than abstract descriptions
- Include actual data examples when discussing data models
- Reference real user workflows and business processes

**Example - Specific Request:**
```
For the user authentication system, I need to handle these specific scenarios:
1. New user registration with email verification
2. Social login via Google and GitHub OAuth
3. Password reset with secure token expiration (24 hours)
4. Account lockout after 5 failed attempts with 30-minute cooldown
5. Integration with our existing user profile system that stores preferences
```

### 3. Structure Complex Requests

**Break Down Large Asks**
- Divide complex features into logical phases
- Prioritize core functionality over nice-to-have features
- Sequence requests to build understanding progressively

**Example - Well-Structured Request:**
```
I want to create a comprehensive spec for a file upload system. Let's start with:

Phase 1: Core upload functionality
- Single file upload with progress tracking
- File type validation (images, documents)
- Size limits (10MB max)

Phase 2: Enhanced features (we'll tackle after Phase 1 is solid)
- Multiple file upload
- Drag-and-drop interface
- Cloud storage integration
```

## Phase-Specific Best Practices

### Requirements Phase

**Do:**
- Start with user problems, not technical solutions
- Use the "As a [role], I want [goal], so that [benefit]" format consistently
- Include both happy path and error scenarios
- Specify measurable acceptance criteria

**Don't:**
- Jump into implementation details
- Assume the AI knows your business context
- Create requirements that are too broad or vague
- Skip edge cases and error handling

**Successful Interaction Example:**
```
User: "I need user authentication for my app."

Better approach: "I'm building a SaaS application for small businesses. 
I need user authentication that supports:
- Business owners who need to manage team access
- Team members with different permission levels
- Integration with existing customer data
- Compliance with SOC 2 requirements

The main user story is: As a business owner, I want to control who can 
access our company data, so that I can maintain security and compliance."
```

### Design Phase

**Do:**
- Reference specific requirements when making design decisions
- Explain trade-offs between different approaches
- Consider scalability and maintainability from the start
- Include error handling and edge cases in the design

**Don't:**
- Design in isolation from requirements
- Over-engineer for hypothetical future needs
- Ignore existing system constraints
- Skip non-functional requirements

**Successful Interaction Example:**
```
User: "Based on our authentication requirements, I need a design that 
handles the multi-tenant access control we discussed. Our current system 
uses JWT tokens, and we have about 500 businesses with an average of 
8 team members each. Performance is critical - login should be under 200ms.

Please design an approach that:
1. Leverages our existing JWT infrastructure
2. Scales to our current user base
3. Supports the role-based permissions from requirement 2.3
4. Integrates with our PostgreSQL user database"
```

### Tasks Phase

**Do:**
- Request tasks that build incrementally
- Specify testing requirements for each task
- Ask for tasks that can be completed independently
- Include integration and deployment considerations

**Don't:**
- Create tasks that are too large or complex
- Skip testing and validation steps
- Ignore dependencies between tasks
- Forget about documentation and cleanup

**Successful Interaction Example:**
```
User: "Please break down the authentication design into coding tasks. 
I want to follow TDD principles and be able to deploy incrementally. 
Each task should be completable in 2-4 hours and include its own tests.

Priority is getting basic login/logout working first, then adding 
the role-based permissions. I'm using Jest for testing and have 
CI/CD set up with GitHub Actions."
```

## Communication Techniques

### Iterative Refinement

**Build on Previous Responses**
```
"The requirements look good overall. I'd like to refine requirement 2.1 
to be more specific about the error handling. Instead of 'system should 
handle errors gracefully', let's specify exactly what happens when 
authentication fails, network is unavailable, and tokens expire."
```

**Validate Understanding**
```
"Before we move to design, let me confirm my understanding:
- We're prioritizing security over convenience
- Integration with existing systems is mandatory, not optional  
- Performance requirements are firm (sub-200ms login)
- We need to support both web and mobile clients
Is this correct?"
```

### Feedback Integration

**Specific Change Requests**
```
"I need these specific changes to the design:
1. Replace Redis caching with in-memory caching to reduce infrastructure complexity
2. Add rate limiting to prevent brute force attacks (requirement 1.4)
3. Include session management for the mobile app use case
4. Specify the database schema changes needed for role storage"
```

**Explain the Reasoning**
```
"I want to change the authentication approach from OAuth to JWT because:
- Our team has more experience with JWT implementation
- It reduces external dependencies (no OAuth provider needed)
- Better fits our offline-capable mobile app requirement
- Simpler to test and debug in our current setup"
```

## Troubleshooting Common Issues

### When Responses Are Too Generic

**Problem:** AI provides high-level, generic advice instead of specific guidance.

**Solution:** Add more context and constraints.

**Before:**
```
"How should I structure my database for user management?"
```

**After:**
```
"I have a PostgreSQL database for a multi-tenant SaaS app with these constraints:
- 500 businesses, average 8 users each
- Need to track user roles, permissions, and activity
- Current users table has id, email, created_at
- Must maintain backward compatibility with existing auth system
- Performance target: user lookup under 50ms

How should I extend the schema to support role-based access control?"
```

### When Requirements Keep Changing

**Problem:** Scope creep during requirements phase.

**Solution:** Establish clear boundaries and priorities.

**Approach:**
```
"Let's establish the MVP scope first. For version 1, we MUST have:
- [Core requirement 1]
- [Core requirement 2]
- [Core requirement 3]

Nice-to-have features for future versions:
- [Enhancement 1]
- [Enhancement 2]

Please focus the requirements only on the MVP scope."
```

### When Design Becomes Too Complex

**Problem:** Design tries to solve every possible future need.

**Solution:** Refocus on current requirements and constraints.

**Approach:**
```
"The design is getting complex. Let's simplify by focusing only on 
requirements 1.1-1.4 for now. We can extend it later for requirements 
2.x. Please revise the design to:
- Handle current user load (not 10x future growth)
- Use our existing tech stack (don't introduce new technologies)
- Solve the specific problems in our requirements (not general problems)"
```

### When Tasks Are Too Abstract

**Problem:** Implementation tasks are too high-level for actual coding.

**Solution:** Request specific, actionable coding steps.

**Before:**
```
"- Implement user authentication system"
```

**After:**
```
"Please break down 'Implement user authentication system' into specific coding tasks like:
- Create User model with email, password_hash, role fields
- Write password hashing utility functions with bcrypt
- Implement login endpoint that validates credentials and returns JWT
- Create middleware to verify JWT tokens on protected routes
- Write unit tests for each component"
```

## Quality Validation Techniques

### Requirements Validation

**Completeness Check:**
```
"Please review these requirements and identify any gaps:
- Are all user types covered?
- Do we handle all error scenarios?
- Are integration points specified?
- Are performance requirements measurable?
- Is the scope clearly bounded?"
```

**EARS Format Validation:**
```
"Please check if these acceptance criteria follow EARS format properly:
- Do they start with WHEN, IF, WHERE, WHILE?
- Is the system response clearly specified with SHALL?
- Are conditions and triggers specific and testable?"
```

### Design Validation

**Architecture Review:**
```
"Please validate this design against our requirements:
- Does it address all functional requirements?
- Are non-functional requirements (performance, security) handled?
- Are interfaces between components well-defined?
- Is error handling comprehensive?
- Can this be tested effectively?"
```

**Technical Feasibility:**
```
"Given our constraints (team size, timeline, existing systems), 
is this design realistic? Are there any parts that seem 
over-engineered or under-specified?"
```

### Task Validation

**Actionability Check:**
```
"Are these tasks specific enough for a developer to implement?
- Do they specify exact files/components to create?
- Are dependencies between tasks clear?
- Can each task be completed and tested independently?
- Do they build incrementally toward the full feature?"
```

## Advanced Techniques

### Research Integration

**When You Need Technical Research:**
```
"Before we finalize the design, I need to research authentication 
best practices for multi-tenant SaaS applications. Please help me 
identify the key areas to research:
- Industry standards for role-based access control
- Common security vulnerabilities and mitigations
- Performance optimization techniques for JWT at scale
- Integration patterns with existing systems"
```

### Constraint Management

**Handling Conflicting Requirements:**
```
"We have conflicting requirements: users want single sign-on (req 1.3) 
but we also need offline capability (req 2.1). Please help me:
1. Analyze the trade-offs between these approaches
2. Suggest compromise solutions
3. Recommend which requirement should take priority and why"
```

### Stakeholder Alignment

**Multi-Perspective Validation:**
```
"Please review these requirements from different stakeholder perspectives:
- Developer: Are they technically feasible with our stack?
- User: Do they solve real user problems effectively?
- Business: Do they align with our business goals and constraints?
- Security: Are there any security concerns or gaps?"
```

---

[← Templates](templates.md) | [Back to Prompting Guide](README.md)